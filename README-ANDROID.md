# Android app (on-device mode)

The Android app runs yt-dlp through Chaquopy and stores its working files in app-private storage. The Telegram transfer engine in this branch has been migrated to Wzgram MTProto: there is no HTTP Bot API upload path and no Kurigram fallback in the Python engine.

## Telegram credentials

- `BOT_TOKEN`: authorizes the bot identity through Wzgram/MTProto. It is not used for HTTP Bot API calls.
- `API_ID` and `API_HASH`: from https://my.telegram.org/apps.
- `TARGET_CHAT_ID`: destination channel username or numeric ID.
- `OWNER_ID` and `BACKUP_CHAT_ID`: optional owner and database-backup destinations.

## Android WarpCrypto integration

Wzgram requires WarpCrypto, a Rust/PyO3 native extension. The Android-compatible wheel is built separately for CPython 3.11 / Android API 24+ / arm64-v8a. The `build-wzgram-android.yml` workflow downloads that wheel from the successful wheel-build run into `app/wheels` before Chaquopy installs Wzgram and its dependencies.

The APK is currently arm64-v8a only because the bundled WarpCrypto wheel targets ARM64. It is suitable for 64-bit ARM Android devices such as the Samsung Galaxy A32 variant used for device testing. The APK must be built by the Wzgram Android workflow; an older Kurigram APK does not contain this integration.

References:
- [Wzgram installation and dependencies](https://wzgram.com/)
- [WarpCrypto wheels](https://pypi.org/project/WarpCrypto/)
- [Chaquopy Android wheel requirements](https://chaquo.com/chaquopy/doc/current/faq.html)

## Transfer behavior

Instagram media is downloaded locally with yt-dlp. Photos, videos, albums and database backup documents are sent with Wzgram. Telegram's own MTProto bot file-size limit still applies (about 2000 MiB per file); there are no application-level Bot API size thresholds or alternate upload transports.

The app retains the existing local SQLite database export/restore interface. Its backup files are ordinary files selected through Android's document picker.
