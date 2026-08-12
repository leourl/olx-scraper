---
type: Reference
title: Visão geral
description: Objetivo, escopo e como o sistema apoia a decisão de compra de OptiPlex/ThinkCentre usados.
tags: [projeto, objetivo]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: objetivo
    resource: ../specs/01-objetivo.md
    title: Objetivo e escopo
---

# Visão

O projeto nasce de uma necessidade pessoal: comprar um **Dell OptiPlex** ou
**Lenovo ThinkCentre** usado na OLX, onde preços são inconsistentes e as
descrições de hardware não seguem padrão.

O sistema:

1. **Coleta** anúncios da OLX (título, descrição, preço, imagens, data).
2. **Persiste** em um banco SQLite.
3. **Extrai specs estruturados** (marca, modelo, CPU, RAM, storage…) usando
   regex determinístico + LLM (DeepSeek), com o texto cru sempre preservado.
4. **Expõe** UI web e API REST para filtrar, comparar e decidir.

## Como apoia a decisão de compra

- Busca com filtros por specs (RAM, geração, preço, formato).
- Gráfico **preço × geração** (ver [análise de ofertas](ofertas.md)).
- Benchmark de mercado por geração e ranking de ofertas por desconto vs
  mediana.

Não-objetivos: login/integração com a OLX, suporte a múltiplos sites,
alertas em tempo real. Detalhes em [objetivo](../specs/01-objetivo.md).[^objetivo]

[^objetivo]: Objetivo e escopo
