#!/bin/sh
# Prova os 5 casos do recibo, o shim do install-hook num caminho com espaço, hits 0 no lcov o quality vermelho, o quality ausente e o preflight. Não toca no repo do chamador.
set -eu
SKILL_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ZERO=0000000000000000000000000000000000000000
# Mesmo fallback do gate.sh: no Windows, `python3` pode ser o atalho da Microsoft Store.
PYTHON=python3
"$PYTHON" -c '' >/dev/null 2>&1 || PYTHON=python
# Espaço no caminho de propósito: a skill copiada e o shim instalado têm de aguentar.
# cd + pwd normaliza: no macOS o TMPDIR termina em "/" e o caminho ficaria com "//".
WORK=$(CDPATH= cd -- "$(mktemp -d "${TMPDIR:-/tmp}/dd XXXX")" && pwd)
trap 'rm -rf "$WORK"' EXIT
SKILL="$WORK/skill dir"
mkdir -p "$SKILL/skills/doctor-deploy"
cp "$SKILL_DIR/gate.sh" "$SKILL_DIR/pre-push.sh" "$SKILL_DIR/layers.py" "$SKILL"
cp "$SKILL_DIR/skills/doctor-deploy/gate.sh" "$SKILL_DIR/skills/doctor-deploy/pre-push.sh" "$SKILL/skills/doctor-deploy"
GATE=$SKILL/skills/doctor-deploy/gate.sh
PY=$SKILL/layers.py
dd_toml() { printf 'version = 1\nproject = "%s"\n' "${1:-test-repo}" > .doctor-deploy.toml; }
TMP=$WORK/repo
mkdir -p "$TMP" && cd "$TMP"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
export CI=1
if "$GATE" preflight >/dev/null 2>&1; then
  echo "preflight aceitou repo sem .doctor-deploy.toml" >&2; exit 1
fi
if "$GATE" install-hook >/dev/null 2>&1; then
  echo "install-hook aceitou repo sem .doctor-deploy.toml" >&2; exit 1
fi
dd_toml mainrepo
"$GATE" install-hook 2>/dev/null
HOOK=$TMP/.git/hooks/pre-push
grep -qF "\"$SKILL\"" "$HOOK"
"$GATE" preflight 2>&1 | grep -q "ok: hook pre-push é o shim"
# Shim com a skill fora do lugar de instalação: acha o plugin mais novo; sem skill, bloqueia só a main.
FHOME=$WORK/fakehome
for n in 1 2; do
  v=0.0.$n
  mkdir -p "$FHOME/.claude/plugins/cache/m/doctor-deploy/$v"
  printf '#!/bin/sh\necho via-plugin-%s >&2\n' "$v" > "$FHOME/.claude/plugins/cache/m/doctor-deploy/$v/pre-push.sh"
  chmod +x "$FHOME/.claude/plugins/cache/m/doctor-deploy/$v/pre-push.sh"
  touch -t "20260101000$n" "$FHOME/.claude/plugins/cache/m/doctor-deploy/$v/pre-push.sh"
done
sed "s#$SKILL#$WORK/sumiu#" "$HOOK" > "$WORK/shim" && chmod +x "$WORK/shim"
: | HOME="$FHOME" "$WORK/shim" 2>&1 | grep -q via-plugin-0.0.2 || { echo "shim não achou o plugin mais novo" >&2; exit 1; }
rm -r "$FHOME/.claude"
mkdir -p "$FHOME/.gemini/config/plugins/doctor-deploy/skills/doctor-deploy"
printf '#!/bin/sh\necho via-antigravity >&2\n' > "$FHOME/.gemini/config/plugins/doctor-deploy/skills/doctor-deploy/pre-push.sh"
chmod +x "$FHOME/.gemini/config/plugins/doctor-deploy/skills/doctor-deploy/pre-push.sh"
: | HOME="$FHOME" "$WORK/shim" 2>&1 | grep -q via-antigravity || { echo "shim não achou o plugin do Antigravity (skills/)" >&2; exit 1; }
rm -rf "$FHOME/.gemini/config/plugins/doctor-deploy/skills"
printf '#!/bin/sh\necho via-antigravity-root >&2\n' > "$FHOME/.gemini/config/plugins/doctor-deploy/pre-push.sh"
chmod +x "$FHOME/.gemini/config/plugins/doctor-deploy/pre-push.sh"
: | HOME="$FHOME" "$WORK/shim" 2>&1 | grep -q via-antigravity-root || { echo "shim não achou fallback na raiz do plugin Antigravity" >&2; exit 1; }
rm -r "$FHOME/.gemini"
if printf 'refs/heads/main %s refs/heads/main %s\n' "$ZERO" "$ZERO" | HOME="$FHOME" "$WORK/shim" 2>/dev/null; then
  echo "shim sem skill deixou passar push para a main" >&2; exit 1
fi
printf 'refs/heads/feat %s refs/heads/feat %s\n' "$ZERO" "$ZERO" | HOME="$FHOME" "$WORK/shim" 2>/dev/null

mkdir -p src
export DD_STAMP="$WORK/stamp"
: > "$DD_STAMP"
printf '%s\n' '{"scripts":{"quality":"node stamp.js"}}' > package.json
printf '%s\n' "require('fs').appendFileSync(process.env.DD_STAMP, '1\n')" > stamp.js
printf '%s\n' 'export const a = 1' > src/a.ts
git add .doctor-deploy.toml package.json stamp.js src/a.ts
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

# squash: commit órfão (não descende do testado) com o mesmo código + *.md
"$GATE" check "$(git commit-tree "$SHA_MD^{tree}" -m squash)" >/dev/null

printf '%s\n' 'export const b = 1' > src/b.ts
git add src/b.ts
git commit -m 'docs: forjado' >/dev/null
SHA_TS=$(git rev-parse HEAD)
if "$GATE" check "$SHA_TS" >/dev/null 2>&1; then
  echo "delta .ts não invalidou" >&2
  exit 1
fi
if "$GATE" check "$(git commit-tree "$SHA_TS^{tree}" -m squash)" >/dev/null 2>&1; then
  echo "squash com .ts não invalidou" >&2
  exit 1
fi
if "$GATE" check 0123456789abcdef0123456789abcdef01234567 >/dev/null 2>&1; then
  echo "SHA inexistente passou no check" >&2
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
dd_toml covrepo
mkdir -p src coverage
printf '%s\n' 'x = 1' > src/a.py
git add .doctor-deploy.toml src/a.py && git commit -m a >/dev/null
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

# branch não tomada em linha alterada é vermelho, mesmo com hits 1
printf '%s\n' 'SF:src/a.py' 'DA:1,1' 'BRDA:1,0,0,1' 'BRDA:1,0,1,0' 'end_of_record' > coverage/lcov.info
if "$PYTHON" "$PY" layers --started 0 >/dev/null; then
  echo "branch não tomada no diff não ficou vermelho" >&2
  exit 1
fi
printf '%s\n' 'SF:src/a.py' 'DA:1,1' 'BRDA:1,0,0,1' 'BRDA:1,0,1,2' 'end_of_record' > coverage/lcov.info
"$PYTHON" "$PY" layers --started 0 >/dev/null
rm -rf coverage
# coverage.xml na raiz (pytest-cov --cov-report=xml) é lido: hits 0 é vermelho
printf '%s\n' '<coverage><packages><package><classes><class filename="src/a.py"><lines><line number="1" hits="0"/></lines></class></classes></package></packages></coverage>' > coverage.xml
if "$PYTHON" "$PY" layers --started 0 >/dev/null; then
  echo "hits 0 no coverage.xml não ficou vermelho" >&2
  exit 1
fi
rm coverage.xml
# pasta oculta: "./.storybook/x.ts" no relatório casa com ".storybook/x.ts" do diff
"$PYTHON" -c 'import sys; sys.path.insert(0, sys.argv[1]); import layers; from pathlib import Path
assert layers.norm("./.storybook/x.ts", Path("/r")) == ".storybook/x.ts"
assert layers.norm("/r/.config/a.ts", Path("/r")) == ".config/a.ts"' "$SKILL"
# audit: saída não-zero sem achado (comando inexistente, rede, sem lock) é skip; achado é vermelho
"$PYTHON" -c 'import sys; sys.path.insert(0, sys.argv[1]); import layers; from subprocess import CompletedProcess as P
s = lambda out: layers.audit_status(P([], 1, out, ""))
assert s("3 high severity vulnerabilities") == "red", s("3 high severity vulnerabilities")
assert s("found 2 vulnerabilities (1 moderate, 1 high)") == "red"
assert s("Found 1 known vulnerability in 1 package") == "red"
assert s("1 vulnerability") == "red"
assert s("Usage Error: Couldnt find a script named audit") == "error"
assert s("npm ERR! code ENOLOCK") == "error"
assert layers.audit_status(P([], 0, "", "")) == "green"' "$SKILL"

# mocks: banco e módulo testado são vermelho; outro módulo próprio é aviso; banco real injetado passa
mock_red() {
  git add -A && git commit -qm "$1" >/dev/null
  if "$PYTHON" "$PY" layers --started 0 >/dev/null; then
    echo "mocks: $1 não ficou vermelho" >&2
    exit 1
  fi
  git reset -q --hard HEAD~1
}
write_test() { mkdir -p src/__tests__ && printf '%s\n' "$@" > src/__tests__/svc.test.ts; }
write_test 'vi.mock("@/lib/prisma")'
mock_red "mock do banco"
write_test 'vi.mock("../svc", () => ({}))'
mock_red "mock do módulo testado"
write_test 'vi.mock("@/lib/logger")' 'await migrate(db)' 'vi.doMock("#/db/index", () => ({ db }))'
git add -A && git commit -qm "mock na fronteira e banco real" >/dev/null
out=$("$PYTHON" "$PY" layers --started 0)
printf '%s\n' "$out" | grep -q 'mocks: src/__tests__/svc.test.ts:1 .*(@/lib/logger)'

# cc: função nova acima de 15 na working tree, antes do commit
if command -v lizard >/dev/null 2>&1; then
  {
    echo 'def f(x):'
    i=0
    while [ $i -lt 16 ]; do echo "    if x == $i: return $i"; i=$((i + 1)); done
    echo '    return -1'
  } > src/complexa.py
  if "$GATE" cc >/dev/null 2>&1; then
    echo "cc não pegou função nova acima de 15 antes do commit" >&2
    exit 1
  fi
  rm src/complexa.py
  "$GATE" cc >/dev/null 2>&1
  # homônimos: a anônima simples depois da complexa não esconde a complexa
  {
    echo 'const a = [1].map((x) => {'
    i=0
    while [ $i -lt 16 ]; do echo "  if (x === $i) return $i"; i=$((i + 1)); done
    echo '  return -1'
    echo '})'
    echo 'const b = [1].map((x) => x)'
  } > src/anon.js
  if "$GATE" cc >/dev/null 2>&1; then
    echo "cc: (anonymous) simples escondeu a (anonymous) acima de 15" >&2
    exit 1
  fi
  rm src/anon.js
fi

# quality que sai 2 (como make com erro) é vermelho, não skip; sem entrypoint é skip
mkdir -p "$TMP/qrepo" && cd "$TMP/qrepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
dd_toml qrepo
mkdir -p scripts
printf '%s\n' 'raise SystemExit(2)' > scripts/quality.py
git add .doctor-deploy.toml scripts && git commit -m a >/dev/null
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
if ! "$GATE" preflight >/dev/null 2>&1; then
  echo "preflight reprovou repo liberado" >&2
  exit 1
fi
git config --unset doctor-deploy.allowNoQuality
if "$GATE" preflight >/dev/null 2>&1; then
  echo "preflight aprovou repo sem quality" >&2
  exit 1
fi
if "$GATE" check "$(git rev-parse HEAD)" >/dev/null 2>&1; then
  echo "check aceitou recibo sem quality depois de revogada a liberação" >&2
  exit 1
fi
"$PYTHON" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert "quality" in r["skipped"], r' \
  "$(git rev-parse --git-dir)/doctor-deploy/receipt.json"
# quality que reescreve arquivo versionado (formatter com --write) não grava recibo
mkdir -p scripts
printf '%s\n' 'x' > fmt.txt
printf '#!/bin/sh\necho y >> fmt.txt\n' > scripts/quality.sh
git add -A && git commit -qm fmt
rm -f "$(git rev-parse --git-dir)/doctor-deploy/receipt.json"
if "$GATE" prepare >/dev/null 2>&1; then
  echo "quality que sujou a árvore gravou recibo" >&2
  exit 1
fi
git checkout -q -- fmt.txt
# package.json ilegível: mensagem clara, não traceback
printf '%s\n' '{' > package.json
"$GATE" preflight 2>&1 | grep -q "package.json ilegível" || { echo "package.json inválido sem mensagem clara" >&2; exit 1; }
rm package.json

# integração: service e componente tocados precisam de teste de integração
mkdir -p "$TMP/irepo" && cd "$TMP/irepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
dd_toml irepo
printf '%s\n' 'x' > README.md
git add .doctor-deploy.toml README.md && git commit -qm base >/dev/null
git update-ref refs/remotes/origin/main HEAD
mkdir -p src/services src/components
printf '%s\n' 'export const total = () => 1' > src/services/conta.service.ts
if "$GATE" integration >/dev/null 2>&1; then
  echo "integration não acusou service sem teste na working tree" >&2
  exit 1
fi
git add -A && git commit -qm service >/dev/null
if "$PYTHON" "$PY" layers --started 0 >/dev/null; then
  echo "service sem teste de integração não ficou vermelho" >&2
  exit 1
fi
mkdir -p src/services/__tests__
printf '%s\n' 'vi.mock("@/lib/prisma")' 'import { total } from "../conta.service"' > src/services/__tests__/conta.test.ts
git add -A && git commit -qm "teste com mock" >/dev/null
if "$PYTHON" "$PY" layers --started 0 2>/dev/null | grep -q '"integration": "green"'; then
  echo "teste com banco mockado contou como integração" >&2
  exit 1
fi
printf '%s\n' 'import { total } from "../conta.service"' 'const db = createDb(":memory:")' > src/services/__tests__/conta.test.ts
printf '%s\n' 'export function Botao() { return null }' > src/components/Botao.tsx
printf '%s\n' 'import { Botao } from "./Botao"' 'render(<Botao />)' 'fireEvent.click(x)' > src/components/Botao.test.tsx
"$GATE" integration >/dev/null 2>&1
git add -A && git commit -qm "integração real" >/dev/null
"$PYTHON" "$PY" layers --started 0 2>/dev/null | grep -q '"integration": "green"'
# hooks/ e components/ fora de JS/TS não são UI (hooks em Python, ViewComponent do Rails)
mkdir -p src/hooks app/components
printf '%s\n' 'x = 1' > src/hooks/stripe.py
printf '%s\n' 'class BotaoComponent; end' > app/components/botao_component.rb
"$GATE" integration >/dev/null 2>&1 || { echo "hooks/ em Python ou components/ em Ruby cobrado como UI" >&2; exit 1; }
rm -r src/hooks app/components
# Python: banco real do Django/pytest-django conta como integração
mkdir -p app/services tests
printf '%s\n' 'def total(): return 1' > app/services/conta.py
printf '%s\n' 'import pytest' 'from app.services.conta import total' '@pytest.mark.django_db' 'def test_total(): assert total() == 1' > tests/test_conta.py
"$GATE" integration >/dev/null 2>&1 || { echo "teste com pytest.mark.django_db não contou como integração" >&2; exit 1; }
rm -r app tests

# grafo de imports: alias do tsconfig, cobertura transitiva, classificação por import do banco
mkdir -p "$TMP/grepo" && cd "$TMP/grepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
dd_toml grepo
printf '%s\n' 'x' > README.md
git add .doctor-deploy.toml README.md && git commit -qm base >/dev/null
git update-ref refs/remotes/origin/main HEAD
mkdir -p src/dominio src/api
# JSONC: comentário, vírgula final e "/*" dentro de string
printf '%s\n' '{ /* bundler */ "compilerOptions": { "paths": { "~app/*": ["./src/*"], }, } }' > tsconfig.json
printf '%s\n' 'export const db = {}' > src/db.ts
printf '%s\n' 'import { db } from "../db"' 'export const regra = () => db' > src/dominio/regra.ts
printf '%s\n' 'import { regra } from "~app/dominio/regra"' 'export const router = { regra }' > src/api/router.ts
printf '%s\n' 'const { router } = await import("~app/api/router")' 'const mem = ":memory:"' > src/api/router.test.ts
"$GATE" integration >/dev/null 2>&1 || { echo "regra coberta pelo teste do router (via alias) foi acusada" >&2; exit 1; }
printf '%s\n' 'import { db } from "../db"' 'export const outra = () => db' > src/dominio/outra.ts
if "$GATE" integration >/dev/null 2>&1; then
  echo "módulo fora das pastas que importa o banco não foi cobrado" >&2
  exit 1
fi
rm src/dominio/outra.ts
mkdir -p src/a/utils src/b/utils
printf '%s\n' 'import { db } from "../../db"' 'export const f = () => db' > src/a/utils/format.ts
cp src/a/utils/format.ts src/b/utils/format.ts
printf '%s\n' 'import { f } from "./format"' 'const mem = ":memory:"' > src/a/utils/format.test.ts
if "$GATE" integration >/dev/null 2>&1; then
  echo "teste de a/utils/format cobriu o homônimo b/utils/format" >&2
  exit 1
fi
rm -r src/a src/b
printf '%s\n' 'vi.mock("~app/dominio/regra")' 'const { router } = await import("~app/api/router")' 'const mem = ":memory:"' > src/api/router.test.ts
if "$GATE" integration >/dev/null 2>&1; then
  echo "módulo mockado no teste de integração contou como coberto" >&2
  exit 1
fi

# mutação: Stryker falso escreve o relatório; mutante vivo em linha alterada é vermelho
mkdir -p "$TMP/mrepo" && cd "$TMP/mrepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
dd_toml mrepo
printf '%s\n' node_modules reports > .gitignore
mkdir -p src node_modules/.bin
printf '%s\n' 'export const a = 1' 'export const b = 2' 'export const c = 3' > src/calc.ts
git add -A && git commit -qm base >/dev/null
git update-ref refs/remotes/origin/main HEAD
printf '%s\n' 'export const a = 1' 'export const b = (x: number) => x > 2' 'export const c = 3' > src/calc.ts
git add -A && git commit -qm muda >/dev/null
cat > node_modules/.bin/stryker.js <<'JS'
const fs = require("fs"); const a = process.argv; const f = a[a.indexOf("--mutate") + 1].split(",")[0]
fs.mkdirSync("reports/mutation", { recursive: true })
const m = { status: process.env.FAKE_STATUS, mutatorName: "EqualityOperator", location: { start: { line: +process.env.FAKE_LINE } } }
fs.writeFileSync("reports/mutation/mutation.json", JSON.stringify({ files: { [f]: { mutants: [m] } } }))
JS
printf '#!/bin/sh\nexec node "$(dirname "$0")/stryker.js" "$@"\n' > node_modules/.bin/stryker
printf '@node "%%~dp0stryker.js" %%*\r\n' > node_modules/.bin/stryker.cmd
chmod +x node_modules/.bin/stryker
# consentimento é global da máquina: isola num HOME vazio (USERPROFILE no Windows)
mut() { HOME="$WORK/home" USERPROFILE="$WORK/home" FAKE_STATUS=$1 FAKE_LINE=$2 "$PYTHON" "$PY" layers --started 0 2>/dev/null | grep -q "\"mutation\": \"$3\"" || { echo "mutação $1 na linha $2 não deu $3" >&2; exit 1; }; }
mut Survived 2 red
mut NoCoverage 2 red
mut Killed 2 green
mut Ignored 2 green
mut Survived 1 green

# agnóstico, Go: quality em scripts/quality.sh, coverprofile, import de pacote pelo go.mod, sqlmock
newrepo() { mkdir -p "$TMP/$1" && cd "$TMP/$1" && git init -qb main && git config user.email t@example.com && git config user.name t && dd_toml "$1"; }
layer() { "$PYTHON" "$PY" layers --started 0 2>/dev/null | grep -q "\"$1\": \"$2\"" || { echo "$3" >&2; exit 1; }; }
newrepo gorepo
printf 'module example.com/loja\n\ngo 1.22\n' > go.mod
mkdir -p internal/db internal/pedido scripts
printf 'package db\n\nimport "database/sql"\n\ntype Store struct{ DB *sql.DB }\n' > internal/db/db.go
printf 'package pedido\n\nimport "example.com/loja/internal/db"\n\nvar S *db.Store\n\nfunc Total(v int) int {\n\treturn v\n}\n' > internal/pedido/service.go
printf 'package pedido\n\nimport "testing"\n\nfunc TestTotal(t *testing.T) {}\n' > internal/pedido/service_test.go
printf '#!/bin/sh\ntrue\n' > scripts/quality.sh
git add -A && git commit -qm base && git update-ref refs/remotes/origin/main HEAD
"$GATE" preflight 2>&1 | grep -q "ok: quality = sh scripts/quality.sh"
printf 'package pedido\n\nimport "example.com/loja/internal/db"\n\nvar S *db.Store\n\nfunc Total(v int) int {\n\tif v > 100 {\n\t\treturn v * 9 / 10\n\t}\n\treturn v\n}\n' > internal/pedido/service.go
git commit -qam desconto
printf 'mode: set\nexample.com/loja/internal/pedido/service.go:7.24,8.13 1 1\nexample.com/loja/internal/pedido/service.go:8.13,10.3 1 0\nexample.com/loja/internal/pedido/service.go:11.2,11.10 1 1\n' > coverage.out
layer diff_cover red "Go: linha nova com hits 0 no coverprofile não ficou vermelha"
layer integration red "Go: service que importa o pacote do banco passou sem teste de integração"
printf 'package pedido\n\nimport _ "github.com/DATA-DOG/go-txdb"\n' > internal/pedido/service_integration_test.go
printf 'package pedido\n\nfunc mock() { db, m, _ := sqlmock.New(); _ = db; _ = m }\n' > internal/pedido/repo_test.go
git add internal && git commit -qm testes
layer integration green "Go: teste txdb na pasta do pacote não cobriu o service"
layer mocks red "Go: sqlmock não ficou vermelho"
# mutadores falsos no PATH (sh + .cmd), em node como o Stryker falso; consentimento isolado num HOME vazio
FAKE=$WORK/fakebin
mkdir -p "$FAKE"
fake_bin() {
  printf '#!/bin/sh\nexec node "$(dirname "$0")/%s.js" "$@"\n' "$1" > "$FAKE/$1"
  printf '@node "%%~dp0%s.js" %%*\r\n' "$1" > "$FAKE/$1.cmd"
  chmod +x "$FAKE/$1"
}
cat > "$FAKE/gremlins.js" <<'JS'
const fs = require("fs"); const a = process.argv; const o = a[a.indexOf("--output") + 1]
const m = { type: "ARITHMETIC_BASE", status: process.env.FAKE_STATUS, line: +process.env.FAKE_LINE }
fs.writeFileSync(o, JSON.stringify({ go_module: "example.com/loja", files: [{ file_name: "internal/pedido/service.go", mutations: [m] }] }))
JS
fake_bin gremlins
mutf() { PATH="$FAKE:$PATH" HOME="$WORK/home" USERPROFILE="$WORK/home" FAKE_STATUS=$1 FAKE_LINE=$2 "$PYTHON" "$PY" layers --started 0 2>/dev/null | grep -q "\"mutation\": \"$3\"" || { echo "mutação $1 linha $2 não deu $3 ($4)" >&2; exit 1; }; }
mutf LIVED 9 red gremlins
case "$(uname -s)" in
  # gremlins <= 0.6.0 no Windows marca tudo NOT COVERED: a skill pula em vez de reprovar
  MINGW*|MSYS*|CYGWIN*) mutf "NOT COVERED" 9 skip "gremlins no Windows" ;;
  *) mutf "NOT COVERED" 9 red gremlins ;;
esac
mutf KILLED 9 green gremlins
mutf LIVED 3 green "gremlins fora do diff"

# agnóstico, Rails: constantes do Zeitwerk, request spec -> controller -> service, stub do ActiveRecord, SimpleCov
newrepo rbrepo
mkdir -p app/models app/controllers app/services/pagamentos spec/requests spec/services/pagamentos coverage
printf 'class Pedido < ApplicationRecord\nend\n' > app/models/pedido.rb
printf 'class PedidosController < ApplicationController\n  def create\n    Pagamentos::Cobrar.new(Pedido.create!).call\n  end\nend\n' > app/controllers/pedidos_controller.rb
printf 'module Pagamentos\n  class Cobrar\n    def call\n      true\n    end\n  end\nend\n' > app/services/pagamentos/cobrar.rb
git add -A && git commit -qm base && git update-ref refs/remotes/origin/main HEAD
printf 'module Pagamentos\n  class Cobrar\n    def call\n      @pedido.update!(pago: true)\n    end\n  end\nend\n' > app/services/pagamentos/cobrar.rb
git commit -qam cobranca
layer integration red "Rails: service tocado sem spec com banco real passou"
printf 'require "rails_helper"\n\nRSpec.describe "Pedidos", type: :request do\n  it { post "/pedidos" }\nend\n' > spec/requests/pedidos_spec.rb
git add spec && git commit -qm request
layer integration green "Rails: request spec não cobriu o service pelo controller"
printf 'require "spec_helper"\n\nRSpec.describe Pagamentos::Cobrar do\n  it { allow(Pedido).to receive(:find) }\nend\n' > spec/services/pagamentos/cobrar_spec.rb
git add spec && git commit -qm stub
layer mocks red "Rails: stub de Pedido.find não ficou vermelho"
# caminho absoluto nativo, como o SimpleCov grava (C:/... no Windows)
R=$(git rev-parse --show-toplevel)
printf '{"RSpec":{"coverage":{"%s/app/services/pagamentos/cobrar.rb":{"lines":[1,1,1,0,null,null,null]}}}}' "$R" > coverage/.resultset.json
layer diff_cover red "Rails: linha nova com 0 no SimpleCov não ficou vermelha"
printf '{"RSpec":{"coverage":{"%s/app/services/pagamentos/cobrar.rb":{"lines":[1,1,1,1,null,null,null],"branches":{"[:if, 0, 4, 6, 4, 30]":{"[:then, 1, 4, 6, 4, 20]":0}}}}}}' "$R" > coverage/.resultset.json
layer diff_cover red "Rails: branch não exercitada no SimpleCov não ficou vermelha"
# duas execuções no resultset (merge do SimpleCov): o branch coberto em qualquer uma conta como coberto
printf '{"A":{"coverage":{"%s/app/services/pagamentos/cobrar.rb":{"lines":[1,1,1,1,null,null,null],"branches":{"[:if, 0, 4, 6, 4, 30]":{"[:then, 1, 4, 6, 4, 20]":0}}}}},"B":{"coverage":{"%s/app/services/pagamentos/cobrar.rb":{"lines":[1,1,1,1,null,null,null],"branches":{"[:if, 0, 4, 6, 4, 30]":{"[:then, 1, 4, 6, 4, 20]":3}}}}}}' "$R" "$R" > coverage/.resultset.json
layer diff_cover green "Rails: branch coberto numa das execuções do resultset ficou vermelho"
# mutant falso via `bundle exec mutant run --since`: imprime a linha "evil:" de mutante vivo
printf 'GEM\n  specs:\n    mutant (0.17.1)\n' > Gemfile.lock
printf 'usage: opensource\nintegration:\n  name: minitest\n' > .mutant.yml
git add -A && git commit -qm mutant
cat > "$FAKE/bundle.js" <<'JS'
if (process.env.FAKE_STATUS === "evil") {
  console.log(`evil:Pagamentos::Cobrar#call:${process.cwd()}/app/services/pagamentos/cobrar.rb:3:3f2a1`)
  process.exit(1)
}
JS
fake_bin bundle
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) mutf evil 0 skip "mutant no Windows (sem fork)" ;;
  *) mutf evil 0 red mutant; mutf killed 0 green mutant
     PATH="$FAKE:$PATH" HOME="$WORK/home" USERPROFILE="$WORK/home" FAKE_STATUS=evil "$PYTHON" "$PY" layers --started 0 2>/dev/null \
       | grep -q "cobrar.rb:3 Pagamentos::Cobrar#call" || { echo "aviso do mutant sem o subject completo" >&2; exit 1; } ;;
esac

# hook de projeto que delega (scripts/pre-push.sh)
mkdir -p "$TMP/hrepo" && cd "$TMP/hrepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
dd_toml hrepo
mkdir -p scripts
printf '#!/bin/sh\n# delega a doctor-deploy/pre-push.sh\necho via-projeto >&2\nexec "%s"\n' "$SKILL/pre-push.sh" > scripts/pre-push.sh
chmod +x scripts/pre-push.sh
git add .doctor-deploy.toml scripts && git commit -m a >/dev/null
HHOOK=$(pwd)/.git/hooks/pre-push
mkdir -p .git/hooks
printf '#!/bin/sh\nexec "%s"\n' "$SKILL/pre-push.sh" > "$HHOOK"
"$GATE" preflight 2>&1 | grep -q "pula o scripts/pre-push.sh"
rm "$HHOOK"
"$GATE" install-hook 2>/dev/null
grep -q 'scripts/pre-push.sh' "$HHOOK"
"$GATE" preflight 2>&1 | grep -q "ok: hook pre-push -> scripts/pre-push.sh"
: | "$HHOOK" 2>&1 | grep -q via-projeto

# core.hooksPath (husky e afins): install-hook e preflight usam a pasta que o git lê
newrepo hprepo
git config core.hooksPath .githooks
printf '#!/bin/sh\ntrue\n' > q.sh && git add q.sh && git commit -qm a
"$GATE" install-hook 2>/dev/null
grep -q "doctor-deploy: shim" .githooks/pre-push || { echo "install-hook ignorou core.hooksPath" >&2; exit 1; }
"$GATE" preflight 2>&1 | grep -q "ok: hook pre-push é o shim" || { echo "preflight ignorou core.hooksPath" >&2; exit 1; }

# hook com dois refs protegidos e quality que lê stdin: o segundo ref ainda passa pelo portão
newrepo sirepo
mkdir -p scripts
printf '%s\n' 'x' > a.txt
printf '#!/bin/sh\ncat >/dev/null\n' > scripts/quality.sh
git add -A && git commit -qm a
OLD=$(git rev-parse HEAD)
printf '%s\n' 'y' > a.txt && git commit -qam b
if printf '%s %s %s %s\n%s %s %s %s\n' \
  refs/heads/main "$(git rev-parse HEAD)" refs/heads/main "$ZERO" \
  refs/heads/rel "$OLD" refs/heads/rel "$ZERO" | PUSH_GATE_BRANCHES=main,rel "$SKILL/pre-push.sh" 2>/dev/null; then
  echo "quality que lê stdin engoliu o segundo ref do push" >&2
  exit 1
fi

# added_lines_map em lote (inclusive arquivo com espaço) e audit de uv.lock via pip-audit falso
newrepo batchrepo
mkdir -p src
printf '%s\n' '{"name":"ação","scripts":{"quality":"true"}}' > package.json
printf '%s\n' 'export const a = 1' > "src/arquivo com espaco.ts"
printf '%s\n' 'export const b = 1' > src/b.ts
git add -A && git commit -qm base && git update-ref refs/remotes/origin/main HEAD
printf '%s\n' 'export const a = 1' 'export const a2 = 2' > "src/arquivo com espaco.ts"
printf '%s\n' 'export const b = 1' 'export const b2 = 2' > src/b.ts
printf '%s\n' 'version = 1' > uv.lock
git add -A && git commit -qm altera
"$PYTHON" -c 'import sys; sys.path.insert(0, sys.argv[1]); import layers
m = layers.added_lines_map(layers.base_sha())
assert m["src/arquivo com espaco.ts"] == [2], m
assert m["src/b.ts"] == [2], m' "$SKILL"
cat > "$FAKE/pip-audit.js" <<'JS'
console.error("Found 1 known vulnerability in 1 package"); process.exit(1);
JS
fake_bin pip-audit
( PATH="$FAKE:$PATH" layer audit red "pip-audit com uv.lock não ficou vermelho" )

# base dinâmica via origin/HEAD (branch develop)
newrepo dynrepo
git switch -qc develop
mkdir -p src
printf '%s\n' 'export const a = 1' > src/a.ts
git add src && git commit -qm base
git update-ref refs/remotes/origin/develop HEAD
git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/develop
printf '%s\n' 'export const a = 1' 'export const b = 2' > src/a.ts
git commit -qam feat
[ -n "$("$PYTHON" -c 'import sys; sys.path.insert(0, sys.argv[1]); import layers; print(layers.base_sha())' "$SKILL")" ] || { echo "base_sha não detectou origin/HEAD -> origin/develop" >&2; exit 1; }

# .doctor-deploy.toml: obrigatório, inválido falha alto (preflight e pre-push), fallback doctor-deploy.toml, [plugin] root, migração e overrides
newrepo cfgrepo
git switch -qc trunk
mkdir -p src/legacy src/usecases src/infra scripts reports
rm -f .doctor-deploy.toml
if "$GATE" preflight >/dev/null 2>&1; then
  echo "preflight aceitou repo sem .doctor-deploy.toml" >&2; exit 1
fi
"$GATE" preflight 2>&1 | grep -q "obrigatório na raiz" || { echo "preflight sem .doctor-deploy.toml não avisou claro" >&2; exit 1; }
printf '%s\n' '{}' > .doctor-deploy.json
"$GATE" preflight 2>&1 | grep -q "substituído por .doctor-deploy.toml" || { echo ".doctor-deploy.json legado sem erro claro" >&2; exit 1; }
rm .doctor-deploy.json
printf '%s\n' '[' > .doctor-deploy.toml
"$GATE" preflight 2>&1 | grep -q ".doctor-deploy.toml ilegível" || { echo ".doctor-deploy.toml inválido sem erro claro" >&2; exit 1; }
if printf 'refs/heads/feat %s refs/heads/feat %s\n' "$ZERO" "$ZERO" | "$SKILL/pre-push.sh" 2>/dev/null; then
  echo "pre-push deixou passar .doctor-deploy.toml inválido" >&2
  exit 1
fi
printf 'version = 1\n' > .doctor-deploy.toml
"$GATE" preflight 2>&1 | grep -q "obrigatório 'project'" || { echo ".doctor-deploy.toml sem project não falhou" >&2; exit 1; }
# Ambiguidade: .doctor-deploy.toml + doctor-deploy.toml falha alto
dd_toml cfgrepo
printf 'version = 1\nproject = "cfgrepo"\n' > doctor-deploy.toml
"$GATE" preflight 2>&1 | grep -q "uso ambíguo" || { echo "ambiguidade toml não falhou" >&2; exit 1; }
rm .doctor-deploy.toml
# Fallback doctor-deploy.toml (sem ponto) aceito normalmente
"$GATE" preflight >/dev/null 2>&1 || true
"$PYTHON" -c 'import sys; sys.path.insert(0, sys.argv[1]); import layers
d = layers._fallback_toml_loads("""
# comentario
version = 1
project = "demo"
[gate]
protected_branches = ["main", "trunk"]
quality = "sh scripts/custom.sh"
[limits]
size = 5
cc = 20
""")
assert d["project"] == "demo" and d["limits"]["size"] == 5 and d["gate"]["protected_branches"] == ["main", "trunk"], d
try:
    layers._fallback_toml_loads("project = [\"x\"]]\n")
    raise AssertionError("colchete sobrando passou")
except ValueError:
    pass' "$SKILL"
rm doctor-deploy.toml
if "$GATE" init --project >/dev/null 2>&1; then
  echo "init aceitou --project sem valor" >&2; exit 1
fi
if "$GATE" init --opcao-invalida >/dev/null 2>&1; then
  echo "init aceitou opção desconhecida" >&2; exit 1
fi
printf '%s\n' '{"project":123}' > .doctor-deploy.json
if "$GATE" init 2>&1 | grep -qi "traceback"; then
  echo "init deu traceback com project não-string no JSON legado" >&2; exit 1
fi
"$GATE" init 2>&1 | grep -q "project deve ser string não vazia" || { echo "init não validou project não-string no JSON legado" >&2; exit 1; }
rm .doctor-deploy.json
# Migração automática de .doctor-deploy.json via gate.sh init
cat > .doctor-deploy.json <<'JSON'
{
  "baseBranch": "trunk",
  "protectedBranches": ["trunk"],
  "quality": "sh scripts/custom.sh",
  "limits": { "size": 5, "cc": 20 },
  "coverageFiles": ["reports/custom.lcov"],
  "ignorePaths": ["src/legacy/**"],
  "dbModules": ["(^|[/.])datasource$"],
  "realDbPatterns": ["startFakeContainer\\("],
  "integration": { "dataDirs": ["usecases"], "ignore": ["src/usecases/ignorado.ts"] },
  "disableLayers": ["size"]
}
JSON
"$GATE" init --project cfgrepo 2>&1 | grep -q "migrado de .doctor-deploy.json" || { echo "gate.sh init não migrou .doctor-deploy.json" >&2; exit 1; }
rm .doctor-deploy.json
printf '%s\n' 'export const db = {}' > src/infra/datasource.ts
printf '%s\n' 'export const ok = 1' > src/usecases/criar.ts
printf '%s\n' '#!/bin/sh' 'echo custom-q >&2' 'exit 1' > scripts/custom.sh
git add -A && git commit -qm base && git update-ref refs/remotes/origin/trunk HEAD
(cd src/usecases && "$GATE" preflight 2>&1 | grep -q "ok: quality = sh scripts/custom.sh") || { echo "preflight em subpasta ignorou quality do .doctor-deploy.toml" >&2; exit 1; }
if printf 'refs/heads/trunk %s refs/heads/trunk %s\n' "$(git rev-parse HEAD)" "$ZERO" | "$SKILL/pre-push.sh" 2>/dev/null; then
  echo "pre-push não protegeu protected_branches do .doctor-deploy.toml" >&2
  exit 1
fi
# [plugin] root: inválido falha alto; válido reencaminha pre-push.sh e gate.sh
ALT_ROOT="$WORK/alt-plugin"
mkdir -p "$ALT_ROOT"
cp "$SKILL/gate.sh" "$SKILL/layers.py" "$ALT_ROOT"
printf '#!/bin/sh\necho forwarded-to-root >&2\nexit 0\n' > "$ALT_ROOT/pre-push.sh"
chmod +x "$ALT_ROOT/pre-push.sh"
cp .doctor-deploy.toml "$WORK/cfg.bak"
{ cat "$WORK/cfg.bak"; printf '\n[plugin]\nroot = "%s"\n' "$WORK/nao-existe"; } > .doctor-deploy.toml
"$GATE" preflight 2>&1 | grep -q "plugin.root inválido" || { echo "plugin.root inválido não falhou" >&2; exit 1; }
{ cat "$WORK/cfg.bak"; printf '\n[plugin]\nroot = "%s"\n' "$ALT_ROOT"; } > .doctor-deploy.toml
printf 'refs/heads/feat %s refs/heads/feat %s\n' "$ZERO" "$ZERO" | "$SKILL/pre-push.sh" 2>&1 | grep -q "forwarded-to-root" || { echo "plugin.root não reencaminhou para a raiz do plugin" >&2; exit 1; }
"$GATE" preflight 2>&1 | grep -q "ok: plugin.root ->" || { echo "preflight não confirmou plugin.root" >&2; exit 1; }
{ cat "$WORK/cfg.bak"; printf '\n[plugin]\nroot = "../../alt-plugin"\n'; } > .doctor-deploy.toml
printf 'refs/heads/feat %s refs/heads/feat %s\n' "$ZERO" "$ZERO" | "$SKILL/pre-push.sh" 2>&1 | grep -q "forwarded-to-root" || { echo "plugin.root relativo não reencaminhou para a raiz do plugin" >&2; exit 1; }
mv "$WORK/cfg.bak" .doctor-deploy.toml
printf '%s\n%s\n%s\n%s\n%s\n%s\n' 'export const a1=1' 'export const a2=2' 'export const a3=3' 'export const a4=4' 'export const a5=5' 'export const a6=6' > src/legacy/velho.ts
printf '%s\n' 'export const x = 1' > src/usecases/ignorado.ts
printf '%s\n' 'import { db } from "../infra/datasource"' 'export const run = () => db' > src/usecases/criar.ts
printf '%s\n' 'vi.mock("../infra/datasource")' 'import { run } from "../usecases/criar"' 'startFakeContainer()' > src/usecases/criar.test.ts
mkdir -p reports
printf '%s\n' 'SF:src/usecases/criar.ts' 'DA:1,1' 'DA:2,1' 'end_of_record' > reports/custom.lcov
git add -A && git commit -qm overrides
out=$("$PYTHON" "$PY" layers --started 0)
printf '%s\n' "$out" | grep -q '"diff_cover": "green"' || { echo "coverage_files customizado não foi lido: $out" >&2; exit 1; }
printf '%s\n' "$out" | grep -q '"size": "skip"' || { echo "disable_layers não pulou size: $out" >&2; exit 1; }
printf '%s\n' "$out" | grep -q '"mocks": "green"' || { echo "real_db_patterns não liberou db_modules custom: $out" >&2; exit 1; }
printf '%s\n' "$out" | grep -q '"integration": "green"' || { echo "integration com real_db_patterns/ignore não ficou green: $out" >&2; exit 1; }
# limits.size = 5 sem disable_layers reprova arquivo com 6 linhas fora de ignore_paths
sed 's/"size"//' .doctor-deploy.toml > .dd.tmp && mv .dd.tmp .doctor-deploy.toml
printf '%s\n%s\n%s\n%s\n%s\n%s\n' 'export const a1=1' 'export const a2=2' 'export const a3=3' 'export const a4=4' 'export const a5=5' 'export const a6=6' > src/grande.ts
git add -A && git commit -qm grande
layer size red "limits.size = 5 no .doctor-deploy.toml não reprovou arquivo de 6 linhas"

# discover --json: funciona mesmo sem .doctor-deploy.toml (acusa gap), e lê .doctor-deploy.toml quando existe
newrepo discrepo
rm -f .doctor-deploy.toml
mkdir -p .github/workflows .husky reports
cat > .github/workflows/ci.yml <<'YAML'
jobs:
  test:
    steps:
      - run: bun install --frozen-lockfile
      - run: |
          bun --bun run check
          bun --bun run test:coverage
YAML
printf '%s\n' '{"scripts":{"check":"biome check","typecheck":"tsc --noEmit","test:coverage":"vitest run --coverage"}}' > package.json
: > bun.lock
: > reports/lcov.info
git add -A && git commit -qm base
HOME="$WORK/home" USERPROFILE="$WORK/home" "$GATE" discover --json > "$WORK/disc_missing.json"
"$PYTHON" -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["config"]["exists"] is False and d["config"]["required"] is True, d["config"]
assert any(".doctor-deploy.toml" in g for g in d["gaps"]), d["gaps"]' "$WORK/disc_missing.json"
cat > .doctor-deploy.toml <<'TOML'
version = 1
project = "discrepo"
[gate]
coverage_files = ["reports/lcov.info"]
TOML
HOME="$WORK/home" USERPROFILE="$WORK/home" "$GATE" discover --json > "$WORK/disc.json"
"$PYTHON" -c 'import json,sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["stacks"]["js"]["packageManager"] == "bun", d
assert "husky" in d["git"]["hooksManagers"], d
assert d["config"]["exists"] is True and d["config"]["path"] == ".doctor-deploy.toml", d["config"]
assert "reports/lcov.info" in d["coverage"]["artifacts"], d
cmds = d["ci"][0]["commands"]
assert "bun install --frozen-lockfile" in cmds and "bun --bun run test:coverage" in cmds, cmds
assert d["quality"]["configured"] is False, d
assert "bun --bun run check && bun --bun run typecheck && bun --bun run test:coverage" == d["quality"]["suggested"], d
assert "sem entrypoint de quality" in d["gaps"], d["gaps"]' "$WORK/disc.json"
"$GATE" discover | grep -q "stacks: js"

# monorepo: múltiplos coverages (TS + Go em subpastas), tsconfig por subpacote e audit em subpasta
newrepo monorepo
mkdir -p apps/web/src/services apps/web/coverage apps/api/internal/db apps/api/internal/pedido
printf '%s\n' '{"compilerOptions":{"paths":{"@/*":["./src/*"]}}}' > apps/web/tsconfig.json
printf '%s\n' 'export const db = {}' > apps/web/src/db.ts
printf '%s\n' 'export const a = 1' > apps/web/src/services/a.ts
printf 'module example.com/api\n\ngo 1.22\n' > apps/api/go.mod
printf 'package pedido\n\nfunc Total() int { return 1 }\n' > apps/api/internal/pedido/svc.go
git add -A && git commit -qm base && git update-ref refs/remotes/origin/main HEAD

printf '%s\n' 'import { db } from "@/db"' 'export const a = () => db' > apps/web/src/services/a.ts
printf '%s\n' 'import { a } from "@/services/a"' 'const mem = ":memory:"' > apps/web/src/services/a.test.ts
printf 'package pedido\n\nfunc Total() int {\n\treturn 2\n}\n' > apps/api/internal/pedido/svc.go
printf 'package pedido\n\nimport _ "github.com/DATA-DOG/go-txdb"\n' > apps/api/internal/pedido/svc_test.go
printf '%s\n' 'SF:src/services/a.ts' 'DA:1,1' 'DA:2,1' 'end_of_record' > apps/web/coverage/lcov.info
printf 'mode: set\nexample.com/api/internal/pedido/svc.go:3.18,5.2 1 1\n' > apps/api/coverage.out
git add -A && git commit -qm mono
out=$("$PYTHON" "$PY" layers --started 0)
printf '%s\n' "$out" | grep -q '"diff_cover": "green"' || { echo "monorepo: coverages mesclados não ficaram green: $out" >&2; exit 1; }
printf '%s\n' "$out" | grep -q '"integration": "green"' || { echo "monorepo: tsconfig por subpacote não resolveu: $out" >&2; exit 1; }

# audit em subdiretório (apps/web/package-lock.json) roda no cwd do subpacote
printf '%s\n' '{}' > apps/web/package-lock.json
git add apps/web/package-lock.json && git commit -qm lock
cat > "$FAKE/npm.js" <<'JS'
const fs = require("fs");
if (!fs.existsSync("package-lock.json")) { console.error("cwd errado"); process.exit(2); }
console.error("1 high severity vulnerability");
process.exit(1);
JS
fake_bin npm
( PATH="$FAKE:$PATH" layer audit red "audit em subpasta não rodou no cwd do subpacote" )
rm -f "$FAKE/npm" "$FAKE/npm.cmd" "$FAKE/npm.js"

# modo CI (gate.sh ci): aceita árvore com artefato untracked, --base explícito, --no-quality e --json
newrepo cirepo
mkdir -p src coverage
printf '%s\n' 'export const a = 1' > src/a.ts
printf '%s\n' '{"scripts":{"quality":"node -e \"console.log(123)\""}}' > package.json
git add -A && git commit -qm base && git update-ref refs/remotes/origin/main HEAD
printf '%s\n' 'export const a = 1' 'export const b = 2' > src/a.ts
git add -A && git commit -qm pr
printf '%s\n' 'SF:src/a.ts' 'DA:1,1' 'DA:2,0' 'end_of_record' > coverage/lcov.info
printf '%s\n' 'build artifact' > untracked.log
if "$GATE" ci --base origin/main --no-quality >/dev/null 2>&1; then
  echo "ci não ficou vermelho com hits 0 no lcov" >&2
  exit 1
fi
if "$GATE" ci --base nao-existe --no-quality >/dev/null 2>&1; then
  echo "ci aceitou --base inexistente" >&2
  exit 1
fi
printf '%s\n' 'SF:src/a.ts' 'DA:1,1' 'DA:2,1' 'end_of_record' > coverage/lcov.info
if ! "$GATE" ci --base origin/main --json > "$WORK/ci.json" 2>"$WORK/ci.err"; then
  cat "$WORK/ci.err" >&2
  exit 1
fi
"$PYTHON" -c 'import json,sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
assert p["layers"]["diff_cover"] == "green", p' "$WORK/ci.json"
[ ! -f "$(git rev-parse --git-dir)/doctor-deploy/receipt.json" ] || { echo "ci não deve gravar receipt.json" >&2; exit 1; }

echo "self-check ok"
