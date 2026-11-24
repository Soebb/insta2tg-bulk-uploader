# Gogram Android bridge

This package is the MTProto transfer layer used by the Android application.
It is intentionally a small gomobile-compatible facade: Java/Python callers
pass only strings and booleans, while Gogram owns the Telegram client,
authentication, uploads, album sends, and session persistence.

Build the Android archive from this directory:

```sh
go mod tidy
go install golang.org/x/mobile/cmd/gomobile@latest
go install golang.org/x/mobile/cmd/gobind@latest
gomobile init
gomobile bind -v -target=android/arm,android/arm64 -javapkg com.soebb.gogram -o ../../app/libs/gogram.aar .
```

The generated class is consumed by the Python engine through Chaquopy's Java
interop. The app keeps yt-dlp, queue management, SQLite progress tracking,
pause/resume/stop checkpoints, and the existing UI in their current layers.
