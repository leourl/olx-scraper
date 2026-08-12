---
okf_version: "0.2"
---
# Wiki do projeto — OLX Monitor

Conhecimento operacional e de domínio do sistema que coleta anúncios de
Dell OptiPlex / Lenovo ThinkCentre na OLX, extrai specs com LLM e expõe
UI + API. Cada conceito segue o Open Knowledge Format v0.2 (`docs/okf.md`).

## Visão

* [Visão geral](visao.md) - objetivo, escopo e como o sistema ajuda na decisão de compra.
* [Pipeline](pipeline.md) - fluxo completo: scrape → enrich → process → serve.
* [Decisões de projeto](decisoes.md) - índice das decisões D-XXX registradas em `docs/specs/00-decisoes.md`.

## Sistema

* [Scraping da OLX](scraping.md) - seletores, rate limit, anti-hotlink de imagens.
* [Extração de specs (LLM)](extracao-llm.md) - DeepSeek V4 Flash, schema, falhas conhecidas, custo.
* [Modelo de dados](banco.md) - tabelas `ads`, `ad_specs`, `ad_images` e a CPU estruturada.
* [API e UI](api-e-ui.md) - endpoints REST e páginas web.
* [Análise de ofertas](ofertas.md) - benchmark de mercado e ranking de custo-benefício.

## Operação

* [Operação e comandos](operacao.md) - comandos CLI, configuração e migrações.
* [Execução de runs](runs.md) - página `/run`, editor de termos, progresso ao vivo e `RunManager`.
* [Qualidade e testes](qualidade.md) - testes offline, auditoria e métricas de dados.
