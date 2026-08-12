---
type: Reference
title: Modelo de dados
description: Tabelas ads, ad_specs e ad_images; preço em centavos e CPU estruturada.
tags: [banco, sqlite, schema]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: banco-doc
    resource: ../specs/04-banco-de-dados.md
    title: Banco de dados
---

# Modelo de dados

SQLite via SQLAlchemy + Alembic. Relações: **1 anúncio → 0..1 specs** e
**1 anúncio → N imagens**.

## `ads` (dados brutos)

`id`, `olx_id`, `title`, `description` (texto cru, sempre preservado),
`price_cents` (**int centavos**; null = sob consulta), `url` (**unique**,
chave de dedup), `city`, `state`, `published_at` (UTC), `scraped_at`,
`extracted_at` (null = specs pendentes), `is_active` (default true; false =
removido da OLX — etapa `check`), `removed_at` (UTC, quando saiu do ar).

## `ad_images`

`id`, `ad_id` (FK, index), `url`, `position` (ordem na galeria).

## `ad_specs` (specs estruturadas, 1:1)

`id`, `ad_id` (FK **unique**), `brand`, `model`, `form_factor`
(mini/sff/tower/notebook/all-in-one), `cpu`, `cpu_family`, `cpu_model`,
`cpu_generation`, `ram_gb`, `storage_gb`, `storage_type` (ssd/hdd/nvme),
`gpu`, `confidence` (0–1), `extraction_method` (regex+llm/llm),
`extracted_at`.

Campos removidos por decisão: `currency` (sempre BRL), `year` (redundante
com a CPU), `condition` (na OLX é essencialmente usado).

## `run_history` (histórico de execuções)

`id`, `source` (`autorun`/`manual`), `started_at` (UTC), `ended_at`,
`duration_sec`, `steps` (JSON), `status` (`running`/`done`/`error`/
`interrupted`), `result` (JSON por etapa), `error`. Criada/finalizada pelo
`RunManager` (best-effort); órfãs `running` → `interrupted` no boot do
`create_app`. Detalhes em [12-autorun-log-historico](../specs/12-autorun-log-historico.md).

## Convenções

- **`NULL` = informação não presente** (a LLM nunca inventa; "não sabe" → null).
- Migrações em `migrations/versions/` — ajustes de schema **sempre** via
  `flask db migrate` + `flask db upgrade`.
- `config.py` resolve `DATABASE_URL` sqlite relativo para absoluto
  (o Flask-SQLAlchemy resolve relativos contra o *instance path*).

Esquema completo em [04-banco-de-dados](../specs/04-banco-de-dados.md).[^banco-doc]

[^banco-doc]: Banco de dados
