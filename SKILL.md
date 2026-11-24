---
name: doctor-deploy
description: >-
  Doctor Deploy: portão local de push/merge com recibo. Use no /dd e quando
  o usuário pedir push, merge, squash-merge, publish, deploy, "faz o push",
  "commita e pusha", "mergear", "sobe pra main", "prepara o merge",
  "prepara a PR" ou "deixa pronto pro merge". No preparo roda o portão uma
  vez e grava o verde. No push/merge não repete se o SHA empurrado for o
  testado ou só difere dele em *.md. Não use para commit sem push.
---

# Doctor Deploy

Canônico em `~/.agents/skills/doctor-deploy`. Todo `gate.sh` deste texto e do [SETUP.md](SETUP.md) é `"$HOME/.agents/skills/doctor-deploy/gate.sh"`, rodado de dentro do repo do projeto; no Windows, pelo sh do Git: `"/c/Program Files/Git/bin/sh.exe" "$HOME/.agents/skills/doctor-deploy/gate.sh" prepare`. Os scripts são POSIX, LF (no Windows rodam no sh do Git for Windows, o mesmo dos hooks) e chamam `python3`, ou `python` quando `python3` não roda (atalho da Microsoft Store), em modo UTF-8. Não copie esta skill para o repo. Não instale ferramenta com `pip install --user` (PEP 668 no Ubuntu).

O portão existe para não subir vermelho. Repetir um verde já gravado é o bug, não rigor. Um recibo válido é o verde.

## Modos

- **Preparo** ("prepara o merge", "prepara a PR", "deixa pronto"): rode o portão, grave o recibo, pare. Não faça push e não crie a PR: "prepara a PR" é deixar o commit pronto para ela. `gh pr create` só quando o usuário pedir a PR.
- **Push/merge** ("faz o push", "mergear", "squash-merge", "sobe pra main"): confira o recibo primeiro. Se for válido, faça a operação e não rode o quality de novo.
- **Push para branch não protegida** (fora de `PUSH_GATE_BRANCHES`, padrão `main,master`): o hook não age, e o agente não roda `check` nem `prepare`. O portão protege a branch principal; na PR, quem cobra é o CI do projeto, quando existe. Se o usuário pedir o preparo, é o modo Preparo.
- **Commit sem push:** não dispare esta skill.

## Antes de tudo

0. `gate.sh preflight` — confere o repo atual sem rodar nada: entrypoint de quality, hook, base, artefato de cobertura e consentimentos. Só a falta de quality (sem `doctor-deploy.allowNoQuality`) reprova; o resto é aviso, porque no portão vira skip. **Reprovou, ou avisou de hook ou cobertura na primeira vez neste repo: leia [SETUP.md](SETUP.md) e monte o setup antes de seguir.** O setup termina quando o preflight sai verde e um `quality` real gerou o lcov.
1. `gate.sh install-hook` — só cria o shim se `.git/hooks/pre-push` não existir. Hook de projeto não se sobrescreve.
2. `gate.sh needs-consent` — se imprimir `NEED_CONSENT`, pergunte uma vez. `gate.sh consent lizard yes|no`: `yes` não instala sozinho: instale no usuário (`pipx` ou `uv tool`), grave a versão no consentimento, não atualize no portão. Sem `pipx`/`uv`, diga isso e deixe a camada em skip. Lizard nunca vira dependência do repo. `gate.sh consent mutation yes|no` só é perguntado quando o projeto já tem o Stryker (dependência de dev do projeto, decisão do dono no setup, [SETUP.md](SETUP.md) §4); `no` desliga a camada nesta máquina.

## Preparo

`gate.sh prepare`

- Tree suja, inclusive untracked, recusa o recibo. Não commite por conta própria.
- Quality é o entrypoint do projeto, nesta ordem: `npm`/`pnpm`/`bun`/`yarn run quality` (o lock da raiz escolhe o gerenciador), `make quality` (se o `make` existir), `sh scripts/quality.sh`, `python3 scripts/quality.py`. Sem entrypoint: vermelho. A exceção é explícita e por clone: `git config doctor-deploy.allowNoQuality true`. Com ela, `quality` entra em `skipped` e o `check` só aceita esse recibo enquanto a chave existir.
- Antes de escrever os testes do diff: `gate.sh cc` (CC das funções novas ou alteradas, inclusive o não commitado; acima de 15, quebre a função) e depois `gate.sh integration` (módulos tocados sem teste de integração; escreva os que ele listar). Ordem completa em [SETUP.md](SETUP.md) §3.
- Depois do quality, as camadas olham o diff contra `origin/master`, `origin/main`, `master` ou `main`. Sem fetch. Sem base, a camada é skip, não vermelho.
- Verde grava `$(git rev-parse --git-dir)/doctor-deploy/receipt.json` com SHA testado, SHA da base, versão e camadas puladas. Imprima as puladas. Pare.

## Push / merge

`gate.sh check <sha-que-vai-subir>`

Válido se o recibo está verde, a versão bate e o conteúdo do SHA é o testado, salvo `*.md`. Ancestralidade não conta: merge, squash e rebase do commit testado passam; se a base andou, o código alheio aparece no delta e invalida. A mensagem do commit não conta: `docs:` com `.ts` ou `.json` invalida. O risco aceito é um `.md` que a suíte lê (no frota, `docs/invariantes` vs `mapa-invariantes.test.ts`) passar sem rerodar.

Se o check sair 0, faça o push ou o merge. Não chame quality. O hook faz a mesma leitura e sai 0.

Se sair não-zero, rode `prepare` de novo e só então empurre. O hook, sem recibo, só roda o portão se `HEAD` for o `local_sha` e a tree estiver limpa. Caso contrário bloqueia: não testa a working tree no lugar de outro commit.

Deleção de branch não passa pelo portão e não desliga o portão dos outros refs do mesmo push.

## Camadas do diff

| Camada | Vermelho | Skip barulhento |
|---|---|---|
| Quality | entrypoint falhou | sem entrypoint |
| Diff-cover | linha alterada de fonte presente no artefato fresco (lcov, Cobertura, coverprofile do Go, SimpleCov) com hits 0 ou com branch não exercitada | sem artefato, artefato velho, ou sem base. Arquivo fora do relatório é aviso, não verde silencioso e não vermelho |
| Tamanho | arquivo novo de fonte acima de 400 linhas, ou editado que sai de ≤400 para >400 | teste, gerado, sem base |
| CC | função nova com CC > 15, ou alterada que cruza 15, se `lizard` existir | sem lizard, sem aceite, teste, gerado |
| Mocks | linha adicionada em teste que mocka o módulo de banco (sem banco real no mesmo arquivo) ou o próprio módulo testado; stub de query/persistência do ActiveRecord (`receive(:find)`, `:update!`...); `sqlmock` | sem base. Mock de outro módulo próprio é aviso |
| Integração | service, action, rota, router, controller ou repositório tocado (em Rails, também model e job; em Go, pacote que importa o banco) sem teste que o alcance e use banco real; componente ou hook tocado sem teste que renderize e interaja. "Alcança" segue imports (TS/JS, Python), pacotes do `go.mod` e constantes do Zeitwerk | sem base |
| Lock | manifest da raiz mudou no diff e nenhum lock correspondente mudou | sem base |
| Audit | high/critical quando o lock mudou e a ferramenta existe (`npm`/`pnpm`/`yarn`/`bun audit`, `govulncheck`, `bundle-audit`, `cargo audit`) | ferramenta ausente, rede, lock intocado |
| Mutação | mutante vivo (`Survived`/`NoCoverage`) do Stryker numa linha adicionada de fonte JS/TS. Equivalente sai com `// Stryker disable next-line <Operador>: <motivo>` | sem Stryker em `node_modules`, recusa, sem base, sem relatório, ou timeout de 180s |

No diff a barra é 100% das linhas e branches que o relatório lista: hits 0 ou branch não exercitada é vermelho. Linha que o relatório não lista não é executável comprovada. O piso global é do projeto (`thresholds`), medido no setup, e só sobe.

Tetos 400 e 15 são constantes da skill. Não crie config por repo. Não abaixe threshold, não alargue exclude, não apague teste para passar.

## Reparo

Vermelho: no máximo 3 rodadas. Conserte a causa, rode `prepare` de novo. Reparo muda a árvore, o recibo velho morre. Falha pré-existente fora do diff vai em commit separado. Não use `--no-verify` salvo emergência declarada, e só depois das 3 rodadas. Diga o motivo numa linha.

## O que esta skill não cobre

Merge pela UI do GitHub e `gh pr merge` não passam pelo hook. O recibo cobre o agente e o `git push`. Convenção de PR, spec, schema em produção e prova de deploy ficam no `AGENTS.md` do projeto. Se esse arquivo for mais rígido, ele ganha.

Se o `AGENTS.md` disser que o check de CI é o recibo da PR (frota-impacto), não rode `prepare` em PR: o portão seria pago duas vezes. O recibo vale para o push direto na branch principal.

Fora do setup ([SETUP.md](SETUP.md)), não edite o repo do projeto por conta da skill.
