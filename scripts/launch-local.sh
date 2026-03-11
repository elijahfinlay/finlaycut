#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
VENV_DIR="$BACKEND_DIR/.venv"
LOCK_DIR="${TMPDIR:-/tmp}/finlaycut-local-launch"
PID_FILE="$LOCK_DIR/pid"
REQUIREMENTS_FILE="$BACKEND_DIR/requirements.txt"
REQUIREMENTS_HASH_FILE="$VENV_DIR/.finlaycut-launcher-requirements.sha256"
LAUNCHER_ENV_FILE="$ROOT_DIR/.env.launcher"
DOUBLE_CLICK_MODE="${FINLAYCUT_LAUNCHER_DOUBLE_CLICK:-0}"
CHECK_ONLY=0

if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

handle_exit() {
  local exit_code="$1"

  if [[ -f "$PID_FILE" ]] && [[ "$(cat "$PID_FILE" 2>/dev/null)" == "$$" ]]; then
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi

  if [[ "$exit_code" -ne 0 && "$DOUBLE_CLICK_MODE" == "1" ]]; then
    echo
    read -r -p "Launcher failed. Press Enter to close..." _
  fi
}

trap 'handle_exit $?' EXIT

log() {
  printf '[FinlayCut launcher] %s\n' "$1"
}

fail() {
  printf '[FinlayCut launcher] %s\n' "$1" >&2
  exit 1
}

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "Missing required command: $command_name"
  fi
}

load_launcher_env() {
  if [[ -f "$LAUNCHER_ENV_FILE" ]]; then
    log "Loading overrides from $LAUNCHER_ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$LAUNCHER_ENV_FILE"
    set +a
  fi
}

set_default_env() {
  local default_resolve_api="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
  local default_resolve_lib="/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
  local detected_ffmpeg=""
  local detected_ffprobe=""

  if [[ -z "${RESOLVE_SCRIPT_API:-}" ]] && [[ -d "$default_resolve_api" ]]; then
    export RESOLVE_SCRIPT_API="$default_resolve_api"
  fi

  if [[ -z "${RESOLVE_SCRIPT_LIB:-}" ]] && [[ -f "$default_resolve_lib" ]]; then
    export RESOLVE_SCRIPT_LIB="$default_resolve_lib"
  fi

  if [[ -z "${FINLAYCUT_FFMPEG_PATH:-}" ]]; then
    detected_ffmpeg="$(command -v ffmpeg || true)"
    if [[ -n "$detected_ffmpeg" ]]; then
      export FINLAYCUT_FFMPEG_PATH="$detected_ffmpeg"
    fi
  fi

  if [[ -z "${FINLAYCUT_FFPROBE_PATH:-}" ]]; then
    detected_ffprobe="$(command -v ffprobe || true)"
    if [[ -n "$detected_ffprobe" ]]; then
      export FINLAYCUT_FFPROBE_PATH="$detected_ffprobe"
    fi
  fi
}

ensure_single_launcher() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$PID_FILE"
    return
  fi

  if [[ -f "$PID_FILE" ]]; then
    local existing_pid=""
    existing_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ "$existing_pid" =~ ^[0-9]+$ ]] && kill -0 "$existing_pid" 2>/dev/null; then
      log "Launcher already running in PID $existing_pid. Reusing the existing app session."
      exit 0
    fi
  fi

  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
  mkdir "$LOCK_DIR"
  printf '%s\n' "$$" > "$PID_FILE"
}

find_system_python() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi

  fail "Python 3 is required to launch FinlayCut locally."
}

find_venv_python() {
  if [[ -x "$VENV_DIR/bin/python3" ]]; then
    printf '%s\n' "$VENV_DIR/bin/python3"
    return
  fi

  if [[ -x "$VENV_DIR/bin/python" ]]; then
    printf '%s\n' "$VENV_DIR/bin/python"
    return
  fi

  printf '\n'
}

hash_requirements() {
  shasum -a 256 "$REQUIREMENTS_FILE" | awk '{ print $1 }'
}

ensure_node_dependencies() {
  if [[ ! -d "$ROOT_DIR/node_modules" ]]; then
    log "Installing Node dependencies..."
    (cd "$ROOT_DIR" && npm install)
  fi
}

ensure_backend_environment() {
  local system_python=""
  local venv_python=""
  local current_hash=""
  local installed_hash=""
  local pip_env=(
    "PIP_DISABLE_PIP_VERSION_CHECK=1"
    "PIP_NO_INPUT=1"
  )

  system_python="$(find_system_python)"
  venv_python="$(find_venv_python)"

  if [[ -z "$venv_python" ]]; then
    log "Creating backend virtual environment..."
    "$system_python" -m venv "$VENV_DIR"
    venv_python="$(find_venv_python)"
  fi

  if [[ -z "$venv_python" ]]; then
    fail "Could not locate the backend virtual environment Python."
  fi

  current_hash="$(hash_requirements)"
  if [[ -f "$REQUIREMENTS_HASH_FILE" ]]; then
    installed_hash="$(cat "$REQUIREMENTS_HASH_FILE")"
  fi

  if [[ "$installed_hash" != "$current_hash" ]]; then
    log "Syncing backend Python dependencies..."
    env "${pip_env[@]}" "$venv_python" -m pip install -r "$REQUIREMENTS_FILE"
    printf '%s\n' "$current_hash" > "$REQUIREMENTS_HASH_FILE"
  fi
}

main() {
  require_command npm
  require_command shasum

  load_launcher_env
  set_default_env
  ensure_single_launcher
  ensure_node_dependencies
  ensure_backend_environment

  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    log "Launcher preflight passed."
    return
  fi

  log "Starting FinlayCut..."
  cd "$ROOT_DIR"
  npm run dev
}

main "$@"
