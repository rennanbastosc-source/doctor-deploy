# Doctor Deploy

> **EN:** A local push/merge gate for coding agents. It runs your project's `quality` once, records a receipt for the tested commit, and checks the diff for coverage (100% of changed lines and branches), complexity, mocked databases, real-DB integration tests, mutation and dependency audit. Works with JS/TS, Python, Go and Rails. Docs below are in Portuguese.

Portão local de push e merge para agentes de código (Claude Code, Codex e qualquer agente que leia skills em `~/.agents/skills`). Ele roda o `quality` do projeto **uma vez**, grava um recibo do commit testado e, no push, confere o recibo em vez de rodar tudo de novo. Repetir um verde já gravado é o bug, não rigor.

O `quality` é do projeto e reproduz localmente os portões do CI (instalação travada, lint, tipos, build, testes); o setup guiado do [SETUP.md](SETUP.md) monta isso a partir da config de CI. Além dele, o portão olha o **diff** contra a branch principal:

| Camada | Vermelho quando |
|---|---|
| Diff-cover | linha alterada com hits 0 ou branch não exercitada (lcov, Cobertura, coverprofile do Go, SimpleCov) |
| Tamanho | arquivo de fonte novo acima de 400 linhas, ou editado que passa de 400 |
| CC | função nova com complexidade ciclomática acima de 15, ou alterada que cruza 15 ([lizard](https://github.com/terryyin/lizard)) |
| Mocks | teste novo que mocka o banco (`vi.mock` do módulo de banco, stub de `find`/`update!` do ActiveRecord, `sqlmock`) ou o próprio módulo testado |
| Integração | service, action, rota, controller, model ou repositório tocado sem um teste que o alcance e use banco real; componente tocado sem teste que renderize e interaja |
| Audit | vulnerabilidade quando o lock mudou (`npm`/`pnpm`/`yarn`/`bun audit`, `govulncheck`, `bundle-audit`, `cargo audit`) |
| Mutação | mutante vivo numa linha alterada: o código mudou e nenhum teste quebrou (Stryker, gremlins, mutant) |

Ferramenta ausente, sem rede ou estouro de tempo vira **skip com aviso**, nunca verde silencioso.

## Linguagens

| | JS/TS | Python | Go | Rails |
|---|---|---|---|---|
| Quality | `npm`/`pnpm`/`bun`/`yarn run quality` | `scripts/quality.py` | `make quality` ou `scripts/quality.sh` | `scripts/quality.sh` |
| Cobertura | lcov, Cobertura | lcov, Cobertura (`coverage.xml`) | `coverage.out` | `coverage/.resultset.json` (SimpleCov) |
| Grafo de integração | imports, `tsconfig` paths | imports | pacotes do `go.mod` | constantes (Zeitwerk) e nome do teste |
| Mutação | Stryker | — | gremlins | mutant (Linux e macOS) |

O setup nasceu de dois apps em produção: um Next + Prisma (Postgres, testado com PGlite) e um TanStack Start + Drizzle (libsql).

## Instalação

**Clone (qualquer agente, caminho fixo):**

```sh
git clone https://github.com/rennanbastosc-source/doctor-deploy ~/.agents/skills/doctor-deploy
```

**Plugin do Claude Code:**

```
/plugin marketplace add rennanbastosc-source/doctor-deploy
/plugin install doctor-deploy@doctor-deploy
```

Requisitos: `git`, `sh` (no Windows, o do Git for Windows) e Python 3. Opcionais, com aviso quando faltam: `lizard`, a ferramenta de audit e o mutador da linguagem.

## Uso

Peça ao agente "prepara o merge", "faz o push" ou `/dd`. Na primeira vez num repo, ele roda o preflight e monta o setup pelo [SETUP.md](SETUP.md): o `quality`, a cobertura, o hook e a política de testes.

À mão, de dentro do repo do projeto (`<skill>` é `~/.agents/skills/doctor-deploy` ou a pasta do plugin):

```sh
sh <skill>/gate.sh preflight      # confere o repo sem rodar nada
sh <skill>/gate.sh install-hook   # instala o pre-push (não sobrescreve hook do projeto)
sh <skill>/gate.sh cc             # complexidade das funções do diff, antes dos testes
sh <skill>/gate.sh integration    # módulos do diff sem teste de integração
sh <skill>/gate.sh prepare        # quality + camadas; verde grava o recibo
sh <skill>/gate.sh check <sha>    # recibo vale para esse commit?
```

O recibo vale para o commit com o mesmo conteúdo do testado, salvo `*.md`. Squash e rebase do commit testado passam; se a base andou, o código alheio aparece no delta e o recibo cai. As regras completas estão no [SKILL.md](SKILL.md).

## Limites

- Merge pela interface do GitHub e `gh pr merge` não passam pelo hook. Para PR, quem cobra é o CI do projeto.
- **Windows:** o mutant precisa de `fork()` e pula. O gremlins até a 0.6.0 marca todo mutante como `NOT COVERED` no Windows (a chave da cobertura usa `\` e a posição usa `/`); o portão detecta isso e pula. No Linux e no macOS os dois rodam.
- A mutação do mutant tem granularidade de método (`--since` seleciona os métodos tocados); Stryker e gremlins são por linha.

## Desenvolvimento

```sh
sh self-check.sh   # casos do recibo, hook e de todas as camadas, com ferramentas falsas onde precisa
```

O CI roda o self-check em Ubuntu, macOS e Windows. O `scripts/quality.sh` da skill é o próprio self-check: ela passa pelo próprio portão. Os testes ponta a ponta com apps Go e Rails reais (ferramentas reais: go test, gremlins, govulncheck, SimpleCov, mutant, bundle-audit) ficam só na máquina do mantenedor, fora do repo e do CI, e rodam antes de cada release.

## Licença

[MIT](LICENSE)
