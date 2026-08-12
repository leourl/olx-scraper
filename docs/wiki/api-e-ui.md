---
type: Reference
title: API e UI
description: Endpoints REST e páginas web do sistema.
tags: [api, ui, flask]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: api-doc
    resource: ../specs/07-api-e-ui.md
    title: API e UI
---

# API e UI

## API REST (`/api`)

| rota | descrição |
|------|-----------|
| `GET /api/ads` | listar/filtrar/ordenar/paginar anúncios |
| `GET /api/ads/<id>` | anúncio + specs + imagens + descrição crua |
| `GET /api/stats` | totais, faixa de preço, contagem por `cpu_family` |
| `POST /api/runs` | inicia run `{terms, region, steps}` (202 / 409 se ativa) |
| `GET /api/runs/<id>` | estado da run (status, progresso, log, resultado) |
| `GET /api/runs/current` | run ativa ou `null` |
| `PUT /api/runs/terms` | salva termos/região em `instance/run_terms.json` |

Filtros em `/api/ads`: `q`, `brand`, `model`, `cpu_family` (inclui **`intel` /
`amd`** para todas as famílias do fabricante, D-030), `cpu_model`,
`gen_min`/`gen_max` (**por família ou fabricante** — ignorados sem `cpu_family`,
D-029/D-030), `ram_min`, `storage_min`, `price_min/max`
(centavos), `form_factor`, `has_specs`, `confidence_min`, `sort`
(newest/price_asc/price_desc/confidence), `page`/`per_page` (máx. 100).

Preço é sempre `price_cents` (int).

## UI web

- `/` — listagem com filtros + paginação.
- `/ads/<id>` — detalhe: galeria, specs, descrição crua, link OLX.
- `/chart` — **gráfico de dispersão preço × geração** (Chart.js, cor por
  marca, clique abre o anúncio). Exige uma **família de CPU ou fabricante**
  (aviso sem família — D-029/D-030).
- `/offers` — **análise de compra** (ver [ofertas](ofertas.md)).
- `/review` — anúncios sem specs ou com confiança < 60%.
- `/run` — **execução de coleta** (scrape/enrich/process) com editor de
  termos e progresso ao vivo (ver [runs](runs.md)).

Estilo com **Pico CSS** (CDN); filtros compartilhados no partial
`_filters.html` (parâmetro `action` = endpoint). Detalhes em
[07-api-e-ui](../specs/07-api-e-ui.md).[^api-doc]

[^api-doc]: API e UI
