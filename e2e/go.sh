#!/bin/sh
# Ponta a ponta em Go: go test real, gremlins real, govulncheck real.
# Precisa de: go, gremlins, govulncheck no PATH.
. "$(dirname "$0")/lib.sh"
for tool in go gremlins govulncheck; do command -v "$tool" >/dev/null || { echo "falta $tool" >&2; exit 1; }; done

newrepo go
printf 'module example.com/loja\n\ngo 1.22\n' > go.mod
mkdir -p internal/db internal/pedido scripts
cat > internal/db/db.go <<'EOF'
package db

import "database/sql"

type Store struct{ DB *sql.DB }
EOF
cat > internal/pedido/service.go <<'EOF'
package pedido

import "example.com/loja/internal/db"

var Store *db.Store

func Total(v int) int {
	return v
}
EOF
cat > internal/pedido/service_test.go <<'EOF'
package pedido

import "testing"

func TestTotal(t *testing.T) {
	if Total(5) != 5 {
		t.Fatal("total")
	}
}
EOF
cat > scripts/quality.sh <<'EOF'
#!/bin/sh
set -e
test -z "$(gofmt -l .)"
go vet ./...
go test -coverprofile=coverage.out ./...
EOF
printf 'coverage.out\n' > .gitignore
base_here

step "preflight reconhece scripts/quality.sh"
"$GATE" preflight 2>&1 | grep -q "ok: quality = sh scripts/quality.sh"
echo "   ok"

step "regra nova sem teste: diff-cover, integração e mutação vermelhos"
cat > internal/pedido/service.go <<'EOF'
package pedido

import "example.com/loja/internal/db"

var Store *db.Store

func Total(v int) int {
	if v > 100 {
		return v * 9 / 10
	}
	return v
}
EOF
commit desconto
sh scripts/quality.sh >/dev/null
expect diff_cover red "service.go:9 hits 0"
expect integration red "internal/pedido/service.go"
if [ "$WINDOWS" = 1 ]; then expect mutation skip "NOT COVERED"; else expect mutation red "service.go:9"; fi

step "teste que executa sem verificar: só a mutação pega"
cat > internal/pedido/service_test.go <<'EOF'
package pedido

import "testing"

func TestTotal(t *testing.T) {
	if Total(5) != 5 || Total(200) == 0 {
		t.Fatal("total")
	}
}
EOF
cat > internal/pedido/service_integration_test.go <<'EOF'
//go:build integration

package pedido

// Banco real por teste: go-txdb abre uma transação no Postgres de teste e faz rollback.
import _ "github.com/DATA-DOG/go-txdb"
EOF
commit "teste fraco"
sh scripts/quality.sh >/dev/null
expect diff_cover green
expect integration green
if [ "$WINDOWS" = 1 ]; then expect mutation skip; else expect mutation red "sobreviveu"; fi

step "teste que verifica as bordas: mutação verde e recibo"
cat > internal/pedido/service_test.go <<'EOF'
package pedido

import "testing"

func TestTotal(t *testing.T) {
	for in, want := range map[int]int{5: 5, 100: 100, 101: 90, 200: 180} {
		if got := Total(in); got != want {
			t.Fatalf("Total(%d) = %d, quero %d", in, got, want)
		}
	}
}
EOF
commit "teste forte"
if [ "$WINDOWS" = 1 ]; then expect mutation skip; else expect mutation green; fi
"$GATE" prepare >"$WORK/prepare.log" 2>&1 || { tail -5 "$WORK/prepare.log"; git status --short | head -20; exit 1; }
"$GATE" check "$(git rev-parse HEAD)" 2>&1 | grep -q "recibo verde"
echo "   recibo verde"

step "audit: dependência com vulnerabilidade chamada pelo código"
cat > internal/pedido/texto.go <<'EOF'
package pedido

import "golang.org/x/text/encoding/unicode"

func Decodifica(s string) string {
	out, _ := unicode.UTF16(unicode.LittleEndian, unicode.UseBOM).NewDecoder().String(s)
	return out
}
EOF
go get golang.org/x/text@v0.3.0 >/dev/null 2>&1
go mod tidy >/dev/null 2>&1
commit vuln
expect audit red "govulncheck"
go get golang.org/x/text@latest >/dev/null 2>&1
go mod tidy >/dev/null 2>&1
commit corrige
expect audit green

printf '\ne2e go ok\n'
