# WarpCrypto Android Device Test

This standalone Android app verifies that the locally built WarpCrypto Android wheel can be installed by Chaquopy and loaded on an ARM64 Android device.

The app runs:
- AES-256-IGE encrypt/decrypt round-trip
- MTProto 2.0 KDF output-size checks

The GitHub Actions workflow downloads the wheel from the successful wheel-build run, builds a debug APK, and publishes it as an artifact.
