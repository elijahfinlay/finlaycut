#!/bin/bash

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

export FINLAYCUT_LAUNCHER_DOUBLE_CLICK=1

exec "$ROOT_DIR/scripts/launch-local.sh"
