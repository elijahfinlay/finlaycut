# FinlayCut Distribution

## Build outputs

- Apple Silicon Mac zip: `dist/FinlayCut-0.1.0-arm64.zip`
- Intel Mac zip: `dist/FinlayCut-0.1.0-x64.zip`

These zip files contain `FinlayCut.app` and are the simplest way to move the app to another Mac.

## Build commands

- Apple Silicon app bundle: `npm run build:mac:arm64:dir`
- Intel app bundle: `npm run build:mac:x64:dir`
- Default mac package build: `npm run build:mac`

Before packaging, FinlayCut copies the local `ffmpeg` and `ffprobe` binaries into `resources/bin` and requires `resources/icons/icon.icns` to exist.
The target architecture must match the build machine architecture unless you provide matching native runtime assets for that target.

## Installing on another Mac

1. Copy the correct zip file to the target Mac.
2. Unzip it.
3. Move `FinlayCut.app` into `/Applications`.
4. Launch it with right-click > `Open` the first time.

If Gatekeeper blocks it, run:

```bash
xattr -dr com.apple.quarantine /Applications/FinlayCut.app
open /Applications/FinlayCut.app
```

## Current limitations

- The app is not signed or notarized in this repository by default.
- A `Developer ID Application` certificate is required on the build machine to sign it.
- Notarization also requires Apple credentials and should be done as part of the release process.
