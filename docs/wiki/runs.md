---
type: Reference
title: Execução de runs (página /run)
description: Como executar scrape/enrich/process pela UI — editor de termos, etapas, progresso ao vivo e RunManager.
tags: [runs, automação, ui]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T17:00:00Z }
sources:
  - id: decisao-runs
    resource: ../specs/00-decisoes.md
    title: D-020 — Página /run (execução via UI)
  - id: runner-code
    resource: ../../app/services/runner.py
    title: runner.py (lógica central das runs)
---

# Execução de runs (página /run)

A página **`/run`** (nav "Rodar") roda `scrape`, `enrich` e `process` pela
UI — sem CLI — com progresso ao vivo. A lógica é a mesma do CLI: **todas as
etapas vivem em `app/services/runner.py`** e `cli.py` apenas delega. **Não
duplicar lógica de coleta em novos lugares — usar `runner.py`.**

## Editor de termos

- Textarea com **1 termo por linha** (ex.: `dell optiplex sff`).
- **Bloqueado por padrão** (`readonly`); botão **Editar** libera a edição.
- **Salvar** persiste via `PUT /api/runs/terms` em
  `instance/run_terms.json` (JSON com `terms`, `region`, `saved_at`).
- **Cancelar** descarta alterações e restaura o último valor salvo.
- Os termos carregados abrem a página já preenchidos (`load_terms`).

## Etapas

| etapa | o que faz | função |
|-------|-----------|--------|
| `scrape` | listagem da OLX (N páginas) + detalhes dos anúncios novos | `run_scrape` |
| `enrich` | busca detalhe dos anúncios sem descrição | `run_enrich` |
| `check` | verifica se anúncios ativos ainda estão publicados (404/410 → removido) | `run_check` |
| `process` | extrai specs (regex + LLM) dos pendentes | `run_process` |

Cada função aceita um callback `on_progress(step, done, total, message)`
que alimenta o progresso da run.

## Histórico de execuções (quadro de log)

Cada run gerenciada pelo `RunManager` (autorun ou manual via página `/run`) é
**persistida na tabela `run_history`** (`services/run_history_service.py`):
- `RunManager.start` cria a entrada (`source='autorun'|'manual'`) **antes** de
  rodar (best-effort: falha → warning, run segue sem histórico, sem job zumbi);
- ao terminar, `_run`/`execute` finalizam (`done`/`error` + `result` por etapa +
  duração), com `rollback` antes para não commitar estado pendente de anúncios;
- órfãs `running` (processo morreu no meio) viram `interrupted` no boot.
- **CLI** (`flask scrape/process/enrich`) não passa pelo `RunManager` → não
  registra.

Visão: **quadro de log na página `/run`** (id, tipo, início UTC, duração,
status, resumo por etapa, erro) + endpoint **`GET /api/runs/history`**.

## Execução e progresso

- `RunManager` (instância em `app.extensions["run_manager"]`) roda a run em
  **thread de fundo**; estado fica **em memória** (`RunJob`), perdido ao
  reiniciar o servidor.
- **Uma run por vez**: se já houver uma ativa, `POST /api/runs` responde
  **409**.
- A UI faz **polling** em `GET /api/runs/<id>` a cada 1s: barra de
  progresso, mensagem da etapa atual e log acumulado.
- Ao concluir, `status: done` com os resultados por etapa; em erro,
  `status: error` + mensagem (ex.: 403 da OLX, `DEEPSEEK_KEY` ausente).

## API

| rota | descrição |
|------|-----------|
| `POST /api/runs` | inicia run `{terms, region, steps}` → `{id}` (202 / 409) |
| `GET /api/runs/<id>` | estado da run (`status`, `percent`, `log`, `result`, `error`) |
| `GET /api/runs/current` | run ativa ou `null` |
| `GET /api/runs/history` | histórico persistido (`run_history`), mais recente primeiro |
| `PUT /api/runs/terms` | salva termos/região em `instance/run_terms.json` |

O `POST /api/runs` também salva os termos automaticamente.
