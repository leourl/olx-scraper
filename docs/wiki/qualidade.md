---
type: Reference
title: Qualidade e testes
description: Testes offline, auditoria das specs e métricas do dataset.
tags: [testes, qualidade, auditoria]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: llm-doc
    resource: ../specs/06-extracao-llm.md
    title: Extração de specs (auditoria)
---

# Qualidade e testes

## Testes

`uv run pytest` — **100% offline**:

- LLM simulada via `httpx.MockTransport` (sem rede).
- HTML real de exemplo em `tests/fixtures/` (`search.html`, `ad_good.html`,
  `ad_bad.html`).
- Banco SQLite in-memory (`tests/conftest.py`); `tests/seed.py` popula
  dados para testes de API/UI.
- Cobertura: regex, schema pydantic, pipeline, LLM mock, scraping, service,
  API, UI, chart, ofertas, enrich, **runner** (`tests/test_runner.py`:
  etapas com `MockTransport`, `RunManager` síncrono, persistência de
  termos).
- **116 testes**, 100% offline.

pandas é dependência apenas de **dev** (análise ad-hoc em `/tmp/opencode`);
a aplicação não deve importá-lo.

## Auditoria de qualidade das specs

Realizada sobre o dataset completo (129+ anúncios). Problemas encontrados e
corrigidos:

- `brand`/`model` ausentes em anúncios óbvios → extração determinística de
  marca/modelo que prevalece no merge.
- CPUs antigas (Pentium, Core 2 Duo, Athlon) sem família → `normalize_cpu`
  estendido.
- `form_factor: "all-in-one"` rejeitado → enum ampliado.
- Erros de digitação do vendedor (ex.: "i5 18500") → faixa de geração
  limitada (Intel 1–14), valor impossível vira `None`.

## Métricas do dataset (2026-08-05)

- ~268 anúncios mapeados, todos com specs; preço em centavos; imagens
  coletadas de todas as páginas de detalhe.
- Cobertura de campos: preço ~98%, geração ~33%, RAM ~67%, storage ~64%
  (o restante não é mencionado nos anúncios).
