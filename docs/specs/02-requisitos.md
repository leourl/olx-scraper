# 02 — Requisitos

## Requisitos funcionais

### Coleta (scraping)

- RF-01: Buscar anúncios na OLX por query (ex.: "dell optiplex", "thinkcentre")
- RF-02: Coletar de cada anúncio: título, descrição, preço, link, cidade,
  **todas as imagens**, **data de publicação**
- RF-03: Não coletar anúncios repetidos (dedup por URL)
- RF-04: Respeitar um atraso mínimo entre requisições (politeness)

### Extração de specs

- RF-05: Extrair por anúncio: marca, modelo, formato (mini/SFF/tower),
  **CPU com família e modelo estruturados**, RAM (GB), armazenamento (GB e tipo), GPU
- RF-06: Extração via LLM com fallback determinístico (regex) para campos óbvios
- RF-07: Guardar a descrição crua sempre (nunca perder o dado original)
- RF-08: Reprocessar um anúncio sem custo se o spec já foi extraído antes (cache)

### Consulta

- RF-09: API REST para listar, buscar e filtrar anúncios + specs
- RF-10: Interface web para visualizar anúncios e filtros de busca
- RF-11: Ver anúncios sem specs / com baixa confiança para revisão manual

## Requisitos não-funcionais

| Id | Requisito | Observação |
|----|-----------|------------|
| RNF-01 | Custo de LLM mínimo | cache por URL, texto truncado, modelo barato |
| RNF-02 | Tolerante a falha de parse | ad salvo mesmo se extração falhar |
| RNF-03 | Tolerante a mudanças de HTML | seletores isolados em um módulo |
| RNF-04 | Execução local simples | uv + SQLite, sem serviço externo |
| RNF-05 | Processamento fora do request HTTP | LLM/scraping via CLI/batch |
| RNF-06 | Testável offline | scraper e extração testados com fixtures |

## Decisões pendentes

- [x] ~~D-001~~ → **DeepSeek V4 Flash** · ~~D-002~~ → **colunas normalizadas**
- [x] ~~D-005~~ → **1 req/s + max_pages 5** · ~~D-006~~ → **HTML + JSON-LD**
- [x] ~~D-011..D-014~~ → **imagens, published_at, CPU estruturada, remoções**
- [ ] D-003 Reprocessamento automático vs manual
- [ ] D-004 Agendamento do scraping
