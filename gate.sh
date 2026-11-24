#!/bin/sh
# doctor-deploy — um comando para a skill e para o hook.
# /bin/sh (no Windows, o do Git for Windows), LF, Python para JSON e camadas. Sem Python o recibo não é aceito.
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PY=$SKILL_DIR/layers.py
# No Windows, `python3` pode ser o atalho da Microsoft Store: está no PATH e não roda.
PYTHON=python3
"$PYTHON" -c '' >/dev/null 2>&1 || PYTHON=python
# UTF-8 em stdout e open(), como no Linux (no Windows o padrão é cp1252).
export PYTHONUTF8=1

die() { echo "[doctor-deploy] $*" >&2; exit 1; }

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "não é um repositório git"

cmd=${1:-}
shift || true

tree_clean() {
  [ -z "$(git status --porcelain)" ]
}

# Imprime o comando do entrypoint de quality da raiz, ou nada. Não roda.
quality_entry() {
  if [ -f package.json ] && command -v "$PYTHON" >/dev/null 2>&1; then
    q=$("$PYTHON" -c'import json; print(json.load(open("package.json")).get("scripts", {}).get("quality", ""))')
    if [ -n "$q" ]; then
      pm=npm
      if [ -f pnpm-lock.yaml ]; then pm=pnpm; fi
      if [ -f yarn.lock ]; then pm=yarn; fi
      if [ -f bun.lock ] || [ -f bun.lockb ]; then pm=bun; fi
      echo "$pm run quality"
      return
    fi
  fi
  if [ -f Makefile ] && grep -q '^quality:' Makefile; then
    echo "make quality"
    return
  fi
  if [ -f scripts/quality.py ]; then
    echo "$PYTHON scripts/quality.py"
  fi
}

run_quality() {
  root=$(git rev-parse --show-toplevel)
  cd "$root"
  q=$(quality_entry)
  if [ -n "$q" ]; then
    echo "[doctor-deploy] quality: $q" >&2
    # Sem aspas de propósito: o comando é "gerenciador run quality" ou "python scripts/quality.py".
    $q
    return $?
  fi
  echo "[doctor-deploy] nenhum entrypoint (npm/pnpm/bun/yarn quality, make quality, scripts/quality.py) — quality pulado" >&2
  # Variável, não código de saída: make sai 2 e um script pode sair com qualquer código.
  quality_skipped=1
  return 0
}

case "$cmd" in
  tree-clean)
    tree_clean
    ;;
  print-skipped)
    "$PYTHON" "$PY" print-skipped
    ;;
  check)
    [ -n "${1:-}" ] || die "check exige um SHA"
    "$PYTHON" "$PY" check "$1"
    ;;
  needs-consent)
    "$PYTHON" "$PY" needs-consent
    ;;
  consent)
    [ -n "${1:-}" ] && [ -n "${2:-}" ] || die "consent <lizard|mutation> <yes|no>"
    "$PYTHON" "$PY" consent "$1" "$2"
    ;;
  preflight)
    # Confere o repo atual sem rodar nada. Só a falta de quality reprova; o resto vira skip no portão.
    cd "$(git rev-parse --show-toplevel)"
    red=0
    q=$(quality_entry)
    if [ -n "$q" ]; then
      echo "[doctor-deploy] ok: quality = $q" >&2
    elif [ "$(git config --bool --get doctor-deploy.allowNoQuality || true)" = true ]; then
      echo "[doctor-deploy] aviso: sem entrypoint de quality (liberado por doctor-deploy.allowNoQuality)" >&2
    else
      echo "[doctor-deploy] VERMELHO: sem entrypoint de quality (npm/pnpm/bun/yarn quality, make quality, scripts/quality.py)" >&2
      red=1
    fi
    hook=$(git rev-parse --git-common-dir)/hooks/pre-push
    if [ ! -e "$hook" ]; then
      echo "[doctor-deploy] aviso: sem hook pre-push (gate.sh install-hook)" >&2
    elif ! grep -qF "$SKILL_DIR/pre-push.sh" "$hook"; then
      echo "[doctor-deploy] aviso: o pre-push é do projeto, não o shim da skill; ele não consulta o recibo" >&2
    else
      echo "[doctor-deploy] ok: hook pre-push é o shim da skill" >&2
    fi
    "$PYTHON" "$PY" preflight
    exit "$red"
    ;;
  install-hook)
    common=$(git rev-parse --git-common-dir)
    hook=$common/hooks/pre-push
    if [ -e "$hook" ]; then
      echo "[doctor-deploy] hook já existe, não sobrescrevo: $hook" >&2
      exit 0
    fi
    mkdir -p "$common/hooks"
    printf '#!/bin/sh\nexec "%s"\n' "$SKILL_DIR/pre-push.sh" > "$hook"
    chmod +x "$hook"
    echo "[doctor-deploy] hook instalado: $hook" >&2
    ;;
  prepare)
    tree_clean || die "tree suja (inclui untracked) — recibo recusado"
    command -v "$PYTHON" >/dev/null 2>&1 || die "python ausente"
    started=$(date +%s)
    quality_skipped=0
    set +e
    run_quality
    qstatus=$?
    set -e
    if [ "$qstatus" -ne 0 ]; then
      die "GATE RED no quality (saída $qstatus)"
    fi
    if [ "$quality_skipped" -eq 1 ] && [ "$(git config --bool --get doctor-deploy.allowNoQuality || true)" != true ]; then
      die "GATE RED: sem entrypoint de quality. Crie um, ou libere este clone: git config doctor-deploy.allowNoQuality true"
    fi
    set +e
    layers_json=$("$PYTHON" "$PY" layers --started "$started")
    lstatus=$?
    set -e
    printf '%s\n' "$layers_json"
    if [ "$lstatus" -ne 0 ]; then
      die "GATE RED nas camadas do diff"
    fi
    "$PYTHON" "$PY" write-receipt --quality-skipped "$quality_skipped" --layers "$layers_json"
    "$PYTHON" "$PY" print-skipped
    ;;
  hook-one)
    sha=${1:-}
    [ -n "$sha" ] || die "hook-one exige o local_sha"
    if "$PYTHON" "$PY" check "$sha"; then
      "$PYTHON" "$PY" print-skipped
      exit 0
    fi
    head=$(git rev-parse HEAD)
    if [ "$head" != "$sha" ] || ! tree_clean; then
      die "não certifico $sha (HEAD diferente ou tree suja). Rode o preparo nesse commit."
    fi
    "$0" prepare
    ;;
  *)
    die "uso: preflight | prepare | check <sha> | hook-one <sha> | print-skipped | tree-clean | install-hook | needs-consent | consent <tool> <yes|no>"
    ;;
esac
