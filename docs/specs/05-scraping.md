# 05 — Scraping da OLX

## Contexto e riscos

- OLX tem **anti-bot** e pode mudar o HTML sem aviso — seletores ficam isolados
  num módulo só para facilitar correção.
- Respeitar `robots.txt` e atraso mínimo entre requisições.
- **D-006 (em aberto):** parsear o HTML da página de listagem ou usar os
  endpoints JSON que a OLX usa internamente. Decisão tomada ainda nesta fase.

## Dados que queremos do anúncio (RawAd)

```
title        -> str
description  -> str   (cru, truncada para LLM depois)
price        -> int   (determinístico via regex)
url          -> str   (canônico, usado no dedup)
olx_id       -> str   (extraído da url quando possível)
city / state -> str
image_url    -> str
```

## Comportamento do `client.py`

- **curl_cffi** com `impersonate` de Chrome (`SCRAPER_IMPERSONATE`, default
  `chrome`) — o Cloudflare bloqueia o fingerprint TLS/HTTP2 do `httpx` com 403
  mesmo com User-Agent realista (D-025); a impersonação emula um TLS de browser
  real e passa na checagem.
- timeout configurável (`SCRAPER_TIMEOUT`)
- retry com backoff exponencial (3 tentativas) para 429/5xx
- `User-Agent` realista configurável (`.env`)
- atraso mínimo configurável entre requests (`SCRAPER_DELAY`, default 1s)
- política de 403/block: levanta `ScrapeBlockedError` → a run termina com
  status **`blocked`** e o autorun respeita um **cooldown** persistido
  (`SCRAPER_BLOCK_COOLDOWN_MINUTES`, default 60; `instance/scrape_block.json`)
  antes de tentar de novo (D-025). Run bem-sucedida limpa o bloqueio.

## Estrutura confirmada (inspeção em 2026-08-05)

Páginas são **HTML estático** (com dados de listagem via classes `olx-*` e
`data-testid`, e dados de detalhe via JSON-LD). Sem JS/anti-bot interativo:
respostas 200 com fetch de fingerprint de browser real (curl_cffi impersonate).
O `robots.txt` fica atrás de Cloudflare (403) e o Cloudflare responde 403 para
stacks de TLS não-browser (ex.: httpx) — a política é prática: fingerprint
real + delay mínimo + cooldown em bloqueio.

### Página de listagem (`/estado-sp?q=...&sf=1`)

- Query: `q=<termo>&sf=1` (sf=1 = "Mais recentes"). Paginação: `&o=N` (1-based).
- 32 anúncios por página (confirmado: "1 - 32 de 32 resultados").
- Cartão = `div.olx-adcard__content`; link dentro de `a[data-testid="adcard-link"]`.
- Contador total: `p` com texto `1 - 32 de 32 resultados`
  (classes `typo-body-small font-regular text-neutral-110`).

| dado        | seletor |
|-------------|---------|
| link/url    | `a[data-testid="adcard-link"]` → `@href` |
| título      | mesmo `a` → `@title` (ou `h2.olx-adcard__title`) |
| preço       | `h3.olx-adcard__price` (ex.: "R$ 2.850") |
| cidade/bairro | `p.olx-adcard__location` (ex.: "São Paulo, Bairro Central") |
| data        | `p.olx-adcard__date` (ex.: "Ontem, 16:33") → `parse_olx_date()` → UTC |
| olx_id      | extrair do final da URL: `-<id>` (ex.: `...-1523803879`) |

### Página de detalhe (`sp.olx.com.br/...`)

- Dados confiáveis em `<script type="application/ld+json">` com `@type: "Product"`:
  - `identifier` → olx_id; `name` → título; `url`
  - `offers.price` → preço como **int** (ex.: "2850")
  - `description` → descrição (usa `<br>` como separador de linha)
  - `image[]` → **todas** as imagens (`contentUrl`), salvando a lista completa
    na tabela `ad_images` (D-011)
- Localização: `span.typo-body-small` com regex `^[A-Z]{2}$` dentro do bloco
  `div.flex.flex-col` → bairro + "Cidade, UF, CEP".
- Obs.: a data de publicação não aparece na página de detalhe (só na listagem).
- Verificar se `description` do JSON-LD é sempre completa (sem truncamento)
  para descrições longas; senão pegar do texto da página.

## Perguntas em aberto

- [x] ~~D-006: HTML vs endpoints JSON internos~~ → **decidido: HTML** (listagem
  via seletores `olx-*`/`data-testid`; detalhe via JSON-LD `Product`)
- [x] ~~D-005: limite de páginas~~ → **decidido: 1 req/s + max_pages default 5**
- [x] ~~Paginação~~ → **confirmada**: `&o=N` (validado: `o=2` com retry de 502 ok)
- [ ] Tratar anúncios "patrocinados"/duplicados na listagem? → hoje: dedup por `olx_id`
- [ ] Filtrar por região (ex.: só SP) → hoje: via `--region` no caminho da URL
