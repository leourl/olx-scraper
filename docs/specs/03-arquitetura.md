# 03 — Arquitetura

## Visão geral

```
 +-------------------------+     +--------------------------+     +-------------------+
 |    Scraping (CLI/task)  |     |   Extração de specs      |     |  Persistência     |
 |                         |     |                          |     |                   |
 | fetch (httpx)           |     | regex (rápido, grátis)   |     | SQLite + SQLAlchemy|
 |  -> parse HTML (bs4)    | --> |  + LLM p/ o que faltar   | --> | Ad (raw)          |
 |  -> dados brutos        |     |  + validação (pydantic)  |     | AdSpec (estrut.)  |
 +-------------------------+     +--------------------------+     +-------------------+
                                                                         |
                                                                         v
 +---------------------------+   REST /api/ads <----------------- +------+--------+
 |   Web (Flask)             |   + filtros + specs                |  Services      |
 |   UI (templates)          |                                     |  upsert/dedup  |
 +---------------------------+                                     +---------------+
```

## Princípios

1. **Camadas separadas** — scraping (infra) → extração (infra/IA) → serviços
   (regra de negócio) → rotas (apresentação). Rotas nunca fazem scraping/LLM.
2. **App factory** (`create_app()`) com blueprints (`main` p/ UI, `api` p/ REST).
3. **Raw primeiro** — todo anúncio coletado é salvo; specs são enriquecimento posterior.
4. **Determinístico antes de IA** — regex resolve preço e campos óbvios; LLM entra
   só onde o texto é despadronizado. Fallback sempre existe.
5. **Fora do request** — scraping e LLM rodam via CLI/batch; a web só consulta o banco.

## Componentes

### `app/scrapers/`
- `client.py` — httpx: timeout, retry com backoff, user-agent, atraso mínimo
- `base.py` — contrato de scraper (fetch + parse)
- `olx.py` — implementação OLX: parse de HTML → `RawAd`

### `app/extractors/`
- `schema.py` — modelos pydantic (`AdSpec`, campos com validação)
- `regex.py` — extração determinística (preço, RAM, CPU óbvios)
- `llm.py` — prompt + chamada à LLM com saída JSON validada
- `pipeline.py` — orquestra: regex → LLM (para o que faltou) → merge → `AdSpec`

### `app/services/`
- `ad_service.py` — upsert por URL, salvar raw, gravar specs, listar/filtrar

### `app/models/`
- `ad.py` — `Ad` (raw) e `AdSpec` (estruturado) — detalhes no doc 04

### `app/blueprints/`
- `main/` — UI: listagem, busca, filtros, detalhe do anúncio
- `api/` — REST: `/api/ads`, `/api/ads/<id>`, `/api/ads/search`

### `app/cli.py` e `app/tasks.py`
- comandos Click: `flask scrape`, `flask process`; agendamento futuro

## Fluxos principais

### 1. Coletar + extrair
1. `flask scrape "dell optiplex são paulo"` busca N páginas na OLX
2. cada anúncio → raw salvo (dedup por URL)
3. `flask process` roda a extração de specs (regex + LLM) nos raw sem specs

### 2. Consultar
1. usuário abre a UI ou chama a API
2. rotas chamam `ad_service.list/filter` com os filtros (marca, RAM ≥, preço ≤)
3. resposta = anúncio + specs (ou sinalização de "sem specs")

## Diagramas (em branco até detalharmos)

- [ ] Diagrama de classes dos modelos (doc 04)
- [ ] Diagrama de sequência do fluxo de extração (doc 06)
