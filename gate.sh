#!/bin/sh
# doctor-deploy — um comando para a skill e para o hook.
# /bin/sh (no Windows, o do Git for Windows), LF, Python para JSON e camadas. Sem Python o recibo não é aceito.
set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
if [ ! -f "$SKILL_DIR/layers.py" ] && [ -f "$SKILL_DIR/../../layers.py" ]; then
  SKILL_DIR=$(CDPATH= cd -- "$SKILL_DIR/../.." && pwd)
fi
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

case "$cmd" in
  preflight|install-hook|cc|integration|prepare|ci|check|hook-one)
    command -v "$PYTHON" >/dev/null 2>&1 || die "python ausente"
    if [ "${DD_PLUGIN_FORWARDED:-0}" != 1 ]; then
      # config-plugin-root valida .doctor-deploy.toml e resolve [plugin] root numa única chamada.
      cfg_root=$("$PYTHON" "$PY" config-plugin-root) || exit 1
      if [ -n "$cfg_root" ]; then
        cfg_root=$(CDPATH= cd -- "$cfg_root" && pwd)
        if [ "$cfg_root" != "$SKILL_DIR" ]; then
          export DD_PLUGIN_FORWARDED=1
          exec "$cfg_root/gate.sh" "$cmd" "$@"
        fi
      fi
    fi
    ;;
esac

tree_clean() {
  [ -z "$(git status --porcelain)" ]
}

# Imprime o comando do entrypoint de quality da raiz, ou nada. Não roda.
quality_entry() {
  root=$(git rev-parse --show-toplevel)
  (
    cd "$root"
    if { [ -f .doctor-deploy.toml ] || [ -f doctor-deploy.toml ]; } && command -v "$PYTHON" >/dev/null 2>&1; then
      q=$("$PYTHON" "$PY" config-quality)
      if [ -n "$q" ]; then
        echo "$q"
        return
      fi
    fi
    if [ -f package.json ] && command -v "$PYTHON" >/dev/null 2>&1; then
      q=$("$PYTHON" -c'
import json, sys
try:
    print(json.load(open("package.json", encoding="utf-8")).get("scripts", {}).get("quality", ""))
except (OSError, ValueError, AttributeError) as exc:
    sys.exit(f"[doctor-deploy] package.json ilegível: {exc}")
')
      if [ -n "$q" ]; then
        pm=npm
        if [ -f pnpm-lock.yaml ]; then pm=pnpm; fi
        if [ -f yarn.lock ]; then pm=yarn; fi
        if [ -f bun.lock ] || [ -f bun.lockb ]; then pm=bun; fi
        echo "$pm run quality"
        return
      fi
    fi
    has_make=0
    if [ -f Makefile ] && grep -q '^quality:' Makefile; then
      if command -v make >/dev/null 2>&1; then
        echo "make quality"
        return
      fi
      has_make=1
    fi
    # Agnóstico: Go, Rails e o que mais vier sem make (Windows) usam um script POSIX.
    if [ -f scripts/quality.sh ]; then
      echo "sh scripts/quality.sh"
    elif [ -f scripts/quality.py ]; then
      echo "$PYTHON scripts/quality.py"
    elif [ "$has_make" = 1 ]; then
      # Sem make instalado: falha alto (127) em vez de virar "sem entrypoint".
      echo "make quality"
    fi
  )
}

# Shim do pre-push. Acha a skill na hora do push: o plugin do Claude Code muda de pasta a cada versão.
# Ordem: $DOCTOR_DEPLOY_HOME, a pasta de quando o hook foi instalado, o clone em ~/.agents/skills,
# o plugin do Antigravity e o plugin mais novo em ~/.claude/plugins/cache. Sem skill: bloqueia só a branch protegida.
write_shim() {
  cat > "$1" <<EOF
#!/bin/sh
# doctor-deploy: shim
for d in "\${DOCTOR_DEPLOY_HOME:-}" "$SKILL_DIR" "\$HOME/.agents/skills/doctor-deploy" "\$HOME/.gemini/config/plugins/doctor-deploy/skills/doctor-deploy" "\$HOME/.gemini/config/plugins/doctor-deploy"; do
  [ -n "\$d" ] && [ -f "\$d/pre-push.sh" ] && exec "\$d/pre-push.sh" "\$@"
done
best=
for d in "\$HOME"/.claude/plugins/cache/*/doctor-deploy/*; do
  if [ -f "\$d/pre-push.sh" ]; then
    cand=\$d/pre-push.sh
  elif [ -f "\$d/skills/doctor-deploy/pre-push.sh" ]; then
    cand=\$d/skills/doctor-deploy/pre-push.sh
  else
    continue
  fi
  if [ -z "\$best" ] || [ "\$cand" -nt "\$best" ]; then best=\$cand; fi
done
[ -n "\$best" ] && exec "\$best" "\$@"
branches=",\${PUSH_GATE_BRANCHES:-main,master},"
while read -r _l _ls remote _rs; do
  case "\$branches" in *",\${remote#refs/heads/},"*)
    echo "[doctor-deploy] skill não encontrada (DOCTOR_DEPLOY_HOME, ~/.agents/skills/doctor-deploy ou plugin); push para \$remote bloqueado" >&2
    exit 1 ;;
  esac
done
exit 0
EOF
}

# O projeto tem hook próprio (scripts/pre-push.sh) que delega à skill.
# Rodar na raiz do repo.
project_hook_delegates() {
  [ -f scripts/pre-push.sh ] && grep -q 'doctor-deploy/pre-push.sh' scripts/pre-push.sh
}

run_quality() {
  root=$(git rev-parse --show-toplevel)
  cd "$root"
  q=$(quality_entry)
  if [ -n "$q" ]; then
    echo "[doctor-deploy] quality: $q" >&2
    # Sem aspas de propósito: o comando é "gerenciador run quality", "sh scripts/quality.sh" etc.
    $q
    return $?
  fi
  echo "[doctor-deploy] nenhum entrypoint (npm/pnpm/bun/yarn quality, make quality, scripts/quality.sh, scripts/quality.py) — quality pulado" >&2
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
  integration)
    # Antes de escrever os testes: módulos tocados pelo diff sem teste de integração.
    "$PYTHON" "$PY" integration
    ;;
  check)
    [ -n "${1:-}" ] || die "check exige um SHA"
    "$PYTHON" "$PY" check "$1"
    ;;
  cc)
    # Antes de escrever os testes: CC das funções do diff, inclusive o que ainda não foi commitado.
    "$PYTHON" "$PY" cc
    ;;
  needs-consent)
    "$PYTHON" "$PY" needs-consent
    ;;
  consent)
    [ -n "${1:-}" ] && [ -n "${2:-}" ] || die "consent <lizard|mutation> <yes|no>"
    "$PYTHON" "$PY" consent "$1" "$2"
    ;;
  discover)
    cd "$(git rev-parse --show-toplevel)"
    set +e
    q=$(quality_entry 2>&1)
    qok=$?
    set -e
    if [ "$qok" -eq 0 ]; then
      "$PYTHON" "$PY" discover --quality "$q" "$@"
    else
      "$PYTHON" "$PY" discover --quality "" --quality-error "$q" "$@"
    fi
    ;;
  init)
    cd "$(git rev-parse --show-toplevel)"
    command -v "$PYTHON" >/dev/null 2>&1 || die "python ausente"
    "$PYTHON" "$PY" init "$@"
    ;;
  preflight)
    # Confere o repo atual sem rodar nada. Só a falta de quality reprova; o resto vira skip no portão.
    cd "$(git rev-parse --show-toplevel)"
    red=0
    q=$(quality_entry)
    if [ -n "$q" ]; then
      if [ "$q" = "make quality" ] && ! command -v make >/dev/null 2>&1; then
        echo "[doctor-deploy] VERMELHO: Makefile tem quality, mas make não está instalado; instale-o ou crie scripts/quality.sh" >&2
        red=1
      else
        echo "[doctor-deploy] ok: quality = $q" >&2
      fi
    elif [ "$(git config --bool --get doctor-deploy.allowNoQuality || true)" = true ]; then
      echo "[doctor-deploy] aviso: sem entrypoint de quality (liberado por doctor-deploy.allowNoQuality)" >&2
    else
      echo "[doctor-deploy] VERMELHO: sem entrypoint de quality (npm/pnpm/bun/yarn quality, make quality, scripts/quality.sh, scripts/quality.py)" >&2
      red=1
    fi
    # --git-path respeita core.hooksPath (husky e afins): é a pasta que o git de fato lê.
    hook=$(git rev-parse --git-path hooks)/pre-push
    hooks_path=$(git config --get core.hooksPath || true)
    if [ ! -e "$hook" ]; then
      echo "[doctor-deploy] aviso: sem hook pre-push em $hook (gate.sh install-hook)" >&2
    elif project_hook_delegates; then
      if grep -q 'scripts/pre-push.sh' "$hook"; then
        echo "[doctor-deploy] ok: hook pre-push -> scripts/pre-push.sh -> skill" >&2
      else
        echo "[doctor-deploy] aviso: o hook pre-push pula o scripts/pre-push.sh do projeto (apague $hook e rode gate.sh install-hook)" >&2
      fi
    elif grep -q "doctor-deploy: shim" "$hook" || grep -qF "$SKILL_DIR/pre-push.sh" "$hook" || grep -q 'doctor-deploy/pre-push.sh' "$hook"; then
      echo "[doctor-deploy] ok: hook pre-push é o shim da skill" >&2
    elif [ -n "$hooks_path" ]; then
      echo "[doctor-deploy] aviso: core.hooksPath=$hooks_path e o pre-push de lá não cita a skill; confira que ele delega a ela (SETUP.md §5)" >&2
    else
      echo "[doctor-deploy] aviso: o pre-push é do projeto e não delega à skill; ele não consulta o recibo" >&2
    fi
    "$PYTHON" "$PY" preflight
    exit "$red"
    ;;
  install-hook)
    cd "$(git rev-parse --show-toplevel)"
    hooks=$(git rev-parse --git-path hooks)
    hook=$hooks/pre-push
    if [ -e "$hook" ]; then
      echo "[doctor-deploy] hook já existe, não sobrescrevo: $hook" >&2
      exit 0
    fi
    mkdir -p "$hooks"
    if project_hook_delegates; then
      # Hook do projeto primeiro: ele faz os passos próprios e delega à skill. Raiz resolvida por worktree.
      printf '#!/bin/sh\nexec "$(git rev-parse --show-toplevel)/scripts/pre-push.sh"\n' > "$hook"
    else
      write_shim "$hook"
    fi
    chmod +x "$hook"
    echo "[doctor-deploy] hook instalado: $hook" >&2
    ;;
  prepare)
    tree_clean || die "tree suja (inclui untracked) — recibo recusado"
    command -v "$PYTHON" >/dev/null 2>&1 || die "python ausente"
    tested=$(git rev-parse HEAD)
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
    # O recibo é do commit testado: quality que reescreve arquivo ou commit no meio do caminho invalidam.
    [ "$(git rev-parse HEAD)" = "$tested" ] || die "HEAD mudou durante o portão ($tested -> $(git rev-parse HEAD)) — recibo recusado"
    if ! tree_clean; then
      git status --short >&2
      die "o portão deixou a árvore suja (quality reescreveu arquivo ou gerou artefato fora do .gitignore) — recibo recusado"
    fi
    "$PYTHON" "$PY" write-receipt --quality-skipped "$quality_skipped" --layers "$layers_json"
    "$PYTHON" "$PY" print-skipped
    ;;
  ci)
    command -v "$PYTHON" >/dev/null 2>&1 || die "python ausente"
    cd "$(git rev-parse --show-toplevel)"
    ci_base=""
    ci_explicit_base=0
    ci_no_quality=0
    ci_json=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --base)
          [ -n "${2:-}" ] || die "--base exige uma ref"
          ci_base=$2
          ci_explicit_base=1
          shift 2
          ;;
        --no-quality)
          ci_no_quality=1
          shift
          ;;
        --json)
          ci_json=1
          shift
          ;;
        *)
          die "opção desconhecida em ci: $1"
          ;;
      esac
    done
    if [ "$ci_explicit_base" -eq 1 ]; then
      if ! git rev-parse --verify --quiet "$ci_base" >/dev/null 2>&1 && ! git rev-parse --verify --quiet "origin/$ci_base" >/dev/null 2>&1; then
        die "base não encontrada: $ci_base"
      fi
    elif [ -z "$ci_base" ] && [ -n "${GITHUB_BASE_REF:-}" ]; then
      ci_base=$GITHUB_BASE_REF
    fi
    started=0
    if [ "$ci_no_quality" -eq 0 ]; then
      started=$(date +%s)
      quality_skipped=0
      set +e
      if [ "$ci_json" -eq 1 ]; then
        run_quality >&2
      else
        run_quality
      fi
      qstatus=$?
      set -e
      if [ "$qstatus" -ne 0 ]; then
        die "GATE RED no quality (saída $qstatus)"
      fi
      if [ "$quality_skipped" -eq 1 ] && [ "$(git config --bool --get doctor-deploy.allowNoQuality || true)" != true ]; then
        die "GATE RED: sem entrypoint de quality"
      fi
    fi
    set +e
    if [ -n "$ci_base" ]; then
      layers_json=$("$PYTHON" "$PY" layers --started "$started" --base "$ci_base")
    else
      layers_json=$("$PYTHON" "$PY" layers --started "$started")
    fi
    lstatus=$?
    set -e
    if [ "$ci_json" -eq 1 ]; then
      printf '%s\n' "$layers_json"
    fi
    "$PYTHON" -c '
import json, sys
p = json.loads(sys.argv[1])
for w in p.get("warnings") or []:
    print(f"[doctor-deploy] {w}", file=sys.stderr)
if p.get("skipped"):
    print("[doctor-deploy] camadas puladas: " + ", ".join(p["skipped"]), file=sys.stderr)
if p.get("red"):
    print("[doctor-deploy] GATE RED: " + ", ".join(p["red"]), file=sys.stderr)
else:
    print("[doctor-deploy] GATE GREEN", file=sys.stderr)
' "$layers_json"
    exit "$lstatus"
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
    die "uso: init [--force] [--project <nome>] [--plugin-root <dir>] | discover [--json] | preflight | cc | integration | prepare | ci [--base <ref>] [--no-quality] [--json] | check <sha> | hook-one <sha> | print-skipped | tree-clean | install-hook | needs-consent | consent <tool> <yes|no>"
    ;;
esac
