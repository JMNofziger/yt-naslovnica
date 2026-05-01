#!/usr/bin/env bash
# Provision minimal GCP resources for the YouTube Newsfeed app:
# - APIs (Firestore, Vertex AI, YouTube Data API, Cloud Run helpers)
# - Firestore Native default database (if missing)
# - IAM for the default Compute Engine / Cloud Run runtime service account
#
# Usage:
#   export GOOGLE_CLOUD_PROJECT=my-project-id   # optional if you pass $1
#   ./scripts/setup_gcp_infrastructure.sh [PROJECT_ID]
#
# Optional overrides:
#   FIRESTORE_LOCATION=europe-west1   # must match a Firestore-supported region
#
# Prerequisites:
#   - gcloud installed and authenticated (`gcloud auth login`)
#   - Billing enabled on the project
#   - Org policies allow Firestore / Vertex in your folder (if restricted)

set -euo pipefail

# Avoid interactive "Enable API? (y/N)" prompts in Cursor / CI terminals.
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "Error: set GOOGLE_CLOUD_PROJECT or pass PROJECT_ID as first argument." >&2
  exit 1
fi

FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-europe-west1}"

echo "Using project: ${PROJECT_ID}"
echo "Firestore location (Native): ${FIRESTORE_LOCATION}"

echo ">> Enabling APIs (idempotent)..."
gcloud services enable \
  --project="${PROJECT_ID}" \
  --quiet \
  firestore.googleapis.com \
  aiplatform.googleapis.com \
  youtube.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com

echo ">> Ensuring Firestore Native (default) database exists..."
if gcloud firestore databases describe --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "   Firestore database '(default)' already exists — skipping create."
else
  echo "   Creating Firestore Native '(default)' in ${FIRESTORE_LOCATION}..."
  # Defaults: database=(default), type=firestore-native, edition=standard (see `gcloud firestore databases create --help`).
  gcloud firestore databases create \
    --project="${PROJECT_ID}" \
    --location="${FIRESTORE_LOCATION}"
fi

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo ">> Granting IAM roles to Cloud Run / Compute default runtime SA:"
echo "   ${RUNTIME_SA}"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user" \
  --quiet

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/aiplatform.user" \
  --quiet

echo ""
echo "Done."
echo "- Firestore: gcloud firestore databases describe --project=${PROJECT_ID}"
echo "- Create a YouTube Data API key in APIs & Services → Credentials (Console)."
echo "- Deploy Cloud Run with GOOGLE_CLOUD_PROJECT=${PROJECT_ID} and secrets/env per README."
