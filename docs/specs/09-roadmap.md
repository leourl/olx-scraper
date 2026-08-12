# 09 — Roadmap

## Fases

### Fase 0 — Fundação ✔
- [x] `pyproject.toml` (uv) + `.env.example` + `.gitignore`
- [x] app factory (`create_app`) + `config.py` + `extensions.py`
- [x] modelo `Ad` + migração Alembic inicial
- [x] CLI esqueleto (`flask db upgrade`, comando `scrape`)

**Saída:** app Flask sobe, banco criado.

### Fase 1 — Scraping ✔
- [x] `client.py` (httpx + retry + delay 1 req/s)
- [x] inspeção manual da OLX (HTML/JSON-LD) e seletores (D-006)
- [x] `olx.py` → `RawAd` (listagem + detalhe)
- [x] `ad_service.upsert` (dedup por URL)
- [x] `flask scrape "query"` (validade: 32/32 ads com descrição; retry 502 ok)

**Saída:** coleta anúncios reais sem duplicar; 9 testes offline passando.

### Fase 2 — Extração de specs ✔
- [x] provedor de LLM (D-001: DeepSeek V4 Flash, Responses API, thinking off)
- [x] `extractors/schema.py` (pydantic + JSON Schema) + `regex.py`
- [x] `extractors/llm.py` (JSON Schema mode + validação) + `pipeline.py`
- [x] D-002: colunas normalizadas (tabela `ad_specs`) + migração
- [x] `flask process` + cache por ad (`extracted_at`)
- [x] validado: 32/32 ads extraídos, ~US$0.005 total, 36 testes offline

**Saída:** anúncios com specs extraídos (regex + LLM), auditoria manual pendente.

### Fase 2.5 — Refinamento de schema ✔
- [x] `ad_images` (todas as imagens, D-011) + `published_at` UTC (D-014)
- [x] `cpu_family`/`cpu_model` estruturados (D-012); remoção de currency/year/condition (D-013)
- [x] backfill: 32 specs com CPU normalizada + 115 imagens (re-fetch dos detalhes)
- [x] 50 testes offline; migração Alembic aplicada

### Fase 3 — API e UI ✔
- [x] `ad_service.list_ads` (filtros/ordenação/paginação) + serializers
- [x] blueprint `api`: `/api/ads`, `/api/ads/<id>`, `/api/stats`
- [x] blueprint `main`: listagem + filtros, detalhe, revisão (Pico CSS)
- [x] testes: 62 offline (API + UI + demais)
- [x] validado com dados reais: filtros, stats, páginas 200

**Saída:** consigo filtrar "OptiPlex, RAM ≥ 8GB, ≤ R$ 800" na web/API.

### Fase 4 — Polimento ✔
- [x] `flask enrich` (detalhe dos ads sem descrição) + `list_missing_description`
- [x] enriquecimento real: 97 ads → 129/129 com descrição e specs
- [x] auditoria de qualidade: marca/modelo determinísticos, famílias de CPU
  (core2/pentium/athlon/celeron), enum `all-in-one`; 0 sem marca, 0 CPU sem família
- [x] D-003/D-004 → **manual por ora (WSL)**
- [x] README + docs finais; 71 testes offline

**Saída:** dataset completo e navegável; documentação de uso finalizada.

## Ordem sugerida de trabalho

```
Fase 0 → Fase 1 → Fase 2 → Fase 3 → Fase 4
```

Decisões bloqueantes: **D-006** (antes da Fase 1), **D-001** e **D-002** (antes/na Fase 2).
