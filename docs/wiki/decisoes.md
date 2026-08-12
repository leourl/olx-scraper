---
type: Reference
title: Decisões de projeto
description: Índice das decisões registradas no formato D-XXX.
tags: [decisões, histórico]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: decisoes-doc
    resource: ../specs/00-decisoes.md
    title: Decisões do projeto
---

# Decisões de projeto

O registro único e completo das decisões (com contexto, alternativas e
impacto) vive em `docs/specs/00-decisoes.md`.[^decisoes-doc] Este conceito é
um índice de navegação.

## Fechadas

- **D-001** — Provedor de LLM: DeepSeek V4 Flash (Responses API), thinking off.
- **D-002** — Specs em colunas normalizadas (tabela `ad_specs`).
- **D-005** — Rate limit 1 req/s; `max_pages` default 5.
- **D-006 / D-006b** — Fonte: HTML + JSON-LD `Product`; detalhe por anúncio.
- **D-007** — Preço em `int` centavos.
- **D-008** — Região via `--region` (default `estado-sp`).
- **D-009** — `scrape` faz listagem + detalhes no mesmo run.
- **D-010** — Campo `confidence` na extração.
- **D-011** — Todas as imagens em tabela `ad_images` (1:N).
- **D-012** — CPU estruturada: `cpu_family`/`cpu_model`.
- **D-013** — Remoção de `currency`, `year`, `condition`.
- **D-014** — `published_at` em UTC (parse de datas relativas da OLX).
- **D-015** — API expõe `price_cents`; UI com Pico CSS; sem CSV.
- **D-016** — `cpu_generation` (Intel geração / Ryzen série), filtro `gen_min/max`.
- **D-017** — Anti-hotlink de imagens via `<meta name="referrer" content="no-referrer">`.
- **D-018** — Gráfico preço × geração (`/chart`, Chart.js).
- **D-019** — Página `/offers` (benchmark + ranking).
- **D-020** — Página `/run`: termos em JSON (`instance/run_terms.json`),
  histórico em memória, uma run por vez, thread + polling (ver
  [runs](runs.md)).
- **D-029** — Filtro de CPU **por família** (Intel × Ryzen): `gen_min/max` só
  valem com `cpu_family`; `/chart` exige família; `/offers` compara por
  (família, geração) (ver [filtro-cpu-por-familia](../specs/17-filtro-cpu-por-familia.md)).
- **D-030** — **Fabricante de CPU**: `cpu_family=intel|amd` filtra todas as
  famílias do fabricante e vale como domínio de escala da geração (ver
  [filtro-por-fabricante](../specs/18-filtro-por-fabricante.md)).

## Em aberto

- **D-003 / D-004** — Reprocessamento e agendamento: **manual por ora** (WSL).

[^decisoes-doc]: Decisões do projeto (registro completo)
