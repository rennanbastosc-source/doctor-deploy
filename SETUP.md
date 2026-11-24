# Setup do portão num repo novo

Leia quando `gate.sh preflight` reprovar. O setup só termina quando o `preflight` sai verde **e** um `quality` real gerou o artefato de cobertura. Não existe marca de "setup feito": o preflight verde é a prova.

Modelos: `frota-impacto` (Next + npm + eslint + Prisma) e `erp-2f` (TanStack Start + bun + biome + Drizzle/libsql). O corpo é o mesmo; muda o runtime. Monte com as ferramentas que o repo já tem. Não adicione framework de teste, linter ou runner se o repo já tem um.

## 1. O `quality`

Um entrypoint na raiz, na ordem que o `gate.sh` procura: `scripts.quality` no `package.json`, `make quality` ou `scripts/quality.py`. Etapas encadeadas com `&&`, da mais rápida para a mais lenta, para o vermelho aparecer cedo:

1. **Estático** — lint/format, com a regra de complexidade do linter em 15 quando ela existe (`complexity: ["error", 15]` no eslint). O biome só tem complexidade cognitiva, que é outra métrica: lá vale a camada `cc` da skill (lizard).
2. **Tipos** — `tsc --noEmit`, `mypy`, `go vet`.
3. **Build** — de propósito antes dos testes: pega o que tipos e testes deixam passar (no frota, um export não-async em arquivo `"use server"` só morria no `next build`).
4. **Testes com cobertura** — unitários e de integração na mesma execução.

| | frota-impacto | erp-2f |
|---|---|---|
| `quality` | `npm run typecheck && eslint && npm run build && vitest run --coverage` | `bun --bun run check && bun --bun run typecheck && bun --bun run build && bun --bun run test:coverage` |

**Sem segredos e sem banco externo.** O `quality` roda igual na máquina e num CI que só faça `install && quality` (o `ci.yml` do frota é exatamente isso). Variável de ambiente que o build exige ganha um valor dummy no próprio config, não um segredo.

## 2. Cobertura

- Formato que a camada diff-cover lê: `coverage/lcov.info` (ou Cobertura XML). No vitest: `provider: "v8"`, `reporter: [..., "lcov"]`.
- Denominador explícito: `include` dos fontes (`src/**/*.{ts,tsx}`), `exclude` de teste, gerado e `.d.ts`. Sem `include`, só o importado entra.
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
   - **UI** (componente, hook): falta um teste que renderize e interaja (`render` + `userEvent`/`fireEvent`).

   Escreva cada um que ele listar e rode de novo até sair limpo. Módulo antigo que o diff tocou entra também: é assim que a suíte full-mock vira integração aos poucos.
5. `gate.sh prepare`. A camada `integration` repete a conta no commit e fica vermelha se sobrou lacuna.

## 4. Testes que pegam bug de produção

Cobertura mede execução, não verificação. Um teste que mocka o banco executa a linha e não prova a query.

- **Unitário:** função pura, sem mock. Se o teste precisa mockar um módulo do próprio projeto, o código está acoplado: extraia a regra para uma função pura e teste essa.
- **Integração:** entra pelo entrypoint real (caller do tRPC, server action, route handler), passa pelo service e chega a um **banco real em memória com as migrations reais**. Afirme o estado do banco no fim, não "o mock foi chamado com".
  - erp-2f: `createDb(":memory:")` + `migrate(db, { migrationsFolder: "./drizzle" })`, injetado com `vi.doMock("#/db/index", ...)` (ver `src/integrations/trpc/__tests__/obras.test.ts`).
  - frota-impacto (Prisma + Postgres): `tests/helpers/pglite-db.ts` sobe um PGlite com o schema do Prisma (`prisma migrate diff --from-empty`) mais `prisma/sql/*.sql`, e o teste injeta o client no seam de `@/lib/prisma` (ver `src/services/ai/rag.service.test.ts`). Sem Docker, sem Neon, sem segredo; ~4s por arquivo.
  - Outro Postgres: o mesmo PGlite (adapter `pglite-prisma-adapter` ou `drizzle-orm/pglite`). Docker disponível: testcontainers.
- **Prove que o teste pega o bug:** quebre a regra no código (tire um filtro do `WHERE`, troque um limite) e veja o teste ficar vermelho. No frota, o teste antigo do RAG, com `$queryRaw` mockado, passava com o filtro de papel removido; o de integração falha.
- **Mock só na fronteira:** rede e SaaS de terceiros, relógio, aleatoriedade, runtime do framework (`next/navigation`, `next/cache`), toasts.
- **Cada bug de produção corrigido ganha um teste que falha sem a correção.**

A skill cobra isso em três camadas: `mocks` (linhas adicionadas em teste que mockam o banco sem banco real no arquivo, ou o próprio módulo testado, são vermelho; outro módulo próprio é aviso), `integration` (módulo tocado sem teste de integração é vermelho) e `mutation` (a prova acima, automática).

**Mutação (opcional, com o consentimento `gate.sh consent mutation yes`):** instale o Stryker como dependência de dev do projeto (`@stryker-mutator/core` e o runner, `@stryker-mutator/vitest-runner` no Vitest) com um `stryker.config.json` que aponte o runner (`"testRunner": "vitest"`, `"coverageAnalysis": "perTest"`). TypeScript 7 (nativo, sem API JS, caso do frota) quebra o Stryker 10 em `ts.parseConfigFileTextToJson`: ponha `"tsconfigFile": "stryker-sem-tsconfig.json"` (arquivo inexistente) para ele não reescrever o tsconfig no sandbox. No frota, o diff de uma linha no `sla.service.ts` mutou o arquivo inteiro em ~50 s. A camada roda `stryker run --mutate <fontes JS/TS do diff> --reporters json` e lê `reports/mutation/mutation.json` (ponha `reports/` no `.gitignore`). Mutante `Survived` ou `NoCoverage` numa linha adicionada é vermelho: o código mudou e nenhum teste quebrou. Conserte com uma asserção que pegue a mudança. Mutante equivalente (a mudança não altera o comportamento) sai com `// Stryker disable next-line <Operador>: <motivo>` na linha, que fica no diff para a review. Timeout de 180s é skip barulhento: diff grande demais para mutar no push.

## 5. Hook

`scripts/pre-push.sh` versionado no repo, que faz os passos próprios do projeto (lembrete de E2E no frota, sync de migrations no erp-2f), exporta `PUSH_GATE_BRANCHES=<branch principal>` e delega para `${DOCTOR_DEPLOY_HOOK:-$HOME/.agents/skills/doctor-deploy/pre-push.sh}`. Sem a skill, bloqueia só o push para a branch principal. Depois, `gate.sh install-hook` grava o shim que executa esse script.

## 6. AGENTS.md

Uma seção curta: o que o `quality` roda e por quê nessa ordem, o piso que só sobe, a política de mock da seção 4, e que quem executa o portão é a skill (não rode o `quality` à mão antes do push).

## 7. Fechar

1. Rode o `quality` uma vez e grave o piso medido.
2. `gate.sh preflight` verde.
3. Mostre o diff e pare. Quem commita é o usuário; tree suja recusa o recibo, então o `prepare` só vem depois do commit.
