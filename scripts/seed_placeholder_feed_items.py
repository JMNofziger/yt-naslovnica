#!/usr/bin/env python3
"""
Optional bootstrap: write a few synthetic feed_items so the UI is non-empty.

Real content comes from POST /tasks/ingest (needs YOUTUBE_API_KEY on Cloud Run).

Requires Application Default Credentials with Firestore write access, e.g.:
  gcloud auth application-default login

Usage:
  python scripts/seed_placeholder_feed_items.py [PROJECT_ID]

Deletes/re-run safe: uses fixed doc ids prefixed with SEED_.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from google.cloud import firestore

COLLECTION = "feed_items"


def main() -> None:
    project_id = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or "summarizer-lab"
    )
    db = firestore.Client(project=project_id)
    now = datetime.now(timezone.utc)

    demos = [
        {
            "doc_id": "SEED_BOOTSTRAP_CARD",
            "data": {
                "title_raw": "Next step: add YOUTUBE_API_KEY and run ingestion",
                "title": "Next step: add YOUTUBE_API_KEY and run ingestion",
                "url": "https://www.youtube.com/watch?v=M7FIvfx5J10",
                "channel": "Seed script",
                "published_at": now - timedelta(days=1),
                "summary": (
                    "This card was written by scripts/seed_placeholder_feed_items.py.\n\n"
                    "To replace it with real summaries: set YOUTUBE_API_KEY on the hackathon "
                    "Cloud Run service, then POST /tasks/ingest (see README). "
                    "You can delete documents whose ids start with SEED_ afterwards."
                ),
                "upvotes": 0,
                "downvotes": 0,
            },
        },
        {
            "doc_id": "SEED_SECOND_CARD",
            "data": {
                "title_raw": "Firestore is connected — feed queries work",
                "title": "Firestore is connected — feed queries work",
                "url": "https://www.youtube.com/watch?v=M7FIvfx5J10",
                "channel": "Seed script",
                "published_at": now - timedelta(days=3),
                "summary": (
                    "If you see this row, Firestore reads/writes succeeded for project "
                    f"{project_id!r}. Configure ingestion for live channel content."
                ),
                "upvotes": 0,
                "downvotes": 0,
            },
        },
    ]

    for row in demos:
        db.collection(COLLECTION).document(row["doc_id"]).set(row["data"])

    print(f"Wrote {len(demos)} documents into {project_id!r} / {COLLECTION}.")


if __name__ == "__main__":
    main()
