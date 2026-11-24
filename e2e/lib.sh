# Helpers dos testes ponta a ponta: app real, ferramentas reais, portão real.
set -eu
SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
GATE=$SKILL_DIR/gate.sh
PY=$SKILL_DIR/layers.py
PYTHON=python3
"$PYTHON" -c '' >/dev/null 2>&1 || PYTHON=python
export PYTHONUTF8=1
WORK=$(CDPATH= cd -- "$(mktemp -d "${TMPDIR:-/tmp}/dd-e2e XXXX")" && pwd)
[ -n "${E2E_KEEP:-}" ] || trap 'rm -rf "$WORK"' EXIT
# Consentimento isolado: não toca no ~/.config de quem roda.
export XDG_CONFIG_HOME="$WORK/config"
"$GATE" consent mutation yes 2>/dev/null

case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) WINDOWS=1 ;; *) WINDOWS=0 ;; esac

step() { printf '\n== %s\n' "$*"; }

newrepo() {
  mkdir -p "$WORK/$1" && cd "$WORK/$1"
  git init -qb main
  git config user.email e2e@example.com
  git config user.name e2e
  git config core.autocrlf false
}

base_here() { git add -A && git commit -qm base && git update-ref refs/remotes/origin/main HEAD; }

commit() { git add -A && git commit -qm "$1"; }

# expect <camada> <status> [trecho que tem de aparecer nos avisos]
expect() {
  out=$("$PYTHON" "$PY" layers --started 0 2>/dev/null || true)
  printf '%s' "$out" | "$PYTHON" -c '
import json, sys
layer, want, needle = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.loads(sys.stdin.read())
got = d["layers"][layer]
hits = [w for w in d["warnings"] if needle and needle in w]
print(f"   {layer}: {got} (quero {want})" + (f" | {hits[0]}" if hits else ""))
if got != want or (needle and not hits):
    print("   avisos: " + json.dumps(d["warnings"], ensure_ascii=False))
    sys.exit(1)
' "$1" "$2" "${3:-}"
}
