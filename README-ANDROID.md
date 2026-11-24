# Android app (on-device mode)

The Android app runs yt-dlp through Chaquopy and stores its working files in app-private storage. The Telegram transfer engine in this branch has been migrated to Wzgram MTProto: there is no HTTP Bot API upload path and no Kurigram fallback in the Python engine.

## Telegram credentials

- `BOT_TOKEN`: authorizes the bot identity through Wzgram/MTProto. It is not used for HTTP Bot API calls.
- `API_ID` and `API_HASH`: from https://my.telegram.org/apps.
- `TARGET_CHAT_ID`: destination channel username or numeric ID.
- `OWNER_ID` and `BACKUP_CHAT_ID`: optional owner and database-backup destinations.

## Important Android build dependency

Wzgram depends on WarpCrypto, a Rust/PyO3 native extension. The published WarpCrypto wheels currently list desktop platforms (Windows, macOS and manylinux), not Android. Chaquopy requires Android-compatible wheels for native extensions, so changing the Python engine to Wzgram is not by itself enough to produce a working Android APK. This branch updates the Android dependency declaration to Wzgram, but a matching Android WarpCrypto wheel must be built and bundled before the Chaquopy APK build can succeed. Do not treat an APK from an earlier Kurigram build as the Wzgram version.

References:
- [Wzgram installation and dependencies](https://wzgram.com/)
- [WarpCrypto wheels](https://pypi.org/project/WarpCrypto/)
- [Chaquopy Android wheel requirements](https://chaquo.com/chaquopy/doc/current/faq.html)

## Transfer behavior

Instagram media is downloaded locally with yt-dlp. Photos, videos, albums and database backup documents are sent with Wzgram. Telegram's own MTProto bot file-size limit still applies (about 2000 MiB per file); there are no application-level Bot API size thresholds or alternate upload transports.

The app retains the existing local SQLite database export/restore interface. Its backup files are ordinary files selected through Android's document picker.
