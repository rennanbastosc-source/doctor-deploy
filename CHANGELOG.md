# Changelog

## 0.0.3 — 2026-11-24

Compatibilidade nativa com plugins do Antigravity (`agy plugin install`) e marcador obrigatório em TOML:

- Estrutura de plugin Antigravity + fallback na raiz: adicionados `plugin.json` na raiz e `skills/doctor-deploy/{SKILL.md,gate.sh,pre-push.sh}` reencaminhando para a raiz do plugin, permitindo `skills: ["doctor-deploy"]` no `agy plugin install` sem quebrar clones diretos em `~/.agents/skills/doctor-deploy`.
- Shim e `SKILL.md` com fallback para a raiz: suporte tanto a `~/.gemini/config/plugins/doctor-deploy/skills/doctor-deploy` quanto à raiz `~/.gemini/config/plugins/doctor-deploy` (e no cache do Claude Code).
- Manifesto obrigatório `.doctor-deploy.toml` (com fallback `doctor-deploy.toml`, no estilo `.ai-memory.toml`): substitui `.doctor-deploy.json`, valida `project` e chaves antes de `preflight`/`install-hook`/`cc`/`integration`/`prepare`/`ci`/`check`/`pre-push`, adiciona `[plugin] root` para reencaminhar para a raiz do plugin e o comando `gate.sh init` (com migração automática de `.doctor-deploy.json`).

## 0.0.2 — 2026-11-24

Camada agnóstica à stack de CI, autoconfigurável por agente, com suporte a monorepos e execução em CI:

- Portabilidade e desempenho (#38): fallback do plugin Antigravity no shim, `encoding="utf-8"` explícito, suporte a `uv.lock`/`poetry.lock`/`Pipfile.lock` via `pip-audit` e `added_lines_map` em lote.
- Base dinâmica e manifesto opcional `.doctor-deploy.json` (#39): detecção via `origin/HEAD`, `DOCTOR_DEPLOY_BASE` e overrides gerais (`quality`, `baseBranch`, `protectedBranches`, `limits`, `coverageFiles`, `ignorePaths`, `dbModules`, `realDbPatterns`, `integration`, `disableLayers`) com validação fail-closed.
- Descoberta estruturada para agentes (#40): comando `gate.sh discover [--json]` inspecionando Git, CI, stacks, gerenciadores de hook, cobertura e `gaps`.
- Suporte a monorepos e múltiplos subprojetos (#41): cobertura mesclada de subdiretórios com rebase de caminhos, `tsconfig.json`/`go.mod` por subpacote, resolução Python aninhada e `audit` por subdiretório.
- Modo CI e GitHub Action (#42): `gate.sh ci [--base <ref>] [--no-quality] [--json]` e `action.yml` para validação em PRs no GitHub Actions.
- Setup orientado ao CI: o `SETUP.md` manda ler a config de CI e reproduzir cada portão no `quality` (instalação travada, lint em modo checagem, audit), e propor um CI quando não houver.
- Camada `deps` removida: lock fora de sincronia é portão do CI (`npm ci`, `--frozen-lockfile`, `go mod tidy -diff`...), reproduzido no `quality`. Fecha #29.
- `prepare` recusa o recibo se HEAD mudar ou a árvore sujar durante o portão (#27).
- `install-hook` e `preflight` seguem `core.hooksPath` (#28).
- Hook fecha o stdin do quality: o push com vários refs protegidos não perde refs (#36).
- Caminho de pasta oculta no relatório (`.storybook/`) casa com o diff (#30).
- Audit: erro da ferramenta sem achado vira skip, não vermelho (#31).
- UI só em JS/TS (#32); banco real de Django/pytest-django/SQLAlchemy reconhecido (#33); `coverage.xml` lido (#34).
- CC: homônimos e `(anonymous)` valem o maior (#35); timeout do lizard e erro inesperado numa camada dizem a camada (#37).

## 0.0.1 — 2026-11-24

Primeira versão pública.

- Portão com recibo: `prepare` roda o `quality` do projeto uma vez e grava o verde; `check` aceita o commit com o mesmo conteúdo do testado, salvo `*.md` (squash e rebase passam).
- Hook `pre-push` que confere o recibo, protege só as branches de `PUSH_GATE_BRANCHES` e acha a skill no clone ou no plugin do Claude Code.
- Camadas do diff: diff-cover (100% de linhas e branches alteradas), tamanho, CC, mocks, integração com banco real, lock, audit e mutação.
- Agnóstico: JS/TS, Python, Go e Rails (quality, cobertura, grafo de integração, mocks, audit e mutação por linguagem).
- Setup guiado ([SETUP.md](SETUP.md)) com exemplos por stack.
- Self-check multiplataforma no CI (Ubuntu, macOS e Windows); validado ponta a ponta com apps Go e Rails reais antes da release.
