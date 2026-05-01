# YouTube Newsfeed — Project Description

A tiny **read-only news site** that watches a fixed list of **YouTube channels**, turns each new-ish video into a **short summary** with **Google Gemini** (Vertex AI), and lays results out in a **scrollable feed** that feels a bit like Reddit: title, metadata, text blurb, **upvote/downvote**, and a link out to the actual video.

Built as a **prototype**: one Flask app, one Firestore collection, no accounts, no anti-abuse voting constraints—optimized for **shipping quickly** on **Google Cloud Run** with **Firestore** as the database.

---

## The pitch (30 seconds)

Keeping up with several channels means opening YouTube and losing an hour. This project **pulls recent uploads from channels you care about**, asks Gemini **what was actually discussed**, and puts it all on **one page** so you can skim in seconds—then dip into the video only when it matters. Older stuff rolls into an **archive** so the main feed stays focused on roughly the **last week** of publishing activity.

---

## What it does

| Area | Behavior |
|------|----------|
| **Feed** | Home page lists cards for videos whose **publish date** falls within the **last 7 days** (config-style constant in code). |
| **Archive** | Anything older than that window still lives under `/archive`, same card layout. |
| **Summaries** | Each item stores a **Gemini-generated summary** of the video (same pattern as the original Vertex “summarize this YouTube URL” demo). |
| **Votes** | Visitors can **upvote or downvote** items without logging in; counts are stored **on the item document** (simple counters). |
| **Sources** | Ingestion pulls from a **configurable list** of channels. By default, if you don’t override env vars, it uses a **placeholder set** aimed at **Croatia / expat-in-Croatia** content (see below). |
| **Ingestion** | On a schedule **you** define (e.g. daily cron or Cloud Scheduler), hit **`POST /tasks/ingest`** to fetch recent uploads from YouTube’s API, skip videos already in Firestore, and summarize new ones up to a **per-run cap** to control cost. Optional **startup bootstrap** can run a first ingest when the database is still empty. |

---

## Default placeholder channels (built-in)

If `YOUTUBE_CHANNEL_IDS` is not set, ingestion falls back to these (handles resolved automatically):

- [Paul Bradbury](https://www.youtube.com/@PaulBradbury/videos)
- [Living in Croatia](https://www.youtube.com/@livingincroatia/videos)
- [Expat Life in Croatia](https://www.youtube.com/@expatlifeincroatia/videos)
- [HRT](https://www.youtube.com/@Hrvatskaradiotelevizija_HRT/videos)
- [Total Croatia News](https://www.youtube.com/@TotalCroatiaNews/videos)

Swap them anytime via environment variables or fork the list in `app.py`.

---

## How it works (architecture, one glance)

1. **YouTube Data API** — Search recent uploads per channel (plus handle→channel-id resolution for `@handle` URLs).
2. **Vertex AI / Gemini** — For each **new** video ID, generate a summary from the watch URL (same media-backed pattern as the original summarizer lab).
3. **Firestore** — Collection `feed_items`: title, URL, channel name, publish time, summary text, vote counters.
4. **Flask** — Server-rendered HTML + CSS; Reddit-ish **vote rail** + card body; no SPA framework.

```text
[ Channels + API key ] --> [ Ingest job ]
                               |
                               v
                    [ Gemini summaries ]
                               |
                               v
                         [ Firestore ]
                               ^
                               |
                         [ Flask UI ]
                               |
                         [ Visitors ]
```

---

## Tech stack

- **Python 3.12**, **Flask**
- **Google Cloud**: Firestore (Native mode), Vertex AI (Gemini via `google-genai`), Cloud Run–friendly container (`Dockerfile`)
- **YouTube Data API v3** for discovery and metadata
- **Jinja2** templates + hand-written CSS (no React/Vue)

---

## Honest limitations (prototype scope)

- **Firestore must exist** in your GCP project (Native mode). If it isn’t created yet, you’ll see a Cloud console link until provisioning completes (`summarizer-lab` is only the code default—your Cloud Run **`GOOGLE_CLOUD_PROJECT`** must match the project where you enabled Firestore).
- **Voting** is trivially gameable; there’s no per-device limit or auth—fine for demos, not for reputation systems.
- **Cost / quota**: ingestion caps (`MAX_VIDEOS_PER_RUN`), API quotas, and Gemini usage matter if you crank volume or run ingest too often.
- **Startup ingest** only hooks **`python app.py`**; if you move to Gunicorn-only entrypoints without going through that module, wire ingest via Scheduler or manual `curl` instead.

---

## Repo layout (high level)

| Path | Role |
|------|------|
| `app.py` | Routes, Gemini calls, Firestore access, ingest pipeline, optional startup ingest thread |
| `templates/` | Base layout, feed, archive, shared `_card.html` partial |
| `static/style.css` | Reddit-like listing styles |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Container image for Cloud Run |
| `README.md` | Env vars, IAM hints, curl examples |

---

## Who might care

- People who want **ambient awareness** of a **small channel ecosystem** (news, expat life, local TV clips) without living inside the YouTube algorithm.
- Builders looking for a **small, copy-pasteable GCP pattern**: Flask + Firestore + Vertex + scheduled HTTP job.

---

## Where to go next (ideas, not promises)

- Auth and real vote integrity  
- Push notifications or email digest  
- Smarter dedupe and channel-specific budgets  
- Dedicated SPA frontend if the UI outgrows server templates  

---

## Try / ship

Clone the repo, configure GCP project + Firestore + API keys per `README.md`, deploy the container to Cloud Run (or run locally with Application Default Credentials). Trigger ingestion manually or turn on startup bootstrap for an empty database.

This document is meant for **friends and collaborators**: share it as-is when you want someone to understand **what we built and why** without reading the whole codebase.
