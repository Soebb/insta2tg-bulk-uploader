"""Python-side downloader engine. The Android service owns the long-running lifecycle."""

from pathlib import Path
import logging
import os

try:
    import yt_dlp
except Exception:
    yt_dlp = None

_log = logging.getLogger("instagram_bulk_android")
_paused = False
_stopped = False


def initialize(files_dir):
    global _log
    root = Path(files_dir)
    root.mkdir(parents=True, exist_ok=True)
    log_file = root / "downloader.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(str(log_file), encoding="utf-8")],
        force=True,
    )
    _log = logging.getLogger("instagram_bulk_android")
    if yt_dlp is None:
        raise RuntimeError("yt-dlp could not be imported in the embedded Python runtime")
    links = root / "links.txt"
    if not links.exists():
        return "Python ready — select links.txt"
    count = sum(1 for line in links.read_text(encoding="utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#"))
    _log.info("Engine initialized. %d input lines found.", count)
    return "Python ready — %d links loaded (engine integration in progress)" % count


def pause():
    global _paused
    _paused = True
    _log.info("Pause requested")


def resume():
    global _paused
    _paused = False
    _log.info("Resume requested")


def stop():
    global _stopped, _paused
    _stopped = True
    _paused = False
    _log.info("Stop requested")
