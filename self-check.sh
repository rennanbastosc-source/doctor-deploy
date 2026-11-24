#!/bin/sh
# Prova os 5 casos do recibo, o shim do install-hook num caminho com espaço, hits 0 no lcov o quality vermelho e o quality ausente. Não toca no repo do chamador.
set -eu
SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ZERO=0000000000000000000000000000000000000000
# Mesmo fallback do gate.sh: no Windows, `python3` pode ser o atalho da Microsoft Store.
PYTHON=python3
"$PYTHON" -c '' >/dev/null 2>&1 || PYTHON=python
# Espaço no caminho de propósito: a skill copiada e o shim instalado têm de aguentar.
WORK=$(mktemp -d "${TMPDIR:-/tmp}/dd XXXX")
trap 'rm -rf "$WORK"' EXIT
SKILL="$WORK/skill dir"
mkdir -p "$SKILL"
cp "$SKILL_DIR/gate.sh" "$SKILL_DIR/pre-push.sh" "$SKILL_DIR/layers.py" "$SKILL"
GATE=$SKILL/gate.sh
PY=$SKILL/layers.py
TMP=$WORK/repo
mkdir -p "$TMP" && cd "$TMP"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
export CI=1
"$GATE" install-hook 2>/dev/null
HOOK=$TMP/.git/hooks/pre-push
grep -qF "$SKILL/pre-push.sh" "$HOOK"

mkdir -p src
export DD_STAMP="$WORK/stamp"
: > "$DD_STAMP"
printf '%s\n' '{"scripts":{"quality":"node stamp.js"}}' > package.json
printf '%s\n' "require('fs').appendFileSync(process.env.DD_STAMP, '1\n')" > stamp.js
printf '%s\n' 'export const a = 1' > src/a.ts
git add package.json stamp.js src/a.ts
git commit -m 'init' >/dev/null
SHA=$(git rev-parse HEAD)

stamp_n() { wc -l < "$DD_STAMP" 2>/dev/null || echo 0; }

# deleção por último, que era o bug
printf '%s %s %s %s\n%s %s %s %s\n' \
  refs/heads/main "$SHA" refs/heads/main "$ZERO" \
  refs/heads/old "$ZERO" refs/heads/old "$ZERO" | "$HOOK"
test "$(stamp_n)" -eq 1
"$GATE" check "$SHA" >/dev/null

printf '%s %s %s %s\n%s %s %s %s\n' \
  refs/heads/main "$SHA" refs/heads/main "$ZERO" \
  refs/heads/old "$ZERO" refs/heads/old "$ZERO" | "$HOOK"
test "$(stamp_n)" -eq 1

printf '%s\n' '# nota' > README.md
git add README.md
git commit -m 'docs: nota' >/dev/null
SHA_MD=$(git rev-parse HEAD)
"$GATE" check "$SHA_MD" >/dev/null
printf '%s %s %s %s\n' refs/heads/main "$SHA_MD" refs/heads/main "$SHA" | "$HOOK"
test "$(stamp_n)" -eq 1

printf '%s\n' 'export const b = 1' > src/b.ts
git add src/b.ts
git commit -m 'docs: forjado' >/dev/null
SHA_TS=$(git rev-parse HEAD)
if "$GATE" check "$SHA_TS" >/dev/null 2>&1; then
  echo "delta .ts não invalidou" >&2
  exit 1
fi
printf '%s %s %s %s\n' refs/heads/main "$SHA_TS" refs/heads/main "$SHA_MD" | "$HOOK"
test "$(stamp_n)" -eq 2

printf '%s\n' 'x' > untracked.txt
if "$GATE" prepare >/dev/null 2>&1; then
  echo "tree suja gravou recibo" >&2
  exit 1
fi
rm untracked.txt
"$PYTHON" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["tested_sha"]==sys.argv[2]' \
  "$(git rev-parse --git-dir)/doctor-deploy/receipt.json" "$SHA_TS"

printf '%s %s %s %s\n' refs/heads/old "$ZERO" refs/heads/old "$ZERO" | "$HOOK"
test "$(stamp_n)" -eq 2

# hits 0 no relatório fresco é vermelho; hits 1 e arquivo fora do relatório não são
mkdir -p covrepo && cd covrepo
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
mkdir -p src coverage
printf '%s\n' 'x = 1' > src/a.py
git add src/a.py && git commit -m a >/dev/null
printf '%s\n' 'x = 2' > src/a.py
git add src/a.py && git commit -m b >/dev/null
BASE=$(git rev-parse HEAD~1)
git update-ref refs/remotes/origin/master "$BASE"
printf '%s\n' 'SF:src/a.py' 'DA:1,0' 'end_of_record' > coverage/lcov.info
touch coverage/lcov.info
if "$PYTHON" "$PY" layers --started 0 >/dev/null; then
  echo "hits 0 não ficou vermelho" >&2
  exit 1
fi
printf '%s\n' 'SF:src/a.py' 'DA:1,1' 'end_of_record' > coverage/lcov.info
"$PYTHON" "$PY" layers --started 0 >/dev/null
printf '%s\n' 'SF:src/outro.py' 'DA:1,1' 'end_of_record' > coverage/lcov.info
"$PYTHON" "$PY" layers --started 0 >/dev/null

mkdir -p "$TMP/deprepo" && cd "$TMP/deprepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
printf '%s\n' '{"name":"t"}' > package.json
git add package.json && git commit -m a >/dev/null
git update-ref refs/remotes/origin/master HEAD
printf '%s\n' '{"name":"t","x":1}' > package.json
git add package.json && git commit -m b >/dev/null
if "$PYTHON" "$PY" layers --started 0 >/dev/null; then
  echo "manifest sem lock não ficou vermelho" >&2
  exit 1
fi

# quality que sai 2 (como make com erro) é vermelho, não skip; sem entrypoint é skip
mkdir -p "$TMP/qrepo" && cd "$TMP/qrepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
mkdir -p scripts
printf '%s\n' 'raise SystemExit(2)' > scripts/quality.py
git add scripts && git commit -m a >/dev/null
if "$GATE" prepare >/dev/null 2>&1; then
  echo "quality com saída 2 não ficou vermelho" >&2
  exit 1
fi
git rm -rq scripts && git commit -m b >/dev/null
if "$GATE" prepare >/dev/null 2>&1; then
  echo "sem quality e sem liberação gravou recibo" >&2
  exit 1
fi
git config doctor-deploy.allowNoQuality true
"$GATE" prepare >/dev/null 2>&1
"$GATE" check "$(git rev-parse HEAD)" >/dev/null 2>&1
git config --unset doctor-deploy.allowNoQuality
if "$GATE" check "$(git rev-parse HEAD)" >/dev/null 2>&1; then
  echo "check aceitou recibo sem quality depois de revogada a liberação" >&2
  exit 1
fi
"$PYTHON" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert "quality" in r["skipped"], r' \
  "$(git rev-parse --git-dir)/doctor-deploy/receipt.json"

echo "self-check ok"
