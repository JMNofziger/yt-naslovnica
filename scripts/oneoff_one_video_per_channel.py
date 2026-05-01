#!/usr/bin/env python3
"""
One-off backfill: ingest at most ONE newest video per configured channel that is
not already in Firestore (same doc shape as normal ingest).

Uses helpers from app.py (does not change ingest routes or caps).

Requires:
  - Same `pip install -r requirements.txt` as the Flask app (google-cloud-firestore, google-genai, …)
  - YOUTUBE_API_KEY
  - GOOGLE_CLOUD_PROJECT / GCP_PROJECT (or default summarizer-lab)
  - Application Default Credentials with Firestore + Vertex (same as Cloud Run runtime)

Example:
  cd youtube-summarizer-1
  source .venv/bin/activate   # optional
  pip install -r requirements.txt
  export YOUTUBE_API_KEY=...
  export GOOGLE_CLOUD_PROJECT=summarizer-lab
  gcloud auth application-default login
  python3 scripts/oneoff_one_video_per_channel.py --lookback-days 120
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import app as newsfeed  # noqa: E402
except ModuleNotFoundError as exc:
    print(
        "Missing Python dependencies. Install the app requirements in this environment:\n"
        f"  cd {ROOT}\n"
        "  pip install -r requirements.txt\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("oneoff_one_video_per_channel")


def main() -> None:
    ap = argparse.ArgumentParser(description="One video per channel backfill (newest missing in Firestore).")
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="publishedAfter window per channel search (default 120 to beat MAX_VIDEOS_PER_RUN skew).",
    )
    args = ap.parse_args()

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        logger.error("Set YOUTUBE_API_KEY")
        sys.exit(1)

    newsfeed.prefetch_firestore_or_raise()

    channel_refs = newsfeed.channel_sources_from_env_or_defaults()
    resolved_pairs: list[tuple[str, str]] = []
    resolve_warnings: list[str] = []
    for ref in channel_refs:
        cid = newsfeed.youtube_channel_id_from_reference(api_key, ref)
        if cid:
            resolved_pairs.append((cid, ref))
        else:
            resolve_warnings.append(ref)

    unique_ids = newsfeed._unique_channel_ids([p[0] for p in resolved_pairs])
    cid_ref: dict[str, str] = {}
    for cid, ref in resolved_pairs:
        cid_ref.setdefault(cid, ref)

    cut = newsfeed.utcnow() - timedelta(days=args.lookback_days)
    published_after_iso = cut.strftime("%Y-%m-%dT%H:%M:%SZ")

    db = newsfeed.get_fs()
    processed = 0
    errors: list[dict] = []
    no_candidate: list[str] = []

    for cid in unique_ids:
        label = cid_ref.get(cid, cid)
        try:
            batch = newsfeed.youtube_search_channel(api_key, cid, published_after_iso)
        except Exception as exc:
            logger.exception("YouTube search failed for %s", label)
            errors.append({"channel": label, "error": str(exc)})
            continue

        batch.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        row = None
        for cand in batch:
            vid = cand["video_id"]
            if not db.collection(newsfeed.COLLECTION).document(vid).get().exists:
                row = cand
                break
        if row is None:
            no_candidate.append(label)
            logger.info("No missing video in last %s days for %s", args.lookback_days, label)
            continue

        vid = row["video_id"]
        doc_ref = db.collection(newsfeed.COLLECTION).document(vid)
        watch = newsfeed.youtube_watch_url(vid)
        raw_title = row.get("title") or ""
        card_headline = newsfeed.concise_english_card_title(raw_title)
        try:
            summary, spoken_lang = newsfeed.generate_summary_and_spoken_language(watch, " ")
        except Exception as exc:
            logger.exception("Gemini failed for %s", vid)
            errors.append({"video_id": vid, "error": str(exc)})
            continue

        try:
            pub_dt = newsfeed.parse_youtube_published_at(row["published_at"])
        except Exception:
            pub_dt = newsfeed.utcnow()

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
        doc_ref.set(doc)
        processed += 1
        logger.info("Ingested %s (%s)", vid, label)

    out = {
        "ok": True,
        "processed": processed,
        "channels_total": len(unique_ids),
        "resolve_warnings": resolve_warnings,
        "no_new_video_in_window": no_candidate,
        "errors": errors[:20],
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
