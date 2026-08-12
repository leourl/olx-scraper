---
type: Attested Computation
title: Análise de ofertas (benchmark e ranking)
description: Página /offers — benchmark de mercado por (família, geração) e ranking de ofertas por desconto vs mediana, para decisão de compra.
tags: [análise, benchmark, ofertas]
status: stable
runtime: python
parameters:
  - { name: cpu_family, type: string, required: false }
  - { name: cpu_generation, type: integer, required: false }
  - { name: brand, type: string, required: false }
  - { name: ram_min, type: integer, required: false }
  - { name: price_max, type: integer, required: false }
computation: ../../app/services/ad_service.py
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: decisao-ofertas
    resource: ../specs/00-decisoes.md
    title: D-019 — Página de ofertas (atualizada por D-029 — filtro por família)
---

# Análise de ofertas

## Conceito

A página `/offers` responde "onde está o melhor custo-benefício?" com duas
visões:

1. **Benchmark de mercado** — preço de referência por **família e geração** da
   CPU, como percentis **p25 / mediana / p75**. A mediana é o "preço justo";
   abaixo do p25 é um bom negócio; acima do p75 é caro.
2. **Ranking de ofertas** — desconto percentual de cada anúncio contra a
   mediana da **mesma família e geração**:
   `(preço − mediana) / mediana × 100`. Ordenado do maior desconto (mais abaixo
   do mercado) ao pior.

Bandeiras de alerta: `peça/sucata` (keywords inequívocas), `muito barato —
confira` (preço < R$ 200), `muito abaixo do mercado` (≤ −40%),
`acima do mercado` (≥ +50%).

## Computation

A computação sancionada está em `app/services/ad_service.py`
(`price_benchmarks` e `best_deals`), com apoio de `_price_by_family_gen`
e `_flag_deal`. Pontos plotados exigem **preço, família e geração** presentes.

Resumo do algoritmo:

    para cada (família, geração): preços = [price de anúncios com aquela combinação]
      benchmark[família·geração] = quantiles(preços, p25, p50, p75)

    para cada anúncio (com preço, família e geração, filtros aplicados):
      desconto = (preço − mediana[família·geração]) / mediana × 100
      flag = regras de palavras-chave e faixas
    ordena por desconto crescente → top N

As palavras-chave de peça/sucata ficam restritas a termos inequívocos
(sucata, riser, cooler, fonte, placa, bios, "não liga"…) para evitar
falsos positivos com acessórios incluídos (ex.: "teclado e mouse").

## Interpretação

- Um desconto **negativo** = mais barato que o mercado da mesma família e geração.
- Comparar **dentro da mesma família e geração** evita o viés de comparar um
  i3 de 6ª geração com um i5 de 10ª — e também entre vendedores distintos
  (Intel 8ª/2017 ≠ Ryzen série 8/2024).
- Exemplo real (2026-08-05): OptiPlex 3040 i5-6500 (i5, gen 6), 8GB/500GB a
  R$ 650 → ~46% abaixo da mediana de i5 de 6ª geração.
