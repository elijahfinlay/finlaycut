#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="$ROOT_DIR/resources/bin"
ICON_PATH="$ROOT_DIR/resources/icons/icon.icns"
PYTHON_RUNTIME_ROOT="$ROOT_DIR/resources/python-runtime"
VENV_PYTHON="$ROOT_DIR/backend/.venv/bin/python3"

mkdir -p "$BIN_DIR"
mkdir -p "$PYTHON_RUNTIME_ROOT"

if [ ! -f "$ICON_PATH" ]; then
  echo "Missing app icon at $ICON_PATH" >&2
  exit 1
fi

FFMPEG_PATH="$(command -v ffmpeg || true)"
FFPROBE_PATH="$(command -v ffprobe || true)"

if [ -z "$FFMPEG_PATH" ] || [ -z "$FFPROBE_PATH" ]; then
  echo "ffmpeg and ffprobe must be available on PATH before packaging." >&2
  exit 1
fi

cp -fL "$FFMPEG_PATH" "$BIN_DIR/ffmpeg"
cp -fL "$FFPROBE_PATH" "$BIN_DIR/ffprobe"
chmod 755 "$BIN_DIR/ffmpeg" "$BIN_DIR/ffprobe"

HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  arm64) HOST_ARCH="arm64" ;;
  x86_64) HOST_ARCH="x64" ;;
esac

TARGET_ARCH="${FINLAYCUT_TARGET_ARCH:-$HOST_ARCH}"
if [ "$TARGET_ARCH" != "$HOST_ARCH" ]; then
  echo "Cross-arch packaging is not supported from this machine." >&2
  echo "Host arch: $HOST_ARCH, requested arch: $TARGET_ARCH" >&2
  exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
  echo "Missing backend virtualenv Python at $VENV_PYTHON" >&2
  exit 1
fi

PYTHON_BASE_PREFIX="$("$VENV_PYTHON" -c 'import pathlib, sys; print(pathlib.Path(sys.base_prefix).resolve())')"
PYTHON_VERSION="$("$VENV_PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_RUNTIME_DIR="$PYTHON_RUNTIME_ROOT/$PYTHON_VERSION"
PYTHON_EXECUTABLE="$PYTHON_RUNTIME_DIR/bin/python$PYTHON_VERSION"
PYTHON_RUNTIME_SITE_PACKAGES="$PYTHON_RUNTIME_DIR/lib/python$PYTHON_VERSION/site-packages"

rm -rf "$PYTHON_RUNTIME_DIR"
ditto "$PYTHON_BASE_PREFIX" "$PYTHON_RUNTIME_DIR"
rm -rf "$PYTHON_RUNTIME_SITE_PACKAGES"
mkdir -p "$PYTHON_RUNTIME_SITE_PACKAGES"
install_name_tool -change "$PYTHON_BASE_PREFIX/Python" "@executable_path/../Python" "$PYTHON_EXECUTABLE"
codesign --force --sign - "$PYTHON_RUNTIME_DIR/Python" "$PYTHON_EXECUTABLE"

echo "Bundled ffmpeg from $FFMPEG_PATH"
echo "Bundled ffprobe from $FFPROBE_PATH"
echo "Bundled Python runtime from $PYTHON_BASE_PREFIX"
