"""Standalone yt-dlp engine; intentionally independent of Telegram/Telethon."""
import hashlib
from pathlib import Path

import yt_dlp


def flatten(info):
    entries = info.get("entries")
    if not entries:
        return [info]
    output = []
    for entry in entries:
        if entry:
            output.extend(flatten(entry) if entry.get("entries") else [entry])
    return output


def is_video(item):
    codec = item.get("vcodec")
    ext = (item.get("ext") or "").lower()
    return (codec and codec != "none") or ext in {
        "mp4", "m4v", "mov", "webm", "mkv"
    }


def best_format(entry):
    formats = [item for item in entry.get("formats", []) if item.get("url")]
    if not formats:
        return entry
    videos = [item for item in formats if is_video(item)]
    candidates = videos or formats
    if not videos:
        return max(
            candidates,
            key=lambda item: (item.get("width") or 0) * (item.get("height") or 0),
        )
    for height in (360, 480, 720):
        exact = [item for item in candidates if item.get("height") == height]
        if exact:
            return max(exact, key=lambda item: item.get("tbr") or 0)
    for limit in (360, 480, 720):
        lower = [
            item for item in candidates
            if (item.get("height") or 0) <= limit
        ]
        if lower:
            return max(
                lower,
                key=lambda item: (item.get("height") or 0, item.get("tbr") or 0),
            )
    return max(
        candidates,
        key=lambda item: (item.get("height") or 0, item.get("tbr") or 0),
    )


def make_items(info):
    result = []
    for entry in flatten(info):
        fmt = best_format(entry)
        media_url = fmt.get("url") or entry.get("url")
        if not media_url:
            continue
        media_id = str(entry.get("id") or hashlib.sha1(media_url.encode()).hexdigest()[:12])
        size = (
            fmt.get("filesize")
            or fmt.get("filesize_approx")
            or entry.get("filesize")
            or entry.get("filesize_approx")
        )
        result.append({
            "id": media_id,
            "url": media_url,
            "is_video": is_video(fmt) or is_video(entry),
            "size": int(size) if size else None,
        })
    return result


def inspect_post(url):
    """Extract post metadata with the same yt-dlp options as the prior branch."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": False,
        "retries": 5,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def download_post(url, folder):
    """Download every media item using the previous branch's yt-dlp settings."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    options = {
        "outtmpl": str(folder / "%(id)s.%(ext)s"),
        "format": (
            "best[height=360]/best[height=480]/best[height=720]/"
            "best[height<=360]/best[height<=480]/best[height<=720]/best"
        ),
        "noplaylist": False,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
        "retries": 8,
        "fragment_retries": 8,
        "socket_timeout": 30,
        "merge_output_format": "mp4",
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise RuntimeError("yt-dlp returned no media information.")
        entries = flatten(info)
        files = []
        for entry in entries:
            paths = [
                Path(item["filepath"])
                for item in entry.get("requested_downloads", [])
                if item.get("filepath")
            ]
            if entry.get("filepath"):
                paths.append(Path(entry["filepath"]))
            paths.extend(folder.glob(f"{entry.get('id', '')}.*"))
            found = next((path for path in paths if path.is_file()), None)
            if found and found not in files:
                files.append(found)
        if len(files) != len(entries):
            remaining = [
                path for path in sorted(folder.iterdir())
                if path.is_file()
                and path not in files
                and not path.name.endswith((".part", ".ytdl"))
            ]
            files.extend(remaining[:len(entries) - len(files)])

    if not files:
        raise RuntimeError("No downloaded files found.")
    if len(files) != len(entries):
        raise RuntimeError(
            f"Downloaded file count mismatch: expected {len(entries)}, found {len(files)}."
        )
    return files
