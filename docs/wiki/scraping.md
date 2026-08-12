---
type: Reference
title: Scraping da OLX
description: Como a OLX é coletada — seletores, JSON-LD, rate limit e anti-hotlink de imagens.
tags: [scraping, olx]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: scraping-doc
    resource: ../specs/05-scraping.md
    title: Scraping da OLX
---

# Scraping da OLX

Fonte de dados é o **HTML** (não API interna): listagem via seletores
`olx-adcard-*`, detalhe via JSON-LD.

## Listagem

- Query: `/estado-sp?q=<termo>&sf=1` (`sf=1` = mais recentes); paginação
  `&o=N` (1-based); ~32–50 anúncios por página.
- Cartão: `a[data-testid="adcard-link"]` (título/URL), preço em
  `h3.olx-adcard__price`, localização em `p.olx-adcard__location`, data de
  publicação em `p.olx-adcard__date`.
- `olx_id` extraído do fim da URL (`...-<id>`).

## Detalhe

JSON-LD `<script type="application/ld+json">` com `@type: "Product"`:
`offers.price` (int), `description` (usa `<br>`), `image[]` com **todas** as
`contentUrl`.

## Politeness e anti-bot

- **1 requisição/segundo** (lock global no `OlxClient`).
- Retry com backoff em 502/5xx; **403 → para com erro claro** (não insistir).
- User-Agent realista configurável (`.env: USER_AGENT`).
- **Headers completos de navegador** (`BROWSER_HEADERS` em
  `app/scrapers/client.py`): a OLX usa **Cloudflare**, que bloqueia (403)
  requisições com apenas o User-Agent. O `OlxClient` envia
  `Accept`, `Accept-Language`, `Sec-Ch-Ua`, `Sec-Fetch-*` e
  `Upgrade-Insecure-Requests` — validado consistente (200) tanto na
  listagem quanto no detalhe. Ao trocar o `USER_AGENT`, alinhar
  `Sec-Ch-Ua`/`Sec-Ch-Ua-Platform` com o navegador/versão.

## Anti-hotlink de imagens

O CDN de imagens da OLX bloqueia requisições com `Referer` de fora
(`403`). Por isso `base.html` carrega
`<meta name="referrer" content="no-referrer">` — o browser deixa de enviar
`Referer` e as imagens são servidas. **Não remover** essa meta.

## Datas

A data de publicação só existe na listagem ("Ontem, 16:33", "31 de jul,
15:25") e é convertida para **UTC** por `parse_olx_date()` (assume
`America/Sao_Paulo`).

Detalhes de seletores em [05-scraping](../specs/05-scraping.md).[^scraping-doc]

[^scraping-doc]: Scraping da OLX
