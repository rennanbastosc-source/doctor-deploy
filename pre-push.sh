#!/bin/sh
# doctor-deploy — hook de push. LF, POSIX, WSL2.
# Deleção de ref não zera o portão dos outros refs.
# Sem recibo, o portão roda no commit empurrado — não na working tree alheia.
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
GATE=$SKILL_DIR/gate.sh
protected=${PUSH_GATE_BRANCHES:-main,master}
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
  if ! "$GATE" hook-one "$local_sha"; then
    fail=1
  fi
done

exit "$fail"
