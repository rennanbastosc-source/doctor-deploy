#!/bin/sh
# Encaminha para o gate.sh na raiz do plugin/repositório.
set -eu
SKILL_SUBDIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SKILL_SUBDIR/../.." && pwd)
[ -f "$PLUGIN_ROOT/gate.sh" ] || {
  echo "[doctor-deploy] gate.sh não encontrado na raiz do plugin: $PLUGIN_ROOT" >&2
  exit 1
}
exec "$PLUGIN_ROOT/gate.sh" "$@"
