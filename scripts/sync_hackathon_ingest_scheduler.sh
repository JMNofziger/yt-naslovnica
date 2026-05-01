#!/usr/bin/env bash
# Create or update Cloud Scheduler HTTP job for POST /tasks/ingest with X-Ingest-Secret.
# Reads the ingest secret from Secret Manager at runtime (avoid storing it in repo).
#
# Prerequisites: gcloud auth, Secret Manager secret hackathon-ingest-secret, Cloud Run hackathon deployed.
#
# Usage:
#   ./scripts/sync_hackathon_ingest_scheduler.sh
# Env overrides: GOOGLE_CLOUD_PROJECT SCHEDULER_LOCATION INGEST_SCHEDULER_JOB INGEST_SECRET_NAME CLOUD_RUN_SERVICE INGEST_SCHEDULE

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-summarizer-lab}"
REGION="${SCHEDULER_LOCATION:-europe-west1}"
JOB="${INGEST_SCHEDULER_JOB:-hackathon-feed-ingest}"
SECRET_NAME="${INGEST_SECRET_NAME:-hackathon-ingest-secret}"
SERVICE="${CLOUD_RUN_SERVICE:-hackathon}"
SCHEDULE="${INGEST_SCHEDULE:-0 6 * * *}"

SERVICE_URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" --format='value(status.url)')"
SECRET_VAL="$(gcloud secrets versions access latest --secret="$SECRET_NAME" --project="$PROJECT")"
HDR="User-Agent=Google-Cloud-Scheduler,X-Ingest-Secret=${SECRET_VAL}"

if gcloud scheduler jobs describe "$JOB" --location="$REGION" --project="$PROJECT" &>/dev/null; then
  gcloud scheduler jobs update http "$JOB" \
    --location="$REGION" \
    --project="$PROJECT" \
    --uri="${SERVICE_URL}/tasks/ingest" \
    --http-method=POST \
    --attempt-deadline=900s \
    --update-headers="$HDR" \
    --quiet >/dev/null
  echo "Updated scheduler job ${JOB}."
else
  gcloud scheduler jobs create http "$JOB" \
    --location="$REGION" \
    --project="$PROJECT" \
    --schedule="$SCHEDULE" \
    --uri="${SERVICE_URL}/tasks/ingest" \
    --http-method=POST \
    --attempt-deadline=900s \
    --headers="$HDR" \
    --quiet >/dev/null
  echo "Created scheduler job ${JOB} (${SCHEDULE})."
fi
