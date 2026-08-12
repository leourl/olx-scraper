---
type: Playbook
title: Operação e comandos
description: Comandos CLI, configuração e fluxo de migrações para operar o sistema.
tags: [cli, operação, configuração]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: operacao-doc
    resource: ../specs/08-operacao.md
    title: Operação
---

# Operação e comandos

## Comandos (sempre via `uv run`)

| comando | o que faz |
|---------|-----------|
| `uv sync` | instala dependências (venv `.venv`) |
| `uv run flask db upgrade` | aplica migrações |
| `uv run flask scrape "query" --region estado-sp [--max-pages 5] [--no-details]` | coleta listagem + detalhes dos anúncios novos |
| `uv run flask enrich [--limit N]` | busca detalhe dos anúncios sem descrição |
| `uv run flask process [--limit N] [--ad <id> --force]` | extrai specs dos pendentes |
| `uv run flask run` | sobe UI + API em :5001 |
| `uv run pytest` | testes offline |

Ordem típica de atualização: `scrape` → `enrich` (se houver sem descrição)
→ `process` → visualizar. Tudo isso também dá para fazer pela página **`/run`**
(termos um por linha, checkboxes das etapas, progresso ao vivo).

## Página /run

- Endereço: `http://localhost:5001/run` (nav "Rodar").
- Termos ficam salvos em `instance/run_terms.json` (1 termo por linha).
- Etapas: `scrape` (coleta), `enrich` (descrições), `process` (specs via LLM).
- Uma run por vez; a UI acompanha o progresso com polling
  (`GET /api/runs/<id>`, 1 req/s). Estado da run é só em memória (perde-se ao
  reiniciar o servidor).
- Requer `DEEPSEEK_KEY` no `.env` para a etapa `process`.

## Migrações

Após alterar `app/models/*`:

```
uv run flask db migrate -m "descrição"
uv run flask db upgrade
```

SQLite usa `batch_alter_table` para remover/adicionar colunas.

## Configuração (`.env`)

`DEEPSEEK_KEY` (obrigatória p/ `process`), `DATABASE_URL`, `USER_AGENT`,
`SCRAPER_DELAY` (1.0), `SCRAPER_MAX_PAGES` (5), `DEEPSEEK_BASE_URL`,
`DEEPSEEK_MODEL`, `LLM_MAX_CHARS`, `LLM_TIMEOUT`, `LLM_REASONING_EFFORT`.

### Host/porta do `flask run`

O comando `uv run flask run` usa o CLI do Flask, que **não** lê `run.py`
(esse só vale para `uv run python run.py`). Para expor na rede:

```
FLASK_RUN_HOST=0.0.0.0
FLASK_RUN_PORT=5001
```

Ambas as variáveis vão no `.env`. `0.0.0.0` torna o servidor acessível pela
LAN (IP da máquina) — cuidado com `debug=True` fora da rede local.

## Agendamento

Manutenção **manual por ora** — ambiente WSL do Windows (sem cron
persistente). Roda-se `scrape`/`process` quando desejado. Detalhes em
[08-operacao](../specs/08-operacao.md).[^operacao-doc]

[^operacao-doc]: Operação
