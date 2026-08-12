# 06 — Extração de specs (LLM + fallback)

## Objetivo

Transformar descrições livres ("Optiplex 7050, i5 8500, 16gb ddr4, ssd 512,
bem conservado") em **specs estruturados e validados**, sem depender de regex
sozinho.

## Provedor (D-001) — DeepSeek V4 Flash

- **Endpoint:** `POST https://api.deepseek.com/responses` (Responses API)
- **Modelo:** `deepseek-v4-flash` · **Auth:** `Authorization: Bearer <DEEPSEEK_KEY>`
- **Saída estruturada:** `text.format = {"type": "json_schema", name, schema}`
  (schema gerado do pydantic `AdSpec` — `app/extractors/schema.py`)
- **Thinking:** `reasoning.effort = "none"` (desligado) — mais rápido/determinístico
- **Custo (1M tokens):** input cache-miss $0.14 / cache-hit $0.0028 / output $0.28
- **Medido em execução real:** ~950 in + ~90 out tokens/anúncio, cache hit ~70%
  (instruções são cacheadas) → **~US$0.00016/anúncio** (~32 ads ≈ US$0.005)

## Implementação (validada em 2026-08-05)

- `app/extractors/schema.py` — pydantic `AdSpec` + `specs_json_schema()`
- `app/extractors/regex.py` — RAM (gb ram/ddr), storage (ssd/hdd/nvme + gb/tb),
  CPU (`i5-8400`, `core i3`), form_factor; retorna `RegexResult.resolved`
- `app/extractors/llm.py` — cliente httpx do `/responses`; valida resposta com
  pydantic; falha → `LlmError` (ad fica pendente)
- `app/extractors/pipeline.py` — regex primeiro; LLM confirma e preenche o resto;
  **regex prevalece** nos campos que resolveu; `extraction_method`
  (`regex+llm` | `llm`)
- CLI: `flask process [--limit N] [--ad <id> --force]` com custo estimado no fim
- Resultado real: **32/32** anúncios com descrição extraídos; 1 falha transitória
  resolvida no reprocessamento

## Auditoria de qualidade (2026-08-05)

Executada sobre as 129 specs após enriquecimento completo. Problemas encontrados
e correções:

| problema | correção |
|----------|----------|
| `brand`/`model` = None em anúncios óbvios ("Computador Dell Optiplex 3040") | extração **determinística** de marca/modelo no `regex.py` (`_BRAND_RE`/`_MODEL_RE`), que prevalece no merge |
| CPs antigas sem família (Pentium, Core 2 Duo, Athlon) | `normalize_cpu` estendido: `core2`, `pentium`, `athlon`, `celeron` |
| Valor lixo da LLM "Intel Duou Core" | capturado por `duo` → família `core2` |
| `form_factor: "all-in-one"` rejeitado pelo enum | enum ampliado (adicionado `all-in-one` ao schema) |

Resultado final: **129/129 com specs**, 0 sem marca, 0 CPU sem família.
Distribuição de famílias: i5=53, i3=25, i7=9, core2=7, pentium=2, athlon=1, sem CPU=32.

## Estratégia: determinístico primeiro, IA depois

```
descrição crua
      |
      v
[regex.py]  -> campos óbvios e baratos (preço é sempre aqui; RAM/CPU quando o
|              padrão for claro)
|              -> produz AdSpec parcial + marca campos "não resolvidos"
v
[llm.py]    -> para os campos que faltaram OU quando o texto é ambíguo
|              -> saída JSON validada por pydantic
v
[pipeline.py] -> merge regex + LLM (LLM não sobrescreve o que regex resolveu
                 com alta confiança) -> AdSpec final
```

Regra geral: **regex nunca é removido por LLM**. Se o regex achou `price`/`ram_gb`
com padrão claro, LLM só complementa o restante (reduz custo e alucinação).

## Schema de saída (`extractors/schema.py`)

```python
class AdSpec(BaseModel):
    brand: str | None            # Dell, Lenovo
    model: str | None            # OptiPlex 7050
    form_factor: str | None      # mini | sff | tower | notebook
    cpu: str | None              # "i5-8400"
    ram_gb: int | None
    storage_gb: int | None
    storage_type: str | None     # ssd | hdd | nvme
    gpu: str | None              # null = integrada
    confidence: float            # 0.0–1.0
```

`year` e `condition` foram removidos (D-013: ano redundante com a CPU; condição
na OLX é sempre usada). `cpu` é complementado por **`cpu_family`**/**`cpu_model`**
calculados de forma determinística por `normalize_cpu()` no pipeline (D-012),
permitindo agrupar `i5-8500` e `i5 8500` juntos e `core i3` na família i3.
A **`cpu_generation`** (D-016) é derivada por `generation_from()` (Intel: geração,
Ryzen: série) e usada no filtro de geração da UI/API. **Fallback (D-027):** se
não houver `cpu_model`, uma **geração explícita no texto** ("10ª geração",
"10th gen", "geração 10") detectada pelo regex vira `cpu_generation` (só com CPU
presente e faixa por família) — anúncios sem número de modelo passam a aparecer
no gráfico/ofertas. `flask process --missing-generation` reprocessa (regex+LLM)
só os ads com specs mas sem `cpu_generation`.

Regras de validação:
- valores fora de faixa (ram_gb <= 0, storage_gb <= 0) -> `null`
- enums (form_factor, storage_type) normalizados
- LLM **deve retornar `null`** quando não souber (nunca inventar)

## Prompt (esboço, a refinar)

> Dado o anúncio a seguir de computador usado, extraia as specs.
> Retorne SOMENTE JSON no schema pedido. Se uma informação não estiver
> presente ou for ambígua, use null. Nunca invente valores.
>
> Título: ...
> Descrição: ...  (truncada em ~1500 chars)
>
> Atenção: modelo pode estar abreviado ("optiplex 7050", "thinkcentre m700").
> Normalize marca/modelo/fator de forma razoável.

## Cache e custo

- cachear resultado da extração por `ad_id` (não re-chamar LLM p/ mesmo ad)
- só enviar para LLM os ads ainda sem specs (`extracted_at is null`)
- texto truncado (configurável, default ~1500 chars)
- D-001: escolha de provedor (local vs API) define custo por chamada

## Confiabilidade

- `confidence` por ad; filtros podem priorizar confiança alta
- ad nunca perde a descrição crua (doc 04)
- se LLM der saída inválida/timeout -> specs ficam `null`, ad fica marcado p/ re-tentar
- **retry automático (D-023, spec 11):** JSON inválido / falha de validação pydantic
  é re-tentado **no mesmo run** até `LLM_MAX_RETRIES` vezes (default `2` → até 3
  chamadas). Cada retry anexa uma **nota corretiva anti-echo ao final do `input`**
  (as `instructions` permanecem constantes → cache de prefixo da DeepSeek
  preservado, retry barato). Tokens são somados entre tentativas e contabilizados
  **inclusive no caminho de falha** (`LlmError` carrega o `usage` acumulado);
  `run_process`/CLI expõem `retries`. Erros HTTP (5xx/429/timeout) continuam
  falhando imediatamente (fora do escopo).
- amostra de ads extraídos + diff manual para auditar qualidade

## Perguntas em aberto

- [x] ~~D-001~~ → **fechada: DeepSeek V4 Flash (Responses API)**
- [x] ~~D-002~~ → **fechada: colunas normalizadas** (tabela `ad_specs`)
- [ ] D-003: reprocessamento automático (fila) vs `flask process` manual
