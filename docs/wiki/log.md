# Wiki Update Log

## 2026-08-05
* **Creation**: Documentada a [execução de runs](runs.md) (página `/run`,
  editor de termos, `RunManager`, endpoints `/api/runs`).
* **Update**: [API e UI](api-e-ui.md) com os novos endpoints de runs e a
  página `/run`.
* **Update**: [Scraping da OLX](scraping.md) com o fix do Cloudflare (403):
  `BROWSER_HEADERS` completos no `OlxClient`.
* **Update**: [Pipeline](pipeline.md) — etapas centralizadas em
  `app/services/runner.py`, executáveis por CLI ou `/run`.
* **Update**: [Decisões](decisoes.md) com a D-020.
* **Update**: [Qualidade e testes](qualidade.md) — cobertura do runner,
  116 testes.

## 2026-08-05
* **Creation**: Iniciada a wiki do projeto em formato OKF v0.2 (`docs/okf.md`).
  Documentados: visão, pipeline, scraping, extração LLM, modelo de dados,
  API/UI, análise de ofertas, operação e qualidade.
* **Initialization**: Estrutura de conceitos criada com `index.md` e `log.md`.
