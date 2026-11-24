#!/bin/sh
# Encaminha para o pre-push.sh na raiz do plugin/repositório.
set -eu
SKILL_SUBDIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SKILL_SUBDIR/../.." && pwd)
[ -f "$PLUGIN_ROOT/pre-push.sh" ] || {
  echo "[doctor-deploy] pre-push.sh não encontrado na raiz do plugin: $PLUGIN_ROOT" >&2
  exit 1
}
exec "$PLUGIN_ROOT/pre-push.sh" "$@"
