# Cloud Run Hello World with Cloud Code

"Hello World" is a [Cloud Run](https://cloud.google.com/run/docs) application that renders a simple webpage.

## YouTube Newsfeed

Server-rendered feed of summarized YouTube videos (Gemini on Vertex AI), Firestore storage, optional ingestion via YouTube Data API (`POST /tasks/ingest`).

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_CLOUD_PROJECT` / `GCP_PROJECT` | Deploy | GCP project id (defaults to `summarizer-lab` if unset). |
| `VERTEX_LOCATION` | No | Vertex AI region (default `europe-west1`). |
| `GEMINI_MODEL` | No | Model id (default `gemini-2.5-flash`). |
| `YOUTUBE_API_KEY` | Ingestion | YouTube Data API v3 key. |
| `YOUTUBE_CHANNEL_IDS` | Ingestion | Comma-separated channel ids. |
| `INGEST_SECRET` | Recommended | If set, ingest requires header `X-Ingest-Secret`. |
| `INGEST_LOOKBACK_DAYS` | No | Search window (default `30`). |
| `MAX_VIDEOS_PER_RUN` | No | Max new summaries per run (default `25`). |
| `PORT` | No | Listen port (default `8080`). |

### Firestore

Use Native mode. Collection `feed_items`, document id = YouTube video id. Fields: `title`, `url`, `channel`, `published_at`, `summary`, `upvotes`, `downvotes`. Feed shows items published in the last 7 days; older items appear under `/archive`.

### Local run

```bash
cd youtube-summarizer-1
pip install -r requirements.txt
export GOOGLE_CLOUD_PROJECT=your-project
gcloud auth application-default login
python app.py
```

### Manual ingest

```bash
curl -sS -X POST "http://localhost:8080/tasks/ingest" -H "X-Ingest-Secret: $INGEST_SECRET"
```

Omit the header if `INGEST_SECRET` is unset (prototype only).

### Cloud Run IAM

Service account needs **Vertex AI User** and Firestore via **Cloud Datastore User** (or broader Firestore roles as needed).

For details on how to use this sample as a template in Cloud Code, read the documentation for Cloud Code for [VS Code](https://cloud.google.com/code/docs/vscode/quickstart-cloud-run?utm_source=ext&utm_medium=partner&utm_campaign=CDR_kri_gcp_cloudcodereadmes_012521&utm_content=-) or [IntelliJ](https://cloud.google.com/code/docs/intellij/quickstart-cloud-run?utm_source=ext&utm_medium=partner&utm_campaign=CDR_kri_gcp_cloudcodereadmes_012521&utm_content=-).

### Table of Contents
* [Getting Started with VS Code](#getting-started-with-vs-code)
* [Getting Started with IntelliJ](#getting-started-with-intellij)
* [Sign up for User Research](#sign-up-for-user-research)

---
## Getting Started with VS Code

### Run the app locally with the Cloud Run Emulator
1. In the Cloud Code status bar, click on the active project name and select 'Run on Cloud Run Emulator'.  
![image](./img/status-bar.png)

2. Use the Cloud Run Emulator dialog to specify your [builder option](https://cloud.google.com/code/docs/vscode/deploying-a-cloud-run-app#deploying_a_cloud_run_service). Cloud Code supports Docker, Jib, and Buildpacks. See the skaffold documentation on [builders](https://skaffold.dev/docs/builders/) for more information about build artifact types.  
![image](./img/build-config.png)

3. Click ‘Run’. Cloud Code begins building your image.

4. View the build progress in the OUTPUT window. Once the build has finished, click on the URL in the OUTPUT window to view your live application.  
![image](./img/cloud-run-url.png)

5. To stop the application, click the stop icon on the Debug Toolbar.

---
## Getting Started with IntelliJ

### Run the app locally with the Cloud Run Emulator

#### Define run configuration

1. Click the Run/Debug configurations dropdown on the top taskbar and select 'Edit Configurations'.  
![image](./img/edit-config.png)

2. Select 'Cloud Run: Run Locally' and specify your [builder option](https://cloud.google.com/code/docs/intellij/developing-a-cloud-run-app#defining_your_run_configuration). Cloud Code supports Docker, Jib, and Buildpacks. See the skaffold documentation on [builders](https://skaffold.dev/docs/builders/) for more information about build artifact types.  
![image](./img/local-build-config.png)

#### Run the application
1. Click the Run/Debug configurations dropdown and select 'Cloud Run: Run Locally'. Click the run icon.  
![image](./img/config-run-locally.png)

2. View the build process in the output window. Once the build has finished, you will receive a notification from the Event Log. Click 'View' to access the local URLs for your deployed services.  
![image](./img/local-success.png)

---
## Sign up for User Research

We want to hear your feedback!

The Cloud Code team is inviting our user community to sign-up to participate in Google User Experience Research. 

If you’re invited to join a study, you may try out a new product or tell us what you think about the products you use every day. At this time, Google is only sending invitations for upcoming remote studies. Once a study is complete, you’ll receive a token of thanks for your participation such as a gift card or some Google swag. 

[Sign up using this link](https://google.qualtrics.com/jfe/form/SV_4Me7SiMewdvVYhL?reserved=1&utm_source=In-product&Q_Language=en&utm_medium=own_prd&utm_campaign=Q1&productTag=clou&campaignDate=January2021&referral_code=UXbT481079) and answer a few questions about yourself, as this will help our research team match you to studies that are a great fit.
