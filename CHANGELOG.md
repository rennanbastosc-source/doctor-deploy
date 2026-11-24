# Changelog

## Não lançado

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
- Setup guiado ([SETUP.md](SETUP.md)) a partir dos repos modelo.
- Self-check multiplataforma no CI (Ubuntu, macOS e Windows); validado ponta a ponta com apps Go e Rails reais antes da release.
