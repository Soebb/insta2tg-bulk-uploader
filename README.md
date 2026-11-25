# Instagram Bulk Downloader

Telegram downloader bot for public Instagram posts. It reads post URLs from \`links.txt\` and sends each post's photos/videos to a Telegram channel.

## Telegram connection

All Telegram operations use **Telethon over MTProto with HTTPS/TLS on port 443**. The project does not call the Telegram Bot HTTP API.

- \`BOT_TOKEN\` is used only by Telethon to authenticate the bot account through MTProto.
- \`API_ID\` and \`API_HASH\` are required from https://my.telegram.org.
- The custom \`TelegramHttpsConnection\` maps Telegram DCs 1-5 to their official HTTPS hostnames:
  - DC1: \`pluto.web.telegram.org\`
  - DC2: \`venus.web.telegram.org\`
  - DC3: \`aurora.web.telegram.org\`
  - DC4: \`vesta.web.telegram.org\`
  - DC5: \`flora.web.telegram.org\`
- The connection uses Telethon's \`ConnectionHttp\`; port 443 activates TLS. If an unsupported DC is encountered, the client fails closed instead of silently falling back to a raw IP/TCP connection.
- Telegram uploads, album sends, database backups, and database restore downloads are performed with Telethon.

Instagram media is still retrieved by yt-dlp. Telethon is a Telegram client, not an Instagram downloader.

## Features

- Handles single-photo posts, videos/reels and carousel posts through yt-dlp's Instagram extractor.
- Video selection preference: exact 360p, then exact 480p, then exact 720p. If none exists, it selects the best available height up to 360p, then up to 480p, then up to 720p, then the best remaining format.
- Preserves carousel order. Telegram allows at most 10 media items per album; carousels larger than 10 are split into consecutive albums.
- Adds post URL and description as the caption on the first album/message.
- Tries Telegram-side URL fetching through Telethon's MTProto \`send_file\` support first. If that fails, yt-dlp downloads the files locally and Telethon uploads them.
- Stores completed posts and album chunks in SQLite, allowing the queue to resume after a restart.
- Sends a consistent SQLite database snapshot to the owner's Telegram private chat after every attempted post.
- Supports restoring a snapshot: reply to a database backup document with \`/restore\` in the bot's private chat.
- Removes each post's temporary downloaded media directory after its attempt, including failed attempts.
- Supports \`/start\`, \`/status\`, \`/pause\`, \`/resume\`, \`/stop\` and \`/restore\`.

## Setup on Windows

1. Install Python 3.10+.
2. Copy \`.env.example\` to \`.env\` and fill in your Telegram settings.
3. Add Instagram URLs to \`links.txt\`, one URL per line.
4. Run \`start.bat\`. It creates a virtual environment and installs dependencies automatically.
5. Open your bot in Telegram and send \`/start\`.

The bot must be an administrator in the destination channel with permission to post. Open the bot's private chat and press Start once so it can send database backups to you.

## Configuration

- \`BOT_TOKEN\`: token from BotFather. Used for MTProto bot authorization by Telethon; it is not used to call the Bot API.
- \`API_ID\` and \`API_HASH\`: from https://my.telegram.org.
- \`TARGET_CHAT_ID\`: channel username (for example \`@my_channel\`) or numeric channel ID.
- \`OWNER_ID\`: your Telegram numeric user ID. Set it to your ID so strangers cannot control the queue. Value \`0\` disables this restriction.
- \`BACKUP_CHAT_ID\`: your Telegram numeric user ID for private database backups. Defaults to \`OWNER_ID\`; keep it set to your own ID.
- \`SESSION_NAME\`: local Telethon session filename (without the \`.session\` suffix).
- \`DIRECT_URL_UPLOAD=true\`: try Telegram-side URL fetching using Telethon before local transfer.
- \`REQUEST_DELAY=2\`: delay between album chunks.

## Telegram transfer limits

Telethon sends media through MTProto. Telegram's current account/bot restrictions still apply. Direct URL fetching is best-effort; if Telegram cannot fetch an Instagram CDN URL, the program downloads it locally and uploads it through Telethon. Telegram albums contain at most 10 items.

## Runtime restarts and database recovery

Telethon stores the bot authorization in the local \`SESSION_NAME.session\` file. Keep this file private and persistent. If the hosting platform has an ephemeral filesystem, configure persistent storage or preserve the session file securely; otherwise the bot may need to be authorized again after a restart.

The bot uses SQLite's online backup API to create a consistent snapshot (including committed WAL data) and sends it to \`BACKUP_CHAT_ID\` after each attempted post. If the runtime loses its local database, download the latest backup from your private chat, send it back to the bot, then reply to that document with \`/restore\`. Restore is allowed only while the queue is stopped and the backup is checked for SQLite integrity and the required tables. Keep \`links.txt\` available on the new runtime too; the database tracks progress but does not contain the original input list. A restart can still happen between a Telegram upload and the next checkpoint, so the most recent in-flight post may need to be retried.

Temporary downloaded media is removed after every post attempt, whether it succeeds or fails. Files are not deleted before Telegram confirms the upload.

## Notes

- Use only public posts you are authorized to download. Instagram can rate-limit requests or restrict access; keep delays conservative and stop if access is restricted. No proxy rotation is used.
- HTTPS transport does not guarantee reachability if the network blocks Telegram's official HTTPS hostnames or IP addresses.
- If a Telegram request times out after Telegram accepted it, the delivery result may be ambiguous and a duplicate can occur on retry. Exactly-once delivery cannot be guaranteed across a network timeout.
- Some posts may be unavailable due to privacy, login/age restrictions, deletion, or changes in Instagram's extractor.
- Keep \`.env\`, \`*.session\`, \`progress.sqlite3\`, \`downloads/\` and \`downloader.log\` private. Do not commit credentials or the input URL list.


## Signed Instagram CDN relay experiment (Android branch)

The `telethon-cdn-proxy-test` branch can route Telegram-side external URL fetching through a small Cloudflare Worker. The Worker streams bytes from an Instagram CDN URL using browser-like request headers; the Android app does not download or upload the media bytes.

1. Deploy `cloudflare/instagram-cdn-relay/worker.js` as a Cloudflare Worker.
2. In the Worker settings, add a secret named `CDN_PROXY_SECRET`. Generate a random secret of at least 32 characters.
3. In the Android app, set **CDN proxy Worker URL** to the Worker endpoint ending in `/relay`, and set **CDN proxy shared secret** to the exact same value.
4. Save settings and retry a public Reel link.

The app signs each source URL with HMAC-SHA256 and a 10-minute expiry. The Worker only accepts HTTPS URLs on Instagram CDN hosts and supports HEAD/GET (including Range forwarding). Do not publish the shared secret. The relay is an experiment: Telegram's cURL fetcher may still reject the response, and Instagram may change its CDN requirements. When no Worker URL/secret is configured, the large-URL route stops with a clear configuration error rather than sending an unprotected open-proxy URL.
