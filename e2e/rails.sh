#!/bin/sh
# Ponta a ponta em Rails: app real (rails new), minitest, SimpleCov, mutant, bundle-audit.
# Precisa de: ruby, bundler, as gems rails e bundler-audit. O mutant usa fork(): no Windows a camada pula.
. "$(dirname "$0")/lib.sh"
for tool in ruby bundle rails bundle-audit; do command -v "$tool" >/dev/null || { echo "falta $tool" >&2; exit 1; }; done

step "rails new"
cd "$WORK"
rails new loja --minimal --skip-git --skip-bundle --database=sqlite3 >/dev/null
mkdir -p "$WORK/loja" && cd "$WORK/loja"
git init -qb main && git config user.email e2e@example.com && git config user.name e2e && git config core.autocrlf false
cat >> Gemfile <<'EOF'

group :test do
  gem "simplecov", require: false
  gem "minitest-mock"
  gem "mutant-minitest", require: false
end
EOF
bundle install --quiet
bin/rails g model Pedido cliente:string pago:boolean valor_pago:integer total:integer --no-fixture >/dev/null
"$PYTHON" - <<'EOF'
import pathlib
p = pathlib.Path("test/test_helper.rb"); s = p.read_text()
s = s.replace("parallelize(workers: :number_of_processors)", "parallelize(workers: 1)")
s = s.replace('require "rails/test_help"\n', 'require "rails/test_help"\nrequire "minitest/mock"\nrequire "mutant/minitest/coverage"\n')
p.write_text('require "simplecov"\nSimpleCov.start "rails" do\n  enable_coverage :branch\nend\n\n' + s)
p = pathlib.Path("app/models/pedido.rb")
p.write_text("class Pedido < ApplicationRecord\n  validates :cliente, presence: true\nend\n")
p = pathlib.Path("config/routes.rb"); s = p.read_text()
p.write_text(s.replace("Rails.application.routes.draw do\n", "Rails.application.routes.draw do\n  resources :pedidos, only: :create\n", 1))
EOF
mkdir -p scripts
printf '#!/bin/sh\nset -e\nbin/rails db:test:prepare\nbin/rails test\n' > scripts/quality.sh
# mutant: tests carregados e app com eager load; sqlite num arquivo só -> um job por vez.
cat > test/mutant_helper.rb <<'EOF'
# O rails/test_help liga o autorun do Minitest: dentro do processo do mutant ele rodaria a suíte
# no fim e leria os argumentos do mutant (--since), e todo mutante contaria como vivo.
require "minitest"
def Minitest.autorun = nil
require_relative "test_helper"
Rails.application.eager_load!
Dir[File.join(__dir__, "**/*_test.rb")].each { |file| require file }
EOF
cat > .mutant.yml <<'EOF'
usage: opensource
integration:
  name: minitest
includes:
  - test
requires:
  - ./test/mutant_helper
jobs: 1
matcher:
  subjects:
    - Pagamentos::Cobrar
EOF
# O .gitignore que o rails new gera sem --skip-git, mais a cobertura.
printf '/log/*\n/tmp/*\n/storage/*\n/db/*.sqlite3*\n/.bundle\ncoverage/\n' > .gitignore
bin/rails db:migrate >/dev/null
base_here

step "preflight"
"$GATE" preflight 2>&1 | grep -q "ok: quality = sh scripts/quality.sh"
echo "   ok"

step "cobrança com teste que stuba a persistência"
mkdir -p app/services/pagamentos test/services/pagamentos test/integration
cat > app/services/pagamentos/cobrar.rb <<'EOF'
module Pagamentos
  class Cobrar
    def initialize(pedido)
      @pedido = pedido
    end

    def call
      valor = @pedido.total
      valor = valor * 9 / 10 if valor > 100
      @pedido.update!(pago: true, valor_pago: valor)
    end
  end
end
EOF
cat > app/controllers/pedidos_controller.rb <<'EOF'
class PedidosController < ApplicationController
  def create
    pedido = Pedido.create!(params.require(:pedido).permit(:cliente, :total))
    Pagamentos::Cobrar.new(pedido).call
    render json: pedido, status: :created
  end
end
EOF
cat > test/services/pagamentos/cobrar_test.rb <<'EOF'
require "test_helper"

class Pagamentos::CobrarTest < ActiveSupport::TestCase
  cover "Pagamentos::Cobrar*"

  test "marca pago" do
    pedido = Pedido.new(cliente: "Ana", total: 50)
    pedido.stub(:update!, true) do
      assert Pagamentos::Cobrar.new(pedido).call
    end
  end
end
EOF
commit cobranca
sh scripts/quality.sh >/dev/null 2>&1
expect mocks red "cobrar_test.rb"
expect integration red "pedidos_controller.rb"
expect diff_cover red "cobrar.rb:9"

step "request test que executa sem verificar o valor: só a mutação pega"
cat > test/services/pagamentos/cobrar_test.rb <<'EOF'
require "test_helper"

class Pagamentos::CobrarTest < ActiveSupport::TestCase
  cover "Pagamentos::Cobrar*"

  test "marca pago no banco" do
    pedido = Pedido.create!(cliente: "Ana", total: 50)
    Pagamentos::Cobrar.new(pedido).call
    assert pedido.reload.pago
  end
end
EOF
cat > test/integration/pedidos_test.rb <<'EOF'
require "test_helper"

class PedidosTest < ActionDispatch::IntegrationTest
  cover "Pagamentos::Cobrar*"

  test "cria pedidos" do
    post pedidos_url, params: { pedido: { cliente: "Ana", total: 200 } }
    post pedidos_url, params: { pedido: { cliente: "Bia", total: 100 } }
    assert_response :created
  end
end
EOF
commit "testes fracos"
sh scripts/quality.sh >/dev/null 2>&1
expect mocks green
expect integration green
expect diff_cover green
if [ "$WINDOWS" = 1 ]; then expect mutation skip "fork"; else expect mutation red "Pagamentos::Cobrar#call"; fi

step "testes que verificam valor, borda e falha: mutação verde e recibo"
cat > test/services/pagamentos/cobrar_test.rb <<'EOF'
require "test_helper"

class Pagamentos::CobrarTest < ActiveSupport::TestCase
  cover "Pagamentos::Cobrar*"

  def cobra(total)
    pedido = Pedido.create!(cliente: "Ana", total: total)
    Pagamentos::Cobrar.new(pedido).call
    pedido.reload.values_at(:pago, :valor_pago)
  end

  test "até 100 cobra o total" do
    assert_equal [true, 100], cobra(100)
  end

  test "acima de 100 dá 10% de desconto" do
    assert_equal [true, 90], cobra(101)
    assert_equal [true, 180], cobra(200)
  end

  test "pedido inválido não é cobrado em silêncio" do
    pedido = Pedido.create!(cliente: "Ana", total: 50)
    pedido.cliente = ""
    assert_raises(ActiveRecord::RecordInvalid) { Pagamentos::Cobrar.new(pedido).call }
  end
end
EOF
cat > test/integration/pedidos_test.rb <<'EOF'
require "test_helper"

class PedidosTest < ActionDispatch::IntegrationTest
  cover "Pagamentos::Cobrar*"

  test "cria e cobra com desconto" do
    post pedidos_url, params: { pedido: { cliente: "Ana", total: 200 } }
    assert_response :created
    assert_equal [true, 180], Pedido.last.values_at(:pago, :valor_pago)
  end
end
EOF
commit "testes fortes"
sh scripts/quality.sh >/dev/null 2>&1
if [ "$WINDOWS" = 1 ]; then expect mutation skip; else expect mutation green; fi
"$GATE" prepare >"$WORK/prepare.log" 2>&1 || { tail -5 "$WORK/prepare.log"; git status --short | head -20; exit 1; }
"$GATE" check "$(git rev-parse HEAD)" 2>&1 | grep -q "recibo verde"
echo "   recibo verde"

step "audit: gem com vulnerabilidade conhecida"
printf '\ngem "rack-cors", "0.4.0"\n' >> Gemfile
bundle install --quiet
commit vuln
expect audit red "bundle-audit"

printf '\ne2e rails ok\n'
