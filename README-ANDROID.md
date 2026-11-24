# Android app (on-device mode)

The Android app keeps the existing Java UI, foreground service, Chaquopy runtime,
yt-dlp Instagram downloader, queue handling, and SQLite progress database.

Telegram transfers are handled by **Gogram over MTProto**, compiled as an Android
AAR with the official Go mobile binding tool (gomobile bind). The generated
library is linked from app/libs/gogram.aar; it is not a Bot API HTTP uploader
and does not depend on Wzgram or WarpCrypto.

## Telegram credentials

- BOT_TOKEN: authorizes the bot through Gogram's MTProto LoginBot flow.
- API_ID and API_HASH: from https://my.telegram.org/apps.
- TARGET_CHAT_ID: destination channel username or numeric ID.
- OWNER_ID and BACKUP_CHAT_ID: optional owner and database-backup destinations.

## How the Android bridge works

1. Python/Chaquopy continues to download Instagram media with yt-dlp and manage
   the existing SQLite queue and pause/resume/stop checkpoints.
2. The Python engine calls the generated Java binding in
   com.soebb.gogram.Gogram.
3. The Go bridge owns the Gogram client, bot authentication, session file,
   single-media uploads, album uploads, and upload progress logging.
4. Java's existing foreground service and the app UI remain in place.

## Build the AAR

The GitHub Actions workflow builds and publishes the AAR as the
gogram-android-aar artifact for this branch. To build locally, install Go,
JDK 17, Android SDK and Android NDK, then run:

```sh
cd mobile/gogram
go mod tidy
go install golang.org/x/mobile/cmd/gomobile@latest
go install golang.org/x/mobile/cmd/gobind@latest
gomobile init
gomobile bind -v -target=android/arm,android/arm64 -androidapi 24 \
  -javapkg com.soebb.gogram -o ../../app/libs/gogram.aar .
```

After generating the AAR, open the Android project in Android Studio and build
the app. The configured Android ABIs are arm64-v8a and armeabi-v7a.

## Transfer behavior

Photos, videos, albums (2–10 items), and SQLite checkpoint backup documents are
sent through Gogram's MTProto media methods. The existing per-link progress
database, duplicate skipping, retry state, and app-private download folders
remain in the Python engine.
