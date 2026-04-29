import logging
import os
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from google import genai
from google.cloud import firestore
from google.cloud.firestore import Increment
from google.genai import types

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__)

PROJECT_ID = (
    os.environ.get("GOOGLE_CLOUD_PROJECT")
    or os.environ.get("GCP_PROJECT")
    or "summarizer-lab"
)
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "europe-west1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

COLLECTION = "feed_items"
FEED_DAYS = 7
MAX_VIDEOS_PER_RUN = int(os.environ.get("MAX_VIDEOS_PER_RUN", "25"))
INGEST_LOOKBACK_DAYS = int(os.environ.get("INGEST_LOOKBACK_DAYS", "30"))

_fs_client = None


def get_fs():
    global _fs_client
    if _fs_client is None:
        _fs_client = firestore.Client(project=PROJECT_ID)
    return _fs_client


def utcnow():
    return datetime.now(timezone.utc)


client = genai.Client(
    vertexai=True,
    project=PROJECT_ID,
    location=VERTEX_LOCATION,
)


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


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
        items.append(
            {
                "id": pid,
                "title": d.get("title") or "(untitled)",
                "url": d.get("url") or youtube_watch_url(pid),
                "channel": d.get("channel") or "",
                "summary": d.get("summary") or "",
                "published_at": pub,
                "published_label": relative_label(pub),
                "upvotes": up,
                "downvotes": down,
                "score": up - down,
            }
        )
    return items


@app.route("/", methods=["GET"])
def index():
    try:
        items = load_items_for_feed(active=True)
    except Exception as e:
        logger.exception("feed query failed")
        items = []
        error = str(e)
        return render_template(
            "index.html",
            items=[],
            page_title="Feed",
            error=error,
            active_feed=True,
            active_archive=False,
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
        return render_template(
            "archive.html",
            items=[],
            page_title="Archive",
            error=str(e),
            active_feed=False,
            active_archive=True,
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


@app.post("/tasks/ingest")
def tasks_ingest():
    secret = os.environ.get("INGEST_SECRET")
    if secret and request.headers.get("X-Ingest-Secret") != secret:
        abort(403)

    api_key = os.environ.get("YOUTUBE_API_KEY")
    raw_channels = os.environ.get("YOUTUBE_CHANNEL_IDS", "")
    channel_ids = [c.strip() for c in raw_channels.split(",") if c.strip()]

    if not api_key or not channel_ids:
        return jsonify(
            {
                "ok": True,
                "skipped": True,
                "message": "Set YOUTUBE_API_KEY and YOUTUBE_CHANNEL_IDS to enable ingestion.",
                "processed": 0,
            }
        )

    lookback = utcnow() - timedelta(days=INGEST_LOOKBACK_DAYS)
    published_after_iso = lookback.strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    for cid in channel_ids:
        try:
            batch = youtube_search_channel(api_key, cid, published_after_iso)
            candidates.extend(batch)
        except Exception as e:
            logger.exception("youtube search failed for channel %s", cid)
            return jsonify({"ok": False, "error": str(e)}), 500

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
        try:
            summary = generate_summary(watch, " ")
        except Exception as e:
            logger.exception("gemini failed for %s", vid)
            errors.append({"video_id": vid, "error": str(e)})
            continue
        try:
            pub_dt = parse_youtube_published_at(row["published_at"])
        except Exception:
            pub_dt = utcnow()
        ref.set(
            {
                "title": row["title"],
                "url": watch,
                "channel": row["channel_title"],
                "published_at": pub_dt,
                "summary": summary,
                "upvotes": 0,
                "downvotes": 0,
            }
        )
        processed += 1

    return jsonify(
        {
            "ok": True,
            "processed": processed,
            "candidates": len(candidates),
            "errors": errors[:10],
        }
    )


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
    server_port = os.environ.get("PORT", "8080")
    app.run(debug=False, port=int(server_port), host="0.0.0.0")
