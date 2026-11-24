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
printf '%s\n' 'x' > README.md
git add README.md && git commit -qm base >/dev/null
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
printf '%s\n' 'x' > README.md
git add README.md && git commit -qm base >/dev/null
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
newrepo() { mkdir -p "$TMP/$1" && cd "$TMP/$1" && git init -qb main && git config user.email t@example.com && git config user.name t; }
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

# hook de projeto que delega (scripts/pre-push.sh, como frota-impacto e erp-2f)
mkdir -p "$TMP/hrepo" && cd "$TMP/hrepo"
git init -b main >/dev/null
git config user.email t@example.com
git config user.name t
mkdir -p scripts
printf '#!/bin/sh\n# delega a doctor-deploy/pre-push.sh\necho via-projeto >&2\nexec "%s"\n' "$SKILL/pre-push.sh" > scripts/pre-push.sh
chmod +x scripts/pre-push.sh
git add scripts && git commit -m a >/dev/null
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

echo "self-check ok"
