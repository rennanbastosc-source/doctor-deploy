# Setup do portão num repo novo

Leia quando `gate.sh preflight` reprovar. O setup só termina quando o `preflight` sai verde **e** um `quality` real gerou o artefato de cobertura. Não existe marca de "setup feito": o preflight verde é a prova.

Stacks de referência: Next + npm + eslint + Prisma e TanStack Start + bun + biome + Drizzle/libsql. O corpo é o mesmo; muda o runtime. Em Go, Rails, Python ou qualquer outra stack vale o mesmo contrato com as ferramentas da linguagem (tabela da seção 1). Monte com as ferramentas que o repo já tem. Não adicione framework de teste, linter ou runner se o repo já tem um.

A skill é agnóstica: ela não sabe rodar lint, build ou instalação da sua stack. Quem sabe é o `quality` do projeto, e o trabalho do setup é fazê-lo **reproduzir localmente os portões do CI**. As camadas do diff (seção 2 em diante) só olham o que o CI costuma não olhar.

## 0. Leia o CI antes de montar

1. Abra a config de CI do repo (`.github/workflows/*.yml`, `.gitlab-ci.yml`, `.circleci/`, `azure-pipelines.yml`, `bitbucket-pipelines.yml`, `Jenkinsfile`...). Liste cada job que bloqueia o merge e cada comando que ele roda: instalação, lint/format, tipos, build, testes, cobertura, checagens de schema/migration, audit.
2. Cada comando que roda sem segredo e sem serviço externo entra no `quality` **com as mesmas flags**. Se o CI roda `eslint --max-warnings 0`, o `quality` também roda.
3. **Instalação travada:** o comando de instalação do CI é o portão do lock (manifest mudou sem o lock acompanhar). A skill não cobra isso; o `quality` reproduz: `npm ci`, `pnpm install --frozen-lockfile`, `yarn install --frozen-lockfile` (Yarn 1) ou `--immutable` (Yarn 2+), `bun install --frozen-lockfile`, `go mod tidy -diff` (Go 1.23+), `bundle install` com `BUNDLE_FROZEN=true`, `uv lock --check`, `poetry check --lock`, `cargo build --locked`.
4. **Audit:** a camada `audit` da skill roda quando um lock da raiz muda (`npm`/`pnpm`/`yarn`/`bun audit`, `govulncheck`, `bundle-audit`, `cargo audit`). Se o CI usa outra ferramenta (`pip-audit`, `osv-scanner`, Snyk sem token...), ela entra no `quality`, sabendo que precisa de rede.
5. O que não dá para reproduzir (segredo, serviço externo, deploy, E2E contra ambiente) fica fora e vai na lista do fechamento (seção 7).
6. **Sem CI:** monte o `quality` pela seção 1 e, no fechamento, proponha ao usuário um CI que rode `install travado && quality` e marque esse job como obrigatório na proteção da branch. É o CI que cobra merge feito pela interface do GitHub ou por `gh pr merge`, que não passam pelo hook.

## 1. O `quality`

Um entrypoint na raiz, na ordem que o `gate.sh` procura: `scripts.quality` no `package.json`, `make quality` (só se o `make` existir na máquina), `scripts/quality.sh` (POSIX, roda no sh do Git no Windows) ou `scripts/quality.py`. Fora de JS, prefira `scripts/quality.sh`: o Windows não tem `make`. Etapas encadeadas com `&&`, da mais rápida para a mais lenta, para o vermelho aparecer cedo:

0. **Instalação travada**, se o CI a tem (seção 0).
1. **Estático** — lint/format **em modo checagem** (`prettier --check`, `ruff format --check`, `gofmt -l`), nunca `--write`/`--fix`: o portão recusa o recibo se o `quality` reescrever arquivo versionado. Regra de complexidade do linter em 15 quando ela existe (`complexity: ["error", 15]` no eslint). O biome só tem complexidade cognitiva, que é outra métrica: lá vale a camada `cc` da skill (lizard).
2. **Tipos** — `tsc --noEmit`, `mypy`, `go vet`.
3. **Build** — de propósito antes dos testes: pega o que tipos e testes deixam passar (no Next, um export não-async em arquivo `"use server"` só morre no `next build`).
4. **Testes com cobertura** — unitários e de integração na mesma execução.

| | Next + npm + Prisma | TanStack Start + bun + Drizzle |
|---|---|---|
| `quality` | `npm run typecheck && eslint && npm run build && vitest run --coverage` | `bun --bun run check && bun --bun run typecheck && bun --bun run build && bun --bun run test:coverage` |

| | Go | Rails |
|---|---|---|
| `scripts/quality.sh` | `test -z "$(gofmt -l .)" && go vet ./... && go build ./... && go test -coverprofile=coverage.out ./...` | `bundle exec rubocop && bin/rails db:test:prepare && bundle exec rspec` (ou `bin/rails test`), com SimpleCov ligado no `spec_helper`/`test_helper` (`enable_coverage :branch`) |

| | Python |
|---|---|
| `scripts/quality.sh` | `ruff check . && ruff format --check . && mypy . && pytest --cov --cov-branch --cov-report=xml` (gera `coverage.xml`) |

**Sem segredos e sem banco externo.** O `quality` roda igual na máquina e num CI que só faça `install && quality`. Variável de ambiente que o build exige ganha um valor dummy no próprio config, não um segredo.

**Árvore limpa depois do `quality`.** Todo artefato que ele gera (cobertura, build, `reports/`, `.mutant/`, cache do linter) vai no `.gitignore`. Arquivo novo ou alterado depois do `quality` recusa o recibo.

## 2. Cobertura

- Arquivos que a camada diff-cover lê, na raiz do repo: `coverage/lcov.info` ou `lcov.info`; Cobertura XML em `coverage.xml`, `cobertura.xml`, `coverage/cobertura.xml` ou `coverage/cobertura-coverage.xml`; o coverprofile do Go (`coverage.out`, `cover.out`, sem branches); o `coverage/.resultset.json` do SimpleCov (linhas e, com `enable_coverage :branch`, branches). A ferramenta da stack grava num desses nomes; se não der, use o conversor dela para lcov ou Cobertura. Exemplos: vitest com `provider: "v8"` e `reporter: [..., "lcov"]`; pytest-cov com `--cov-branch --cov-report=xml`; `cargo llvm-cov --lcov --output-path lcov.info`.
- O artefato tem de ser regerado a cada `quality`: o portão só aceita arquivo mais novo que o início da execução.
- **Denominador explícito**, senão arquivo que nenhum teste carrega fica fora do relatório e vira só aviso: `include` dos fontes no vitest/jest (`src/**/*.{ts,tsx}`), `track_files "{app,lib}/**/*.rb"` no SimpleCov, `source`/`--cov=<pacote>` no coverage.py, `-coverpkg=./...` no Go. `exclude` de teste, gerado e `.d.ts`.
- **Piso global = o valor medido no primeiro run, arredondado para baixo**, em `thresholds`. Só sobe. Nunca uma meta inventada: piso acima do real deixa o repo vermelho no dia 1 e empurra para teste cosmético.
- **No diff, a barra é 100% de linhas e de branches**, e quem cobra é a camada diff-cover da skill, não o `thresholds`. Linha alterada com hits 0 ou com branch não exercitada é vermelho.
- Foque em branches, não em linhas, e nas regras de domínio: dinheiro, datas, transições de estado, guards de permissão, parsers, queries. UI de passagem e gerado ficam no piso global.

## 3. Ordem do trabalho no diff

A cada execução da skill, nesta ordem:

1. Escreva o código.
2. **`gate.sh cc`** — CC das funções novas ou alteradas, inclusive o que ainda não foi commitado. Função acima de 15: quebre antes de testar. Teste escrito em cima de uma função de CC 25 congela esse formato e precisa de 25+ caminhos.
3. **Unitários** das funções puras que o diff criou ou mudou (seção 4).
4. **`gate.sh integration`** — lista cada módulo tocado que ainda não tem teste de integração:
   - **dados** (service, action, rota, router, controller, repositório): falta um teste que importe o módulo e rode contra banco real em memória;
   - **UI** (componente, hook em JS/TS): falta um teste que renderize e interaja (`render` + `userEvent`/`fireEvent`).

   Escreva cada um que ele listar e rode de novo até sair limpo. Módulo antigo que o diff tocou entra também: é assim que a suíte full-mock vira integração aos poucos.
5. `gate.sh prepare`. A camada `integration` repete a conta no commit e fica vermelha se sobrou lacuna.

## 4. Testes que pegam bug de produção

Cobertura mede execução, não verificação. Um teste que mocka o banco executa a linha e não prova a query.

- **Unitário:** função pura, sem mock. Se o teste precisa mockar um módulo do próprio projeto, o código está acoplado: extraia a regra para uma função pura e teste essa.
- **Integração:** entra pelo entrypoint real (caller do tRPC, server action, route handler), passa pelo service e chega a um **banco real em memória com as migrations reais**. Afirme o estado do banco no fim, não "o mock foi chamado com".
  - Drizzle + libsql: `createDb(":memory:")` + `migrate(db, { migrationsFolder: "./drizzle" })`, injetado com `vi.doMock("<módulo do db>", ...)`.
  - Prisma + Postgres: um helper de teste sobe um PGlite com o schema do Prisma (`prisma migrate diff --from-empty`) mais o SQL cru do projeto, e o teste injeta o client no seam do módulo do Prisma. Sem Docker, sem banco hospedado, sem segredo; segundos por arquivo.
  - Outro Postgres: o mesmo PGlite (adapter `pglite-prisma-adapter` ou `drizzle-orm/pglite`). Docker disponível: testcontainers.
  - Rails: o banco de teste do próprio Rails já é real (`rails_helper`/`ActiveSupport::TestCase`, transação por teste). Request spec entra pela rota e chega no model; não stube `find`/`where`/`save`/`update!`.
  - Go: Postgres de teste com `go-txdb` (transação com rollback por teste), `pgtestdb`, `embedded-postgres` ou testcontainers; SQLite `:memory:` quando o app é SQLite. Sem `sqlmock`. O teste fica na pasta do pacote (ou atrás de `//go:build integration`).
  - Python: Django já sobe o banco de teste real (`django.test.TestCase`, `@pytest.mark.django_db` do pytest-django). SQLAlchemy: SQLite `:memory:` com as migrations do alembic (`command.upgrade(cfg, "head")`), `pytest-postgresql` (`postgresql_proc`) ou testcontainers. Não use `patch` no módulo de banco.
  - Outra stack: o portão reconhece banco real no arquivo de teste por `:memory:`, `migrate(`, `testcontainers`, `pglite`, os helpers de Rails, Go e Python acima. Se a stack sobe banco real de outro jeito, monte o teste com um desses (testcontainers serve para qualquer linguagem) e avise o usuário que é uma lacuna da skill.
- **Prove que o teste pega o bug:** quebre a regra no código (tire um filtro do `WHERE`, troque um limite) e veja o teste ficar vermelho. Um teste com `$queryRaw` mockado passa com o filtro removido; o de integração falha.
- **Mock só na fronteira:** rede e SaaS de terceiros, relógio, aleatoriedade, runtime do framework (`next/navigation`, `next/cache`), toasts.
- **Cada bug de produção corrigido ganha um teste que falha sem a correção.**

A skill cobra isso em três camadas: `mocks` (linhas adicionadas em teste que mockam o banco sem banco real no arquivo, ou o próprio módulo testado, são vermelho; outro módulo próprio é aviso), `integration` (módulo tocado sem teste de integração é vermelho) e `mutation` (a prova acima, automática).

**Mutação (opcional, com o consentimento `gate.sh consent mutation yes`):** instale o Stryker como dependência de dev do projeto (`@stryker-mutator/core` e o runner, `@stryker-mutator/vitest-runner` no Vitest) com um `stryker.config.json` que aponte o runner (`"testRunner": "vitest"`, `"coverageAnalysis": "perTest"`). TypeScript 7 (nativo, sem API JS) quebra o Stryker 10 em `ts.parseConfigFileTextToJson`: ponha `"tsconfigFile": "stryker-sem-tsconfig.json"` (arquivo inexistente) para ele não reescrever o tsconfig no sandbox. Um diff de uma linha num service muta o arquivo inteiro em cerca de um minuto. A camada roda `stryker run --mutate <fontes JS/TS do diff> --reporters json` e lê `reports/mutation/mutation.json` (ponha `reports/` no `.gitignore`). Mutante `Survived` ou `NoCoverage` numa linha adicionada é vermelho: o código mudou e nenhum teste quebrou. Conserte com uma asserção que pegue a mudança. Mutante equivalente (a mudança não altera o comportamento) sai com `// Stryker disable next-line <Operador>: <motivo>` na linha, que fica no diff para a review. Timeout de 180s é skip barulhento: diff grande demais para mutar no push.

**Mutação em Go:** `go install github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0` (ferramenta da máquina, não do repo). A camada roda `gremlins unleash --diff <base> --timeout-coefficient 30` e lê o JSON: `LIVED` ou `NOT COVERED` numa linha adicionada é vermelho. Sem config no repo. No Windows, o gremlins até a 0.6.0 marca tudo `NOT COVERED` (bug de caminho) e a camada pula.

**Mutação em Rails:** gems `mutant-minitest` (ou `mutant-rspec`) no grupo `:test`, mais um `.mutant.yml` e um helper que carrega app e testes. No minitest, cada teste declara o que cobre (`cover "Pagamentos::Cobrar*"`, com `require "mutant/minitest/coverage"` no `test_helper`). A camada roda `bundle exec mutant run --since <base>`; mutante vivo num método tocado é vermelho, e o aviso mostra a linha mutada.

```yaml
# .mutant.yml
usage: opensource      # ou commercial: é a licença do mutant, decisão do dono do repo
integration:
  name: minitest
includes: [test]
requires: [./test/mutant_helper]
jobs: 1                # SQLite num arquivo só: um mutante por vez
matcher:
  subjects: [Pagamentos::Cobrar, Pedido]   # nomes exatos: "Pagamentos*" pega também Pagamentos::CobrarTest
```

```ruby
# test/mutant_helper.rb
require "minitest"
def Minitest.autorun = nil   # sem isto, o autorun do rails/test_help lê os argumentos do mutant e todo mutante "sobrevive"
require_relative "test_helper"
Rails.application.eager_load!
Dir[File.join(__dir__, "**/*_test.rb")].each { |file| require file }
```

Ponha `.mutant/` no `.gitignore`: o mutant grava resultados lá, e a árvore suja recusa o próximo recibo. O mutant precisa de `fork()`: no Windows a camada pula (use WSL).

## 5. Hook

`scripts/pre-push.sh` versionado no repo, que faz os passos próprios do projeto (lembrete de E2E, sync de migrations), exporta `PUSH_GATE_BRANCHES=<branch principal>` e delega para `${DOCTOR_DEPLOY_HOOK:-$HOME/.agents/skills/doctor-deploy/pre-push.sh}` (instalado como plugin do Claude Code, exporte `DOCTOR_DEPLOY_HOOK` ou use o shim do `install-hook`, que acha o plugin mais novo sozinho). Sem a skill, bloqueia só o push para a branch principal. Depois, `gate.sh install-hook` grava o shim que executa esse script.

**`core.hooksPath`** (husky, lefthook, pre-commit e afins): o git lê os hooks dessa pasta, não de `.git/hooks`. O `install-hook` e o `preflight` seguem o `core.hooksPath`, mas não sobrescrevem o hook que o gerenciador já criou. Nesse caso, ponha a delegação no hook que o gerenciador executa, por exemplo uma linha em `.husky/pre-push` (husky 9) que chame `scripts/pre-push.sh` ou `${DOCTOR_DEPLOY_HOOK}`, repassando o stdin. O `preflight` avisa enquanto o pre-push não citar a skill.

## 6. `.doctor-deploy.json` (opcional)

Por padrão o portão é zero-config. Se o repositório fugir das convenções (branch principal diferente de `main`/`master`, paths de cobertura customizados, pasta de banco/integração fora do padrão, ou necessidade de ajustar tetos), crie `.doctor-deploy.json` na raiz:

```json
{
  "baseBranch": "develop",
  "protectedBranches": ["main", "develop"],
  "quality": "just quality",
  "limits": { "size": 500, "cc": 20 },
  "coverageFiles": ["reports/lcov.info"],
  "ignorePaths": ["src/legacy/**"],
  "dbModules": ["(^|[/.])datasource$"],
  "realDbPatterns": ["createTestContainer\\("],
  "integration": {
    "dataDirs": ["usecases"],
    "uiDirs": ["widgets"],
    "ignore": ["src/components/icons/**"]
  },
  "disableLayers": ["mutation"]
}
```

Todo campo é opcional; `disableLayers` registra a camada como `skip` explícito no recibo.

## 7. AGENTS.md

Uma seção curta: o que o `quality` roda e por quê nessa ordem, o piso que só sobe, a política de mock da seção 4, e que quem executa o portão é a skill (não rode o `quality` à mão antes do push).

## 8. Fechar

1. Rode o `quality` uma vez e grave o piso medido.
2. `gate.sh preflight` verde, e `git status` limpo depois do `quality` (fora o próprio setup).
3. Diga ao usuário quais portões do CI o `quality` reproduz e quais ficaram de fora e por quê (seção 0). Sem CI, proponha um.
4. Mostre o diff e pare. Quem commita é o usuário; tree suja recusa o recibo, então o `prepare` só vem depois do commit.
