# Android Go Engine Migration

Target branch: `go-native-android-engine`
Base branch: `gogram-android-transfers`

## Non-negotiable target

- Keep the existing Android UI in Java; do not redesign or rewrite screens.
- Run the Instagram extraction/download engine in Go using `github.com/tejasa97/ytdlp-go`.
- Use Gogram for Telegram MTProto communication and uploads.
- Move queue scheduling, download concurrency, pause/resume/stop, retry and recovery into Go.
- Use SQLite from Go as the durable source of truth for jobs and media items.
- Expose a small, Java-friendly API through gomobile and ship it as an Android AAR.
- Remove Python and Chaquopy from the Android app and its Android build/runtime path.

## Migration rules

1. Preserve the current branch as the baseline; all work for this migration stays on this branch.
2. Keep the Java UI contract stable. Replace its Python bridge behind the existing UI-facing Java methods where possible.
3. Do not delete server-side Python files merely because the Android runtime is becoming Python-free. They are a separate deployment target unless explicitly brought into scope.
4. Do not claim feature parity until the relevant Instagram fixtures and device tests pass.
5. Do not remove the old Android Python implementation until the Go path builds, installs, downloads, uploads, and survives process restart on a real device.

## Proposed Go module layout

```
mobile/
  androidengine/
    go.mod
    bridge/       # gomobile-exported API; primitive/string/JSON boundary only
    app/          # lifecycle and orchestration
    queue/        # scheduling, concurrency, cancellation
    downloader/   # ytdlp-go adapter and Instagram media normalization
    telegram/     # Gogram client, auth and upload/album splitting
    store/        # SQLite schema, migrations and repositories
    recovery/     # restart reconciliation and resumable job state
```

The exact package boundaries may be adjusted after inspecting the public embedding API and Android constraints of the selected dependency.

## Implementation stages and acceptance gates

### 0. Dependency feasibility spike
- Pin a reviewed `ytdlp-go` version/commit.
- Verify its public Go embedding API can be compiled for Android ARM and ARM64 with gomobile.
- Verify Instagram support for reels, single posts, mixed carousels, photos and videos. If a capability is absent, record it before replacing the current engine.
- Identify and package the required FFmpeg/FFprobe and JavaScript-helper runtime dependencies for Android, or prove they are not needed for the Instagram paths used by this app.

### 1. Go foundation
- Create the Android Go module and gomobile bridge.
- Add SQLite schema/migrations and deterministic queue tests.
- Add cancellation-safe operation handles and structured events.
- Build and publish a test AAR artifact.

### 2. Instagram adapter
- Implement extraction and download through the public `ytdlp-go` API.
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
- Remove Chaquopy Gradle configuration, Python runtime assets, Python engine files used by Android, and related build steps.
- Keep unrelated server-side Python entry points unless their removal is separately requested.
- Run Go tests, Android Gradle build, AAR ABI checks and device acceptance tests.
- Only declare migration complete after a real-device end-to-end test.

## Dependency risk noted at kickoff

The upstream project describes itself as alpha and explicitly says it is not a drop-in replacement for yt-dlp; its extractor catalog is smaller. Its published documentation lists Go 1.25.12+, FFmpeg/FFprobe for certain media operations, and a colocated `ytdlp-js-helper` for supported JavaScript challenge flows. These are feasibility gates, not assumptions that all are required for this app.

## Current status

- [x] Created this branch from `gogram-android-transfers`.
- [ ] Verify the upstream Instagram extractor and public embedding API against this app's required URL/media cases.
- [ ] Build the first Android-compatible Go module and AAR.
- [ ] Port queue, SQLite, Telegram, and recovery.
- [ ] Switch Java bridge and remove Android Python/Chaquopy.
