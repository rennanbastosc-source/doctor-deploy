#!/bin/sh
# Prova os 5 casos do recibo mais hits 0 no lcov. Não toca no repo do chamador.
set -eu
SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
GATE=$SKILL_DIR/gate.sh
HOOK=$SKILL_DIR/pre-push.sh
PY=$SKILL_DIR/layers.py
ZERO=0000000000000000000000000000000000000000
TMP=$(mktemp -d /tmp/opencode/dd-XXXX)
trap 'rm -rf "$TMP"' EXIT
cd "$TMP"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
export CI=1

mkdir -p src
export DD_STAMP=$(mktemp /tmp/opencode/dd-stamp-XXXX)
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
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["tested_sha"]==sys.argv[2]' \
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
if python3 "$PY" layers --started 0 >/dev/null; then
  echo "hits 0 não ficou vermelho" >&2
  exit 1
fi
printf '%s\n' 'SF:src/a.py' 'DA:1,1' 'end_of_record' > coverage/lcov.info
python3 "$PY" layers --started 0 >/dev/null
printf '%s\n' 'SF:src/outro.py' 'DA:1,1' 'end_of_record' > coverage/lcov.info
python3 "$PY" layers --started 0 >/dev/null

mkdir -p "$TMP/deprepo" && cd "$TMP/deprepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
printf '%s\n' '{"name":"t"}' > package.json
git add package.json && git commit -m a >/dev/null
git update-ref refs/remotes/origin/master HEAD
printf '%s\n' '{"name":"t","x":1}' > package.json
git add package.json && git commit -m b >/dev/null
if python3 "$PY" layers --started 0 >/dev/null; then
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
"$GATE" prepare >/dev/null 2>&1
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert "quality" in r["skipped"], r' \
  "$(git rev-parse --git-dir)/doctor-deploy/receipt.json"

echo "self-check ok"
