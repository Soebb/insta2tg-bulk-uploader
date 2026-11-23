# Cloud transfer worker

This service moves Instagram media directly from Instagram to Telegram. The Android
app sends only the Instagram post URL and polls a small JSON status endpoint.
The media itself is never downloaded to or uploaded from the phone.

## Deploy on Railway

1. Create a Railway service from this repository.
2. Set the service **Root Directory** to `/cloud`.
3. Railway detects `requirements.txt` and the `Procfile` in this directory.
4. Add the environment variables below.
5. Generate a public HTTPS domain.

Required variables:

- `BOT_TOKEN`: Telegram bot token.
- `API_ID`: Telegram API ID.
- `API_HASH`: Telegram API hash.
- `TARGET_CHAT_ID`: destination channel username or numeric ID.
- `CLOUD_API_KEY`: a long random secret used by the Android client.

Optional variables:

- `REQUEST_DELAY`: delay between media groups (default: 2 seconds).
- `JOB_DB_PATH`: SQLite job database path (default: `/tmp/instagram-cloud-jobs.sqlite3`).
- `WORK_DIR`: temporary download directory (default: `/tmp/instagram-cloud-work`).
- `LOG_LEVEL`: log level (default: `INFO`).

The bot must be an administrator in the destination channel with permission to post.
The bot token and MTProto API credentials stay on the cloud service; do not put them
in the Android app when using cloud mode.

## API

All endpoints except `GET /health` require the `X-API-Key` header.

Submit a job:

```http
POST /v1/jobs
X-API-Key: YOUR_CLOUD_API_KEY
Content-Type: application/json

{"url":"https://www.instagram.com/reel/EXAMPLE/"}
```

Response:

```json
{"job_id":"...","status":"queued"}
```

Poll status:

```http
GET /v1/jobs/JOB_ID
X-API-Key: YOUR_CLOUD_API_KEY
```

The response includes `status` (`queued`, `processing`, `done`, or `failed`),
`stage`, `progress`, and an `error` field when applicable.

## Resource and reliability notes

- yt-dlp writes media to temporary disk storage; it does not hold the full media file
  in Python RAM.
- Kurigram uploads from the temporary file path using its chunked MTProto transfer.
- This avoids sending media bytes over the phone's mobile connection. The cloud
  server uses its own bandwidth to download from Instagram and upload to Telegram.
- Temporary files are deleted after each job.
- The SQLite job database and temporary files use the container filesystem by default.
  They are not durable across every platform restart/redeployment unless a persistent
  volume is configured. A restart during an upload marks the job as interrupted; it
  is not automatically retried because Telegram may already have accepted the post.
- This first service version processes one job at a time. The Android client
  integration, cancellation, and restart-safe reconciliation are separate steps.
- The cloud provider must be able to reach both Instagram's media CDN and Telegram.
  The phone only needs to reach the cloud API, but if the provider's domain is blocked
  on the phone network, the client still needs a working route to that API.
