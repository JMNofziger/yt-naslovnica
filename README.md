# YT Naslovnica

An **aggregated front page of hand-picked YouTube sources** focused on **Croatia and Zagreb** — national and regional news, broadcasters, tourism, expat life, and related channels. A scheduler triggers **automatic ingestion** of recent uploads; each item gets a **short English headline and summary** (Vertex Gemini). Visitors **vote up or down**; **ordering favors higher scores** so favorites surface above newer-but-weaker posts (within the active feed window).

The UI brand in-app is **“Youtube Naslovnica”** / compact **“YT Naslovnica”** on small screens.

**Reference deployment:** Cloud Run service **`hackathon`**, GCP project **`summarizer-lab`**, region **`europe-west1`** (URL shape: `https://hackathon-<PROJECT_NUMBER>.europe-west1.run.app`).

## Stack

Python **Flask**, **Firestore**, **Vertex AI (Gemini)**, **YouTube Data API v3**, **Docker** → Cloud Run.

## Operator quick path

```bash
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
export GOOGLE_CLOUD_PROJECT=summarizer-lab   # or yours

cd youtube-summarizer-1   # repository root (see rename note below)
pip install -r requirements.txt

chmod +x scripts/setup_gcp_infrastructure.sh
./scripts/setup_gcp_infrastructure.sh "$GOOGLE_CLOUD_PROJECT"
```

Create a **YouTube Data API key** (Console → APIs & Services → Credentials). Set **`YOUTUBE_API_KEY`** on Cloud Run.

Secure ingest: **`INGEST_SECRET`** must be set for **`POST /tasks/ingest`**. Production mounts **`hackathon-ingest-secret`** from Secret Manager. After rotating that secret, refresh Scheduler headers:

```bash
chmod +x scripts/sync_hackathon_ingest_scheduler.sh
./scripts/sync_hackathon_ingest_scheduler.sh
```

**Deploy / redeploy** (preserves Secret-ref pattern):

```bash
gcloud run deploy hackathon --source . --region europe-west1 --project summarizer-lab \
  --timeout=900 \
  --set-secrets=INGEST_SECRET=hackathon-ingest-secret:latest
```

**Manual ingest** (example):

```bash
SERVICE_URL="$(gcloud run services describe hackathon --region europe-west1 --project summarizer-lab --format='value(status.url)')"
curl -sS -X POST "${SERVICE_URL}/tasks/ingest" \
  -H "X-Ingest-Secret: $(gcloud secrets versions access latest --secret=hackathon-ingest-secret --project summarizer-lab)"
```

Use **`CLOUDSDK_CORE_DISABLE_PROMPTS=1`** in automation so **`gcloud`** never blocks on API-enable **`y/N`** prompts.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `GOOGLE_CLOUD_PROJECT` / `GCP_PROJECT` | GCP project (default in code: `summarizer-lab`) |
| `YOUTUBE_API_KEY` | Ingestion / channel resolution |
| `INGEST_SECRET` | **`POST /tasks/ingest`** — **`503`** if unset on service |
| `YOUTUBE_CHANNEL_IDS` | Optional comma-separated `UC…` and/or `/@handle/` URLs; else **`DEFAULT_CHANNEL_SOURCES`** in `app.py` |
| `VERTEX_LOCATION`, `GEMINI_MODEL` | Vertex defaults `europe-west1`, `gemini-2.5-flash` |
| `FEED_DAYS` | Published-after cutoff for main feed vs **`/archive`** (default **30**) |
| `MAX_VIDEOS_PER_RUN`, `INGEST_LOOKBACK_DAYS` | Ingest caps / search window |

Startup ingest flags (**`INITIAL_INGEST_ON_STARTUP`**, **`FORCE_INGEST_ON_STARTUP`**) are documented in **`app.py`**.

Runtime SA needs Firestore (**Datastore User**), **Vertex AI User**, and **Secret Accessor** on the ingest secret if used.

## Data model

Firestore **`feed_items`**, document id = YouTube video id — **`title`**, **`title_raw`**, **`url`**, **`channel`**, **`published_at`**, **`summary`**, **`primary_language`**, **`upvotes`**, **`downvotes`**.

## Local development

```bash
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=summarizer-lab
gcloud auth application-default login
python app.py
```

Optional empty-db demo rows: **`scripts/seed_placeholder_feed_items.py`**. One-off diversity backfill: **`scripts/oneoff_one_video_per_channel.py`**.

## Renaming this repository to **`yt-naslovnica`**

GitHub **rename first**, then update clients so nothing depends on the old URL long-term (GitHub redirects for a while, but triggers and bookmarks should move).

1. **GitHub** → repo **Settings → General → Repository name** → **`yt-naslovnica`**.
2. **Every clone:**

   ```bash
   git remote set-url origin git@github.com:JMNofziger/yt-naslovnica.git
   git fetch origin
   ```

   Replace **`JMNofziger`** with your org/user if different.

3. **GCP Cloud Build** (or any CI): reconnect triggers / edit URLs if they pointed at the old repo path.

4. **Cursor / IDE** “linked repo”: reopen from Git clone URL after **`git remote set-url`** if the IDE caches the old path.

Do **not** change **`origin`** to **`yt-naslovnica`** until step **1** is done, or **`git fetch`/`pull`** will fail until the repo exists under that name.
