# insta2tg bulk uploader

Telegram download & upload bot for public Instagram posts. It reads Instagram URLs from `links.txt`, downloads the media locally with yt-dlp, then uploads every media file to Telegram using **Wzgram over MTProto**.

## What it does

- Handles single-photo posts, videos/reels and carousel posts through yt-dlp's Instagram extractor.
- Video selection preference: exact 360p, then exact 480p, then exact 720p. If none exists, it selects the best available height up to 360p, then up to 480p, then up to 720p, then the best remaining format.
- Preserves carousel order. Telegram allows at most 10 media items per album; carousels larger than 10 are split into consecutive albums.
- Sends every Telegram media upload through one Wzgram MTProto client.
- Uses Wzgram for Telegram document uploads and downloads too, including database backup delivery and restoring a backup sent to the bot.
- Stores completed posts and album chunks in SQLite, allowing the queue to resume after a restart.
- Sends a consistent SQLite database snapshot to the owner's Telegram private chat after every attempted post.
- Supports restoring a snapshot: reply to the backup document with `/restore` in the bot's private chat.
- Removes each post's temporary downloaded media directory after its attempt.
- Supports `/start`, `/status`, `/pause`, `/resume`, `/stop` and `/restore`.

## Telegram transport and file-size limits

Wzgram uses Telegram's MTProto API for both bot and user identities. This project continues to use a **bot identity**, so `BOT_TOKEN` is still required to authorize that identity; it is not used to call the HTTP Bot API.

The code no longer has separate small-file and large-file upload routes. Photos, videos, albums and backup documents all go through Wzgram. This removes the Bot API's 10/50 MB multipart and 5/20 MB URL-fetch thresholds from the application logic.

This does **not** mean unlimited file sizes: Telegram still enforces its platform limits. Wzgram's documentation describes a maximum of about 2000 MiB per file for MTProto bot transfers. A file larger than Telegram permits will be rejected by Telegram, regardless of the client library. See [Wzgram's MTProto vs Bot API guide](https://wzgram.com/topics/mtproto-vs-botapi/).

## Setup on Windows

1. Install Python 3.10 or newer.
2. Copy `.env.example` to `.env` and fill in the Telegram settings.
3. Add Instagram URLs to `links.txt`, one URL per line.
4. Run `start.bat`. It creates a virtual environment and installs dependencies automatically.
5. Open your bot in Telegram and send `/start`.

The bot must be an administrator in the destination channel with permission to post.

## Configuration

- `BOT_TOKEN`: token from BotFather. Wzgram uses it to authorize the bot identity over MTProto; the project does not call the HTTP Bot API.
- `API_ID` and `API_HASH`: from https://my.telegram.org/apps.
- `TARGET_CHAT_ID`: channel username (for example `@my_channel`) or numeric channel ID.
- `OWNER_ID`: your Telegram numeric user ID. Set it to your ID so strangers cannot control the queue. Value `0` disables this restriction.
- `BACKUP_CHAT_ID`: your Telegram numeric user ID for private database backups. Defaults to `OWNER_ID`; keep it set to your own ID.
- `REQUEST_DELAY=2`: delay between album chunks.

## Runtime restarts and database recovery

The bot uses SQLite's online backup API to create a consistent snapshot (including committed WAL data) and sends it to `BACKUP_CHAT_ID` through Wzgram. If the runtime loses its local disk, download the latest backup from your private chat, send it back to the bot, then reply to that document with `/restore`. Wzgram downloads the replied Telegram document; the bot validates its SQLite integrity and required tables before replacing the database. Keep `links.txt` available on the new runtime too; the database tracks progress but does not contain the original input list.

A restart can still happen between a Telegram upload and the next checkpoint, so the most recent in-flight post may need to be retried. Exactly-once delivery cannot be guaranteed across an ambiguous network failure.

Temporary downloaded Instagram media is removed after each post attempt. Files are not deleted before Wzgram confirms the upload.

## Notes

- Instagram downloads are performed by yt-dlp; Wzgram handles Telegram MTProto operations. These are separate responsibilities.
- Use only public posts you are authorized to download. Instagram can rate-limit requests or restrict access; keep delays conservative and stop if access is restricted. No proxy rotation is used.
- Keep `.env`, `progress.sqlite3`, `downloads/` and `downloader.log` private. Do not commit credentials or the input URL list.
