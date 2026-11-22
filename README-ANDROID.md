# Android app (on-device mode)

This branch starts a standalone Android application. The app is intended to run yt-dlp on the phone through Chaquopy; it does not depend on a cloud worker.

## Current foundation

- Settings screen for Telegram credentials and destination chat.
- Import of a local `links.txt` using Android's document picker.
- Foreground service scaffold for long-running work.
- Embedded Python runtime with yt-dlp and a persistent app-private working directory.
- GitHub Actions workflow which builds and uploads a debug APK artifact.

The current first milestone validates the Android/Python integration and APK build. The full queue, Telegram Bot API transfer, SQLite checkpoints, backup/restore, pause/resume and media cleanup still need to be ported and tested before this app is ready for a real 6,000-link run.

## Configuration

Required: `BOT_TOKEN`, `API_ID`, `API_HASH`, `TARGET_CHAT_ID`.

Optional: `OWNER_ID`, `BACKUP_CHAT_ID`.

Values are stored in app-private preferences on the device. Do not share the APK with credentials saved in it.

## Build

Push to `android-native-downloader` or run the **Android Debug APK** workflow manually. Download the `instagram-bulk-downloader-debug` artifact from the Actions run.

## Compatibility

Chaquopy requires Android API 24 or later. The app currently targets Android 12+ behavior and includes ARM ABIs for common phones. Network, battery and foreground-service restrictions vary by Android version and device vendor.
