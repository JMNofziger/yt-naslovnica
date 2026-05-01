import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from google import genai
from google.cloud import firestore
from google.cloud.firestore import Increment
from google.genai import types

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__)

# README / architecture docs linked from header "About".
GITHUB_REPO_URL = "https://github.com/JMNofziger/yt-naslovnica"


@app.context_processor
def inject_repo_url():
    return {"github_repo_url": GITHUB_REPO_URL}


PROJECT_ID = (
    os.environ.get("GOOGLE_CLOUD_PROJECT")
    or os.environ.get("GCP_PROJECT")
    or "summarizer-lab"
)
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "europe-west1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

COLLECTION = "feed_items"
FEED_DAYS = int(os.environ.get("FEED_DAYS", "30"))
MAX_VIDEOS_PER_RUN = int(os.environ.get("MAX_VIDEOS_PER_RUN", "20"))
INGEST_LOOKBACK_DAYS = int(os.environ.get("INGEST_LOOKBACK_DAYS", "30"))

# Used when YOUTUBE_CHANNEL_IDS is unset: @handle URLs resolved via Data API (forHandle).
DEFAULT_CHANNEL_SOURCES = (
    # Expats / English-language Croatia life & bureaucracy
    "https://www.youtube.com/@PaulBradbury/videos",
    "https://www.youtube.com/@livingincroatia/videos",
    "https://www.youtube.com/@expatlifeincroatia/videos",
    "https://www.youtube.com/@ExpatinCroatia/videos",
    # News / national broadcaster / Zagreb tourism / regions (all Croatia-scoped)
    "https://www.youtube.com/@TotalCroatiaNews/videos",
    "https://www.youtube.com/@Hrvatskaradiotelevizija_HRT/videos",
    "https://www.youtube.com/@vecernjiTV/videos",
    "https://www.youtube.com/@RTLdan/videos",
    "https://www.youtube.com/@RTLTelevizija/videos",
    "https://www.youtube.com/@TelegrafVIDEO/videos",
    "https://www.youtube.com/@TelegramVIDEO/videos",
    "https://www.youtube.com/@Hrvatskadanas/videos",
    "https://www.youtube.com/@ZagrebNews/videos",
    "https://www.youtube.com/@OtvoreniRadio/videos",
    "https://www.youtube.com/@N1hr/videos",
    "https://www.youtube.com/@Hrvatskapolitika/videos",
    "https://www.youtube.com/@dubrovniktimes/videos",
    "https://www.youtube.com/@VisitZagreb/videos",
    "https://www.youtube.com/@CroatiaFullOfLife/videos",
    "https://www.youtube.com/@ZagrebExplorer/videos",
    "https://www.youtube.com/@CroatiaUncovered/videos",
    "https://www.youtube.com/@KorculaExplorer/videos",
    "https://www.youtube.com/@SplitLiving/videos",
)

_CHANNEL_ID_UC = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")
_HANDLE_IN_URL = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/@([^/?#]+)", re.I
)

# Remove hashtag spam / repeated tags from titles (always applied on display + ingest fallback).
_TITLE_HASHTAG_RUN = re.compile(
    r"(?:[#＃][\w\u0100-\u024f\u0400-\u04FF.-]+\s*)+", re.UNICODE
)
_TITLE_MULTI_SPACE = re.compile(r"\s{2,}")

_fs_client = None


def get_fs():
    global _fs_client
    if _fs_client is None:
        _fs_client = firestore.Client(project=PROJECT_ID)
    return _fs_client


def utcnow():
    return datetime.now(timezone.utc)


def firestore_missing_ui_context(exc: BaseException, project_id: str) -> dict:
    """
    Detect Firestore/Datastore 'database does not exist' errors for friendlier UI/API hints.
    """
    raw = str(exc)
    lower = raw.lower()
    if "does not exist" not in lower:
        return {}
    if not any(
        k in lower for k in ("database", "firestore", "datastore", "cloud datastore")
    ):
        return {}
    pid = quote(project_id, safe="")
    return {
        "firestore_missing": True,
        "firestore_setup_url": f"https://console.cloud.google.com/datastore/setup?project={pid}",
        "firestore_console_url": f"https://console.cloud.google.com/firestore/databases?project={pid}",
        "gcp_project_id": project_id,
    }


def prefetch_firestore_or_raise():
    """Cheap read so missing-database errors surface early on ingest."""
    next(get_fs().collection(COLLECTION).limit(1).stream(), None)


client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=VERTEX_LOCATION,
)


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def channel_sources_from_env_or_defaults() -> list[str]:
    raw = os.environ.get("YOUTUBE_CHANNEL_IDS", "").strip()
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    return list(DEFAULT_CHANNEL_SOURCES)


def youtube_channel_id_for_handle(api_key: str, handle: str) -> str | None:
    """Resolve @handle via channels.list(forHandle)."""
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {"part": "id", "forHandle": handle.strip(), "key": api_key}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    items = data.get("items") or []
    if not items:
        logger.warning("No channel returned for forHandle=%s", handle)
        return None
    return items[0]["id"]


def youtube_channel_id_from_reference(api_key: str, reference: str) -> str | None:
    """
    Accepts a channel id (UC…), a URL containing /@handle/, or a bare @handle/handle string.
    """
    reference = reference.strip()
    if not reference:
        return None
    if _CHANNEL_ID_UC.match(reference):
        return reference
    m = _HANDLE_IN_URL.search(reference)
    handle = None
    if m:
        handle = m.group(1)
    elif reference.startswith("@"):
        handle = reference[1:].strip()
    elif "youtube.com" not in reference and "/" not in reference:
        handle = reference.lstrip("@").strip()
    if not handle:
        logger.warning("Unrecognized channel reference: %s", reference)
        return None
    return youtube_channel_id_for_handle(api_key, handle)


def _unique_channel_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in ids:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def generate_summary(youtube_link: str, extra: str = " ") -> str:
    youtube_video = types.Part.from_uri(file_uri=youtube_link, mime_type="video/*")
    if not extra.strip():
        extra = " "
    contents = [
        youtube_video,
        types.Part.from_text(text="Provide a concise summary of what is discussed in this video."),
        extra,
    ]
    cfg = types.GenerateContentConfig(
        temperature=1,
        top_p=0.95,
        max_output_tokens=8192,
        response_modalities=["TEXT"],
    )
    return client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=cfg,
    ).text


def _parse_summary_language_blob(raw: str | None) -> tuple[str, str]:
    """Parse Gemini JSON {\"summary\",\"primary_language\"}; returns (summary, iso639-1 or '')."""
    if not raw:
        return "", ""
    text = raw.strip()
    for fence in ("```json", "```JSON", "```"):
        if fence in text:
            try:
                text = text.split(fence, 1)[1]
                text = text.split("```", 1)[0].strip()
            except IndexError:
                pass
            break
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            summary = (data.get("summary") or "").strip()
            lang = data.get("primary_language") or data.get("language") or ""
            if isinstance(lang, str):
                lang = normalize_spoken_language_code(lang)
            else:
                lang = ""
            return summary, lang
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return "", ""


def normalize_spoken_language_code(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if len(s) >= 2 and s[:2].isalpha():
        return s[:2]
    return ""


def _regional_flag_emoji(alpha2_country: str) -> str:
    """Build a Unicode regional-flag pair from ISO 3166-1 alpha-2 (e.g. HR → 🇭🇷)."""
    a = (alpha2_country or "").strip().upper()
    if len(a) != 2 or not (a.isalpha() and a.isascii()):
        return ""
    return chr(ord(a[0]) - ord("A") + 0x1F1E6) + chr(ord(a[1]) - ord("A") + 0x1F1E6)


# ISO 639-1 → representative territory for flag emoji (languages ≠ countries; heuristic).
_LANGUAGE_TO_TERRITORY = {
    "en": "US",
    "hr": "HR",
    "sr": "RS",
    "bs": "BA",
    "sl": "SI",
    "de": "DE",
    "it": "IT",
    "fr": "FR",
    "es": "ES",
    "pl": "PL",
    "uk": "UA",
    "nl": "NL",
    "sq": "AL",
    "mk": "MK",
    "hu": "HU",
    "ro": "RO",
    "cs": "CZ",
    "sk": "SK",
    "bg": "BG",
    "el": "GR",
    "tr": "TR",
    "pt": "PT",
    "ru": "RU",
    "sv": "SE",
    "da": "DK",
    "fi": "FI",
    "no": "NO",
    "nb": "NO",
    "nn": "NO",
}


def spoken_language_ui(code: str) -> dict[str, str]:
    c = normalize_spoken_language_code(code)
    if not c:
        return {"language_abbr": "—", "language_flag_emoji": "🌐"}
    territory = _LANGUAGE_TO_TERRITORY.get(c)
    emoji = _regional_flag_emoji(territory) if territory else ""
    if not emoji:
        emoji = "🌐"
    return {"language_abbr": c.upper(), "language_flag_emoji": emoji}


def ingest_request_authorized(expected_secret: str) -> bool:
    if request.headers.get("X-Ingest-Secret", "").strip() == expected_secret:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() == expected_secret
    return False


def generate_summary_and_spoken_language(youtube_link: str, extra: str = " ") -> tuple[str, str]:
    youtube_video = types.Part.from_uri(file_uri=youtube_link, mime_type="video/*")
    if not extra.strip():
        extra = " "
    instructions = (
        "Listen to the spoken audio and respond with VALID JSON ONLY (no markdown, no prose outside JSON).\n"
        'Schema: {"summary": "<concise plain-text summary of topics discussed>", '
        '"primary_language": "<ISO 639-1 two-letter lowercase code for the main spoken language, e.g. en, hr>"}\n'
        "Rules: summary is one or two short paragraphs of plain text. primary_language must be exactly two letters."
    )
    contents = [
        youtube_video,
        types.Part.from_text(text=instructions),
        extra,
    ]
    cfg = types.GenerateContentConfig(
        temperature=0.8,
        top_p=0.95,
        max_output_tokens=8192,
        response_modalities=["TEXT"],
    )
    blob = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=cfg,
    ).text
    summary, lang = _parse_summary_language_blob(blob)
    if not summary.strip():
        logger.warning(
            "summary+language JSON parse failed or empty summary; retrying summary-only fallback"
        )
        summary = generate_summary(youtube_link, extra).strip()
    return summary, lang


def sanitize_plain_card_title(raw: str | None, max_len: int = 160) -> str:
    """Strip hashtags / noisy spacing for safe card display."""
    s = (raw or "").strip()
    if not s:
        return "(untitled)"
    s = _TITLE_HASHTAG_RUN.sub(" ", s)
    s = _TITLE_MULTI_SPACE.sub(" ", s).strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s or "(untitled)"


def concise_english_card_title(raw_title: str) -> str:
    """
    Short English headline for cards via Gemini (text-only). Falls back to sanitized raw title.
    """
    raw_title = (raw_title or "").strip()
    if not raw_title:
        return "(untitled)"

    cfg = types.GenerateContentConfig(
        temperature=0.2,
        top_p=0.9,
        max_output_tokens=160,
        response_modalities=["TEXT"],
    )
    prompt = (
        "Turn this YouTube video title into a concise English headline for a news card.\n"
        "Hard rules:\n"
        "- Output ONLY the headline as plain text, one line. No quotes or punctuation wrappers.\n"
        "- Aim for ~60–90 characters; shorter is fine.\n"
        "- Remove ALL hashtags (#tags), emoji, clickbait filler ('must watch', excessive punctuation).\n"
        "- Translate fully into natural English if the title is not English.\n\n"
        "Original title:\n"
        f"{raw_title}"
    )
    contents = [types.Part.from_text(text=prompt)]
    try:
        out = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=cfg,
        ).text
        line = (out or "").strip().splitlines()[0].strip().strip("\"'")
        head = sanitize_plain_card_title(line, max_len=160)
        if len(head) < 4:
            raise ValueError("title too short")
        return head
    except Exception as exc:
        logger.warning("English card title generation failed, using fallback: %s", exc)
        return sanitize_plain_card_title(raw_title)


def parse_youtube_published_at(raw: str) -> datetime:
    """RFC3339 from YouTube Data API."""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def relative_label(dt: datetime | None) -> str:
    if dt is None:
        return ""
    now = utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} hr ago"
    if secs < 86400 * 7:
        return f"{secs // 86400} days ago"
    return dt.strftime("%b %d, %Y")


def normalize_published(val) -> datetime:
    if val is None:
        return utcnow()
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if hasattr(val, "timestamp"):
        return datetime.fromtimestamp(val.timestamp(), tz=timezone.utc)
    return utcnow()


def load_items_for_feed(*, active: bool):
    db = get_fs()
    cutoff = utcnow() - timedelta(days=FEED_DAYS)
    coll = db.collection(COLLECTION)
    if active:
        q = (
            coll.where("published_at", ">=", cutoff)
            .order_by("published_at", direction=firestore.Query.DESCENDING)
            .limit(200)
        )
    else:
        q = (
            coll.where("published_at", "<", cutoff)
            .order_by("published_at", direction=firestore.Query.DESCENDING)
            .limit(500)
        )
    items = []
    for snap in q.stream():
        d = snap.to_dict() or {}
        pid = snap.id
        pub = normalize_published(d.get("published_at"))
        up = int(d.get("upvotes") or 0)
        down = int(d.get("downvotes") or 0)
        stored = d.get("title")
        raw_yt = d.get("title_raw")
        card_title = sanitize_plain_card_title(stored or raw_yt or "(untitled)")
        lang_code = normalize_spoken_language_code(d.get("primary_language"))
        lang_ui = spoken_language_ui(lang_code)
        items.append(
            {
                "id": pid,
                "title": card_title,
                "url": d.get("url") or youtube_watch_url(pid),
                "channel": d.get("channel") or "",
                "summary": d.get("summary") or "",
                "published_at": pub,
                "published_label": relative_label(pub),
                "upvotes": up,
                "downvotes": down,
                "score": up - down,
                "language_abbr": lang_ui["language_abbr"],
                "language_flag_emoji": lang_ui["language_flag_emoji"],
            }
        )
    items.sort(key=lambda row: (-int(row["upvotes"]), -(row["published_at"].timestamp())))
    return items


@app.route("/", methods=["GET"])
def index():
    try:
        items = load_items_for_feed(active=True)
    except Exception as e:
        logger.exception("feed query failed")
        ui = firestore_missing_ui_context(e, PROJECT_ID)
        return render_template(
            "index.html",
            items=[],
            page_title="Feed",
            error=None if ui else str(e),
            active_feed=True,
            active_archive=False,
            **ui,
        )
    return render_template(
        "index.html",
        items=items,
        page_title="Feed",
        error=None,
        active_feed=True,
        active_archive=False,
    )


@app.route("/archive", methods=["GET"])
def archive():
    try:
        items = load_items_for_feed(active=False)
    except Exception as e:
        logger.exception("archive query failed")
        ui = firestore_missing_ui_context(e, PROJECT_ID)
        return render_template(
            "archive.html",
            items=[],
            page_title="Archive",
            error=None if ui else str(e),
            active_feed=False,
            active_archive=True,
            **ui,
        )
    return render_template(
        "archive.html",
        items=items,
        page_title="Archive",
        error=None,
        active_feed=False,
        active_archive=True,
    )


@app.post("/vote")
def vote():
    video_id = request.form.get("video_id", "").strip()
    direction = request.form.get("direction", "").strip()
    if not video_id or direction not in ("up", "down"):
        abort(400)
    field = "upvotes" if direction == "up" else "downvotes"
    ref = get_fs().collection(COLLECTION).document(video_id)
    ref.update({field: Increment(1)})
    nxt = request.form.get("next")
    if isinstance(nxt, str) and nxt.startswith("/"):
        pass
    else:
        nxt = url_for("index")
    return redirect(nxt)


def youtube_search_channel(api_key: str, channel_id: str, published_after_iso: str):
    videos = []
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": 50,
        "order": "date",
        "type": "video",
        "publishedAfter": published_after_iso,
        "key": api_key,
    }
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    for it in data.get("items") or []:
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if vid:
            videos.append(
                {
                    "video_id": vid,
                    "title": sn.get("title") or "",
                    "channel_title": sn.get("channelTitle") or "",
                    "published_at": sn.get("publishedAt") or "",
                }
            )
    return videos


def feed_has_any_items() -> bool:
    """True if at least one feed_items document exists."""
    db = get_fs()
    snap = next(db.collection(COLLECTION).limit(1).stream(), None)
    return snap is not None


def perform_ingestion() -> tuple[dict, int]:
    """
    Run one ingestion pass (resolve channels, search, summarize new videos).
    Returns (payload dict, HTTP status code).
    """
    api_key = os.environ.get("YOUTUBE_API_KEY")
    channel_refs = channel_sources_from_env_or_defaults()

    if not api_key or not channel_refs:
        return (
            {
                "ok": True,
                "skipped": True,
                "message": "Set YOUTUBE_API_KEY to enable ingestion (channel URLs/ids come from "
                "YOUTUBE_CHANNEL_IDS or built-in placeholders).",
                "processed": 0,
            },
            200,
        )

    try:
        prefetch_firestore_or_raise()
    except Exception as e:
        ui = firestore_missing_ui_context(e, PROJECT_ID)
        if ui:
            return (
                {
                    "ok": False,
                    "error": "Firestore database is not provisioned for this GCP project.",
                    "firestore_setup_url": ui["firestore_setup_url"],
                    "gcp_project_id": PROJECT_ID,
                },
                503,
            )
        raise

    resolved_ids: list[str] = []
    resolve_warnings: list[dict[str, str]] = []
    for ref in channel_refs:
        cid = youtube_channel_id_from_reference(api_key, ref)
        if cid:
            resolved_ids.append(cid)
        else:
            resolve_warnings.append({"ref": ref, "error": "could not resolve channel id"})

    resolved_ids = _unique_channel_ids(resolved_ids)

    if not resolved_ids:
        return (
            {
                "ok": False,
                "error": "No channel IDs resolved; check refs and API key quotas.",
                "resolve_warnings": resolve_warnings,
                "processed": 0,
            },
            422,
        )

    lookback = utcnow() - timedelta(days=INGEST_LOOKBACK_DAYS)
    published_after_iso = lookback.strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    for cid in resolved_ids:
        try:
            batch = youtube_search_channel(api_key, cid, published_after_iso)
            candidates.extend(batch)
        except Exception as e:
            logger.exception("youtube search failed for channel %s", cid)
            return ({"ok": False, "error": str(e)}, 500)

    # Dedupe by video id across channels
    _by_vid = {}
    for row in candidates:
        vid = row["video_id"]
        if vid not in _by_vid:
            _by_vid[vid] = row
    candidates = list(_by_vid.values())
    candidates.sort(key=lambda x: x.get("published_at") or "", reverse=True)

    db = get_fs()
    processed = 0
    errors = []

    for row in candidates[:MAX_VIDEOS_PER_RUN]:
        vid = row["video_id"]
        ref = db.collection(COLLECTION).document(vid)
        if ref.get().exists:
            continue
        watch = youtube_watch_url(vid)
        raw_title = row.get("title") or ""
        card_headline = concise_english_card_title(raw_title)
        try:
            summary, spoken_lang = generate_summary_and_spoken_language(watch, " ")
        except Exception as e:
            logger.exception("gemini failed for %s", vid)
            errors.append({"video_id": vid, "error": str(e)})
            continue
        try:
            pub_dt = parse_youtube_published_at(row["published_at"])
        except Exception:
            pub_dt = utcnow()
        doc = {
            "title_raw": raw_title,
            "title": card_headline,
            "url": watch,
            "channel": row["channel_title"],
            "published_at": pub_dt,
            "summary": summary,
            "upvotes": 0,
            "downvotes": 0,
        }
        if spoken_lang:
            doc["primary_language"] = spoken_lang
        ref.set(doc)
        processed += 1

    return (
        {
            "ok": True,
            "processed": processed,
            "candidates": len(candidates),
            "channels_resolved": len(resolved_ids),
            "resolve_warnings": resolve_warnings,
            "errors": errors[:10],
        },
        200,
    )


def schedule_initial_ingest_if_configured() -> None:
    """
    INITIAL_INGEST_ON_STARTUP: background ingest once when the process starts (Dockerfile uses python app.py).
    By default runs only if feed_items has no documents yet.
    FORCE_INGEST_ON_STARTUP: also run when feed already has items (still skips videos already in Firestore).
    """
    boot = os.environ.get("INITIAL_INGEST_ON_STARTUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    force = os.environ.get("FORCE_INGEST_ON_STARTUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not boot and not force:
        return

    def runner() -> None:
        try:
            if feed_has_any_items() and not force:
                logger.info(
                    "INITIAL_INGEST_ON_STARTUP: skip — feed_items already has documents "
                    "(set FORCE_INGEST_ON_STARTUP=true to run anyway)."
                )
                return
            if not os.environ.get("YOUTUBE_API_KEY"):
                logger.warning(
                    "INITIAL_INGEST_ON_STARTUP set but YOUTUBE_API_KEY missing; skipping bootstrap."
                )
                return
            payload, code = perform_ingestion()
            logger.info(
                "INITIAL_INGEST_ON_STARTUP finished: http_status=%s payload=%s",
                code,
                payload,
            )
        except Exception:
            logger.exception("INITIAL_INGEST_ON_STARTUP failed")

    threading.Thread(target=runner, daemon=True).start()


@app.post("/tasks/ingest")
def tasks_ingest():
    secret = (os.environ.get("INGEST_SECRET") or "").strip()
    if not secret:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "INGEST_SECRET must be set on the service to enable HTTP ingest. "
                    "Send the same value in header X-Ingest-Secret or Authorization: Bearer <secret>. "
                    "In-process startup ingest (INITIAL_INGEST_ON_STARTUP) does not use this route.",
                }
            ),
            503,
        )
    if not ingest_request_authorized(secret):
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    payload, status = perform_ingestion()
    return jsonify(payload), status


@app.route("/summarize", methods=["GET", "POST"])
def summarize():
    if request.method == "POST":
        youtube_link = request.form.get("youtube_link", "")
        extra = request.form.get("additional_prompt", " ")
        try:
            summary = generate_summary(youtube_link, extra or " ")
            return summary
        except Exception as e:
            return str(e), 500
    return redirect(url_for("index"))


if __name__ == "__main__":
    schedule_initial_ingest_if_configured()
    server_port = os.environ.get("PORT", "8080")
    app.run(debug=False, port=int(server_port), host="0.0.0.0")
