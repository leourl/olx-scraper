---
type: Reference
title: Extração de specs (LLM)
description: Como specs são extraídas — regex determinístico + DeepSeek V4 Flash, schema, falhas conhecidas e custo.
tags: [llm, deepseek, extração]
status: stable
generated: { by: opencode/deepseek-v4-flash, at: 2026-08-05T02:10:00Z }
sources:
  - id: llm-doc
    resource: ../specs/06-extracao-llm.md
    title: Extração de specs (LLM + fallback)
---

# Extração de specs

Estratégia: **determinístico primeiro, IA depois**.

```
regex → campos óbvios (RAM, storage, CPU, marca, modelo, formato)
LLM   → completa o restante, validado por pydantic (json_schema)
merge → regex prevalece; LLM nunca sobrescreve campos já resolvidos
```

## Provedor (DeepSeek)

- **Modelo** `deepseek-v4-flash`; **Responses API** (`POST /responses`,
  base `https://api.deepseek.com`).
- Saída estruturada: `text.format = {type: json_schema, name, schema}`
  (schema gerado do pydantic `AdSpec`).
- **Thinking desligado** (`reasoning.effort: none`).
- **Custo real** ~US$0.00016/anúncio (instruções cacheadas, cache hit ~70%);
  ~32 ads ≈ US$0.005.

## CPU estruturada

Do texto livre `cpu` derivam-se, deterministicamente:

- `cpu_family`: i3/i5/i7/i9/ryzen3..9/core2/pentium/athlon/celeron.
- `cpu_model`: número do modelo (ex.: 8500).
- `cpu_generation`: `model // 1000` — Intel geração real, Ryzen série;
  limitado a faixas plausíveis (Intel 1–14, Ryzen 1–9); fora → `None`.

`brand` e `model` também vêm de regex (Dell/Lenovo, OptiPlex/ThinkCentre) e
**sobrescrevem** a LLM no merge.

## Falhas conhecidas e mitigação

| falha | mitigação |
|-------|-----------|
| Modelo ecoa o schema (JSON inválido) | prompt com linha anti-echo; `flask process --ad <id> --force` para retentar |
| Retorna `0` para `ram_gb`/`storage_gb` | `pipeline._merge` e regex sanitizam valores ≤ 0 → `None` |
| `form_factor` fora do enum (ex.: "all-in-one") | enum ampliado conforme necessário |
| Valores lixo (ex.: "Intel Duou Core") | `normalize_cpu` mapeia padrões comuns para famílias |

Anúncio que falha **não perde** a descrição crua e fica visível na página de
revisão. Detalhes em [06-extracao-llm](../specs/06-extracao-llm.md).[^llm-doc]

[^llm-doc]: Extração de specs
