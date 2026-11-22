# Instagram Bulk Downloader

Telegram downloader bot for public Instagram posts. It reads post URLs from `links.txt` and sends each post's photos/videos to a Telegram channel.

## What it does

- Handles single-photo posts, videos/reels and carousel posts through yt-dlp's Instagram extractor.
- Video selection preference: exact 360p, then exact 480p, then exact 720p. If none exists, it selects the best available height up to 360p, then up to 480p, then up to 720p, then the best remaining format.
- Preserves carousel order. Telegram allows at most 10 media items per album; carousels larger than 10 are split into consecutive albums.
- Adds post URL and description as the caption on the first album/message.
- Tries sending public CDN URLs directly to Telegram first. Telegram fetches eligible files itself, which avoids routing those media bytes through this PC. If URL delivery is unavailable or fails, the program downloads locally and uploads the files.
- Stores completed posts and album chunks in SQLite, allowing the queue to resume after a restart.
- Sends a consistent SQLite database snapshot to the owner's Telegram private chat after every attempted post.
- Supports restoring a snapshot: reply to the backup document with `/restore` in the bot's private chat.
- Removes each post's temporary downloaded media directory after its attempt, including failed attempts.
- Supports `/start`, `/status`, `/pause`, `/resume`, `/stop` and `/restore`.

## Setup on Windows

1. Install Python 3.10+.
2. Copy `.env.example` to `.env` and fill in your Telegram settings.
3. Add Instagram URLs to `links.txt`, one URL per line.
4. Run `start.bat`. It creates a virtual environment and installs dependencies automatically.
5. Open your bot in Telegram and send `/start`.

The bot must be an administrator in the destination channel with permission to post.

## Configuration

- `BOT_TOKEN`: token from BotFather.
- `API_ID` and `API_HASH`: from https://my.telegram.org.
- `TARGET_CHAT_ID`: channel username (for example `@my_channel`) or numeric channel ID.
- `OWNER_ID`: your Telegram numeric user ID. Set it to your ID so strangers cannot control the queue. Value `0` disables this restriction.
- `BACKUP_CHAT_ID`: your Telegram numeric user ID for private database backups. Defaults to `OWNER_ID`; keep it set to your own ID. Open the bot and press Start once so it can message you.
- `DIRECT_URL_UPLOAD=true`: try Telegram-side URL fetching before local transfer.
- `REQUEST_DELAY=2`: delay between album chunks.

## Telegram transfer limits

The official Bot API documents URL fetching limits of 5 MB for photos and 20 MB for other media. Multipart uploads are limited to 10 MB for photos and 50 MB for other files. Direct URL transfer is therefore best-effort; larger media normally use the local fallback. Details: https://core.telegram.org/bots/api#sending-files

This means direct transfer reduces PC bandwidth only for media Telegram can fetch itself. It does not eliminate network transfer in general: Telegram's servers still download the Instagram CDN resource.

## Runtime restarts and database recovery

The bot uses SQLite's online backup API to create a consistent snapshot (including committed WAL data) and sends it to `BACKUP_CHAT_ID` after each attempted post. If the cloud runtime loses its local disk, download the latest backup from your private chat, send it back to the bot, then reply to that document with `/restore`. Restore is allowed only while the queue is stopped and the backup is checked for SQLite integrity and the required tables. Keep `links.txt` available on the new runtime too; the database tracks progress but does not contain the original input list. A restart can still happen between a Telegram upload and the next checkpoint, so the most recent in-flight post may need to be retried.

Temporary downloaded media is removed after every post attempt, whether it succeeds or fails. Files are not deleted before Telegram confirms the upload.

## Notes

- Use only public posts you are authorized to download. Instagram can rate-limit requests or restrict access; keep delays conservative and stop if access is restricted. No proxy rotation is used.
- If a Telegram request times out after Telegram accepted it, the delivery result may be ambiguous and a duplicate can occur on retry. Exactly-once delivery cannot be guaranteed across a network timeout.
- Some posts may be unavailable due to privacy, login/age restrictions, deletion, or changes in Instagram's extractor.
- Keep `.env`, `progress.sqlite3`, `downloads/` and `downloader.log` private. Do not commit credentials or the input URL list.
