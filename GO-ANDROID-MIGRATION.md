# Android Go Engine Migration

Target branch: `go-native-android-engine`
Base branch: `gogram-android-transfers`

## Non-negotiable target

- Keep the existing Android UI in Java; do not redesign or rewrite screens.
- Evaluate `github.com/lrstanley/go-ytdlp` as the yt-dlp integration layer for Instagram extraction/download.
- Use Gogram for Telegram MTProto communication and uploads.
- Move queue scheduling, download concurrency, pause/resume/stop, retry and recovery into Go.
- Use SQLite from Go as the durable source of truth for jobs and media items.
- Expose a small, Java-friendly API through gomobile and ship it as an Android AAR.
- Remove Python and Chaquopy from the Android app and its Android build/runtime path, if the downloader feasibility gate can be met.

## Important dependency finding — do not assume this is a pure-Go downloader

`go-ytdlp` is a Go wrapper/binding for the **yt-dlp command-line executable**, not a native Go reimplementation of yt-dlp. Its command execution uses Go's `os/exec` / `exec.CommandContext` to launch an external executable. Its installer selects binaries for a limited list of desktop/server OS/architectures; Android is not listed. The upstream module currently declares Go 1.27.

Consequences:
- Compiling the wrapper into an AAR does not embed yt-dlp itself.
- The normal upstream installer cannot provision an Android yt-dlp executable from its supported target list.
- The project may still be useful if we can provide a compatible Android executable and all required runtime helpers, but that must be proven on-device.
- Since yt-dlp itself is Python-based, this route does **not** automatically satisfy the requirement to remove Python/Chaquopy. We must not silently reintroduce a Python runtime or claim a Python-free engine.
- FFmpeg/FFprobe and a JavaScript runtime may also be needed for some extraction/download paths; verify which are actually required for our Instagram cases.

References:
- https://github.com/lrstanley/go-ytdlp
- https://github.com/lrstanley/go-ytdlp/blob/master/command.go
- https://github.com/lrstanley/go-ytdlp/blob/master/install_ytdlp.go

## Migration rules

1. Keep all migration work on this branch; preserve the base branch.
2. Keep the Java UI contract stable. Replace its backend behind existing Java-facing methods where possible.
3. Do not delete server-side Python files merely because the Android runtime is becoming Python-free. They are a separate deployment target unless explicitly brought into scope.
4. Do not claim feature parity until the relevant Instagram fixtures and device tests pass.
5. Do not remove the old Android Python implementation until the Go path builds, installs, downloads, uploads, and survives process restart on a real device.
6. First run a feasibility spike for go-ytdlp on Android. If it requires an unsupported external runtime, stop and report the blocker before investing in the full port.

## Proposed Go module layout

```
mobile/
  androidengine/
    go.mod
    bridge/       # gomobile-exported API; primitive/string/JSON boundary only
    app/          # lifecycle and orchestration
    queue/        # scheduling, concurrency, cancellation
    downloader/   # go-ytdlp adapter; executable/runtime integration isolated here
    telegram/     # Gogram client, auth and upload/album splitting
    store/        # SQLite schema, migrations and repositories
    recovery/     # restart reconciliation and resumable job state
```

The downloader package must isolate the executable/runtime dependency so a different backend can be substituted without changing queue, database, Telegram or Java APIs.

## Implementation stages and acceptance gates

### 0. go-ytdlp Android feasibility spike
- Inspect and pin a reviewed go-ytdlp version compatible with the Android build toolchain. Check its declared Go version before changing CI.
- Verify the wrapper itself compiles for Android ARM/ARM64 with gomobile.
- Determine whether a yt-dlp executable can legally and technically run on Android without Python/Chaquopy.
- Verify required JavaScript runtime and FFmpeg/FFprobe dependencies for Instagram.
- Test actual Instagram Reel, single post, photo, video and mixed carousel URLs on a physical Android device.
- If the executable/runtime cannot be supplied without violating the Python-free requirement, stop here and present alternatives rather than pretending the wrapper is a native Go downloader.

### 1. Go foundation
- Create the Android Go module and gomobile bridge.
- Add SQLite schema/migrations and deterministic queue tests.
- Add cancellation-safe operation handles and structured events.
- Build and publish a test AAR artifact.

### 2. Instagram adapter
- Implement extraction and download through the verified go-ytdlp execution strategy.
- Normalize posts and carousel entries into ordered media records.
- Preserve quality selection/fallback and safe temporary-file handling.
- Add fixtures/tests for single video, reel, image post and mixed carousel.

### 3. Telegram and orchestration
- Integrate Gogram login/session lifecycle and uploads.
- Preserve media ordering and split albums when Telegram album limits require it.
- Add bounded concurrency, retry/backoff, pause/resume/stop and durable checkpoints.

### 4. Java integration
- Replace the Android Python/Chaquopy bridge with the AAR API.
- Keep Java screens and user-visible behavior unchanged.
- Route progress/state/error events from Go to Java without exposing Go channels or complex types through gomobile.

### 5. Removal and verification
- Remove Chaquopy Gradle configuration, Python runtime assets, Python engine files used by Android, and related build steps only after the Go path is proven.
- Keep unrelated server-side Python entry points unless their removal is separately requested.
- Run Go tests, Android Gradle build, AAR ABI checks and device acceptance tests.
- Only declare migration complete after a real-device end-to-end test.

## Current status

- [x] Created this branch from `gogram-android-transfers`.
- [x] Corrected the downloader dependency to `github.com/lrstanley/go-ytdlp`.
- [x] Inspected upstream README, command execution and installer implementation.
- [ ] Prove Android-compatible yt-dlp execution while meeting the Python-free requirement.
- [ ] Build the first Android-compatible Go module and AAR.
- [ ] Port queue, SQLite, Telegram, and recovery.
- [ ] Switch Java bridge and remove Android Python/Chaquopy.
