#!/bin/sh
# doctor-deploy — hook de push. LF, POSIX, WSL2.
# Deleção de ref não zera o portão dos outros refs.
# Sem recibo, o portão roda no commit empurrado — não na working tree alheia.
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ ! -f "$SKILL_DIR/layers.py" ] && [ -f "$SKILL_DIR/../../layers.py" ]; then
  SKILL_DIR=$(CDPATH= cd -- "$SKILL_DIR/../.." && pwd)
fi
PYTHON=python3
"$PYTHON" -c '' >/dev/null 2>&1 || PYTHON=python
command -v "$PYTHON" >/dev/null 2>&1 || { echo "[doctor-deploy] python ausente" >&2; exit 1; }
export PYTHONUTF8=1

if [ "${DD_PLUGIN_FORWARDED:-0}" != 1 ]; then
  if cfg_root=$("$PYTHON" "$SKILL_DIR/layers.py" config-plugin-root); then
    if [ -n "$cfg_root" ]; then
      cfg_root=$(CDPATH= cd -- "$cfg_root" && pwd)
      if [ "$cfg_root" != "$SKILL_DIR" ]; then
        export DD_PLUGIN_FORWARDED=1
        exec "$cfg_root/pre-push.sh" "$@"
      fi
    fi
  else
    exit 1
  fi
fi

GATE=$SKILL_DIR/gate.sh
protected=${PUSH_GATE_BRANCHES:-}
if ! cfg_branches=$("$PYTHON" "$SKILL_DIR/layers.py" config-branches); then
  exit 1
fi
if [ -z "$protected" ]; then
  protected=$cfg_branches
fi
protected=${protected:-main,master}
zero=0000000000000000000000000000000000000000
fail=0

while read -r local_ref local_sha remote_ref remote_sha; do
  [ -n "${local_ref:-}" ] || continue
  # Deleção: pula só este ref. Não zera o portão de um push misto.
  if [ "$local_sha" = "$zero" ]; then
    continue
  fi
  branch=${remote_ref#refs/heads/}
  case ",$protected," in
    *",$branch,"*) ;;
    *) continue ;;
  esac
  # stdin fechado: o quality não pode engolir as linhas dos próximos refs.
  if ! "$GATE" hook-one "$local_sha" </dev/null; then
    fail=1
  fi
done

exit "$fail"
