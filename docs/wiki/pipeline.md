---
type: Reference
title: Pipeline
description: Fluxo completo de dados — scrape → enrich → process → serve.
tags: [arquitetura, pipeline]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: arquitetura
    resource: ../specs/03-arquitetura.md
    title: Arquitetura
---

# Pipeline

```
fetch (httpx, 1 req/s) → parse (BeautifulSoup / JSON-LD) → banco (SQLite)
    → extração de specs (regex + DeepSeek) → API REST + UI web
```

## Etapas

1. **`scrape`** — busca a listagem da OLX (N páginas) e salva os anúncios
   com dedup por URL. Só busca o **detalhe** de anúncios **novos**
   (descrição + todas as imagens). Anúncios existentes sem descrição exigem
   a etapa 2.
2. **`enrich`** — busca a página de detalhe dos anúncios com
   `description IS NULL`, preenchendo descrição, imagens e preço.
3. **`process`** — extrai specs dos anúncios pendentes
   (`extracted_at IS NULL`): regex primeiro, LLM para o restante.
4. **Serve** — `flask run`: UI (`/`, `/chart`, `/offers`, `/review`,
   `/run`) e API (`/api/ads`, `/api/ads/<id>`, `/api/stats`, `/api/runs`).

As etapas 1–3 vivem em **`app/services/runner.py`** e podem ser executadas
tanto por CLI (`flask scrape/enrich/process`) quanto pela **página `/run`**
(ver [execução de runs](runs.md)), que expõe progresso ao vivo.

## Princípios

- **Camadas separadas**: scraping/extraction (infra) → services (regra de
  negócio) → rotas (apresentação). Rotas nunca fazem scraping/LLM.
- **Raw primeiro**: o anúncio é sempre salvo; specs são enriquecimento.
- **Determinístico antes de IA**: regex resolve campos óbvios; a LLM só
  completa (e reduz custo/alucinação).
- **Fora do request**: scraping e LLM rodam em background (thread do
  `RunManager` ou CLI/batch), nunca dentro do handler HTTP.

Arquitetura detalhada em [03-arquitetura](../specs/03-arquitetura.md).[^arquitetura]

[^arquitetura]: Arquitetura
