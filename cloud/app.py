"""Cloud-side Instagram -> Telegram worker.

The Android client submits only Instagram URLs. Media is downloaded to temporary
disk storage and uploaded from the server with Kurigram; media bytes never pass
through the phone.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, HttpUrl
import yt_dlp
from pyrogram import Client
from pyrogram.types import InputMediaPhoto, InputMediaVideo

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("cloud_worker")

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TARGET_CHAT_ID = os.environ["TARGET_CHAT_ID"]
CLOUD_API_KEY = os.environ["CLOUD_API_KEY"]
DB_PATH = Path(os.getenv("JOB_DB_PATH", "/tmp/instagram-cloud-jobs.sqlite3"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/tmp/instagram-cloud-work"))
REQUEST_DELAY = max(0.0, float(os.getenv("REQUEST_DELAY", "2")))

app = FastAPI(title="Instagram Bulk Downloader Cloud Worker", version="1.0.0")
jobs_queue = queue.Queue()
worker_started = False
worker_lock = threading.Lock()


class JobRequest(BaseModel):
    url: HttpUrl


def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT 'queued',
            result TEXT,
            error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    return db


def update_job(job_id, **values):
    allowed = {"status", "progress", "stage", "result", "error"}
    values = {key: value for key, value in values.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = "CURRENT_TIMESTAMP"
    assignments = []
    params = []
    for key, value in values.items():
        if key == "updated_at":
            assignments.append("updated_at=CURRENT_TIMESTAMP")
        else:
            assignments.append(key + "=?")
            params.append(value)
    params.append(job_id)
    db = connect_db()
    try:
        db.execute("UPDATE jobs SET " + ",".join(assignments) + " WHERE id=?", params)
        db.commit()
    finally:
        db.close()


def read_job(job_id):
    db = connect_db()
    try:
        row = db.execute(
            "SELECT id,url,status,progress,stage,result,error,created_at,updated_at FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def authorize(api_key):
    if not api_key or not hmac.compare_digest(api_key, CLOUD_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


def extract_media(url, folder, job_id):
    folder.mkdir(parents=True, exist_ok=True)

    def progress_hook(data):
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            percent = int(downloaded * 100 / total) if total else 0
            update_job(job_id, status="processing", stage="downloading", progress=min(49, percent // 2))
        elif status == "finished":
            update_job(job_id, status="processing", stage="preparing media", progress=50)

    options = {
        "outtmpl": str(folder / "%(autonumber)03d-%(id)s.%(ext)s"),
        "format": "best[height=360]/best[height=480]/best[height=720]/best[height<=360]/best[height<=480]/best[height<=720]/best",
        "noplaylist": False,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 5,
        "socket_timeout": 45,
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook],
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(str(url), download=True)
    if not info:
        raise RuntimeError("yt-dlp returned no information for this Instagram URL.")

    entries = info.get("entries") or [info]
    flattened = []
    for entry in entries:
        if not entry:
            continue
        flattened.extend([item for item in (entry.get("entries") or [entry]) if item])

    media = []
    for entry in flattened:
        candidates = []
        for item in entry.get("requested_downloads") or []:
            if item.get("filepath"):
                candidates.append(Path(item["filepath"]))
        if entry.get("filepath"):
            candidates.append(Path(entry["filepath"]))
        media_id = str(entry.get("id") or "")
        if media_id:
            candidates.extend(folder.glob("*" + media_id + ".*"))
        path = next((p for p in candidates if p.is_file() and not p.name.endswith((".part", ".ytdl"))), None)
        if path and path not in [item["path"] for item in media]:
            media.append({
                "path": path,
                "is_video": path.suffix.lower() in {".mp4", ".m4v", ".mov", ".webm", ".mkv"},
                "id": media_id or hashlib.sha1(path.name.encode()).hexdigest()[:12],
            })

    if not media:
        for path in sorted(folder.iterdir()):
            if path.is_file() and not path.name.endswith((".part", ".ytdl")):
                media.append({
                    "path": path,
                    "is_video": path.suffix.lower() in {".mp4", ".m4v", ".mov", ".webm", ".mkv"},
                    "id": hashlib.sha1(path.name.encode()).hexdigest()[:12],
                })
    if not media:
        raise RuntimeError("No downloaded media files were found.")
    return media, (info.get("description") or "")


async def upload_media(media, caption, job_id):
    client = Client(
        name="cloud_instagram_bulk_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        workdir=str(WORK_DIR / "kurigram"),
        no_updates=True,
        sleep_threshold=60,
    )
    await client.start()
    try:
        total_bytes = sum(item["path"].stat().st_size for item in media)
        sent_bytes = 0
        for start in range(0, len(media), 10):
            batch = media[start:start + 10]
            batch_caption = caption if start == 0 else ""
            if len(batch) == 1:
                item = batch[0]

                def progress(current, total, *_args):
                    if total:
                        percent = int((sent_bytes + current) * 50 / max(total_bytes, 1))
                        update_job(job_id, status="processing", stage="uploading", progress=50 + min(49, percent))

                common = {"chat_id": TARGET_CHAT_ID, "caption": batch_caption[:1024] or None, "progress": progress}
                if item["is_video"]:
                    await client.send_video(
                        video=str(item["path"]),
                        supports_streaming=True,
                        **common,
                    )
                else:
                    await client.send_photo(photo=str(item["path"]), **common)
                sent_bytes += item["path"].stat().st_size
            else:
                inputs = []
                for index, item in enumerate(batch):
                    item_caption = batch_caption[:1024] if index == 0 and batch_caption else None
                    if item["is_video"]:
                        inputs.append(InputMediaVideo(
                            media=str(item["path"]),
                            caption=item_caption,
                            supports_streaming=True,
                        ))
                    else:
                        inputs.append(InputMediaPhoto(media=str(item["path"]), caption=item_caption))
                await client.send_media_group(chat_id=TARGET_CHAT_ID, media=inputs)
                sent_bytes += sum(item["path"].stat().st_size for item in batch)

            update_job(
                job_id,
                status="processing",
                stage="uploading",
                progress=50 + int(49 * sent_bytes / max(total_bytes, 1)),
            )
            if start + 10 < len(media) and REQUEST_DELAY:
                await asyncio.sleep(REQUEST_DELAY)
    finally:
        await client.stop()


def process_job(job_id, url):
    folder = WORK_DIR / job_id
    try:
        update_job(job_id, status="processing", stage="extracting", progress=0, error=None)
        media, description = extract_media(url, folder, job_id)
        caption = str(url) + ("\n\n" + description if description else "")
        update_job(job_id, status="processing", stage="uploading", progress=50)
        asyncio.run(upload_media(media, caption, job_id))
        update_job(
            job_id,
            status="done",
            stage="completed",
            progress=100,
            result=json.dumps({"media_count": len(media)}),
            error=None,
        )
        log.info("Job %s completed (%d media item(s)).", job_id, len(media))
    except Exception as exc:
        log.exception("Job %s failed.", job_id)
        update_job(job_id, status="failed", stage="failed", error=(str(exc) or type(exc).__name__)[:1000])
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def worker_loop():
    while True:
        job_id, url = jobs_queue.get()
        try:
            process_job(job_id, url)
        finally:
            jobs_queue.task_done()


@app.on_event("startup")
def startup():
    global worker_started
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    db = connect_db()
    try:
        # A process restart cannot safely resume an in-flight Telegram upload.
        db.execute(
            "UPDATE jobs SET status='failed',stage='interrupted',error='Cloud worker restarted during this job',updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','processing')"
        )
        db.commit()
    finally:
        db.close()
    with worker_lock:
        if not worker_started:
            thread = threading.Thread(target=worker_loop, name="instagram-cloud-worker", daemon=True)
            thread.start()
            worker_started = True


@app.get("/health")
def health():
    return {"ok": True, "service": "instagram-bulk-cloud-worker"}


@app.post("/v1/jobs", status_code=202)
def submit_job(payload: JobRequest, x_api_key: str = Header(default="")):
    authorize(x_api_key)
    url = str(payload.url)
    if "instagram.com/" not in url.lower():
        raise HTTPException(status_code=400, detail="Only Instagram URLs are accepted.")
    job_id = uuid.uuid4().hex
    db = connect_db()
    try:
        db.execute(
            "INSERT INTO jobs(id,url,status,stage) VALUES(?,?,'queued','queued')",
            (job_id, url),
        )
        db.commit()
    finally:
        db.close()
    jobs_queue.put((job_id, url))
    return {"job_id": job_id, "status": "queued"}


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, x_api_key: str = Header(default="")):
    authorize(x_api_key)
    job = read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
