# Changelog

## 0.0.1 — 2026-11-24

Primeira versão pública.

- Portão com recibo: `prepare` roda o `quality` do projeto uma vez e grava o verde; `check` aceita o commit com o mesmo conteúdo do testado, salvo `*.md` (squash e rebase passam).
- Hook `pre-push` que confere o recibo, protege só as branches de `PUSH_GATE_BRANCHES` e acha a skill no clone ou no plugin do Claude Code.
- Camadas do diff: diff-cover (100% de linhas e branches alteradas), tamanho, CC, mocks, integração com banco real, lock, audit e mutação.
- Agnóstico: JS/TS, Python, Go e Rails (quality, cobertura, grafo de integração, mocks, audit e mutação por linguagem).
- Setup guiado ([SETUP.md](SETUP.md)) a partir dos repos modelo.
- Self-check multiplataforma e testes ponta a ponta com apps Go e Rails reais.
