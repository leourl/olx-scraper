# 15 — Geração explícita de CPU no texto (sem número de modelo)

## Contexto e problema

O `/chart` e `/offers` só mostram anúncios com `cpu_generation` preenchida:
`chart_data` (`ad_service.py:324`) e `best_deals` (`ad_service.py:286`) filtram
`Ad.price_cents IS NOT NULL` **e** `AdSpec.cpu_generation IS NOT NULL`. A busca
(`list_ads`) não exige specs → um anúncio pode aparecer na busca e sumir do
gráfico/ofertas.

`cpu_generation` é derivada **deterministicamente** de `cpu_model`
(`generation_from` = `model // 1000`). Mas muitos anúncios descrevem a CPU **sem
número de modelo** — ex.:

> *"Dell OptiPlex 3080 … processador Intel Core i5 de **10ª geração**"* (ad 888,
> cadastro manual D-026)

Resultado: regex captura `core i5` → `cpu_family=i5`, `cpu_model=None` →
`cpu_generation=None` → o anúncio some do gráfico/ofertas, mesmo tendo preço e a
**geração explícita no texto**.

Medido no banco do RPi: **667** `ad_specs` sem `cpu_generation`; **135** deles
mencionam "geração"/"gen" no texto (beneficiáveis pela detecção de geração
explícita). Os demais ~532 não têm geração nenhuma no texto.

## Objetivos

- Detectar **geração explícita no texto** ("10ª geração", "10th gen", "10 gen",
  "geração 10", "8ª geração"…) e gravar `cpu_generation` mesmo quando não há
  `cpu_model` — fazendo esses anúncios aparecerem no gráfico/ofertas.
- **Não inventar** `cpu_model`: a geração vem do texto, o modelo segue `None`.
- Adicionar **re-processo seletivo** `flask process --missing-generation`
  (regex + LLM) só dos ads com specs mas sem `cpu_generation` (custo ~US$0,11
  nos ~667; o `--force` atual reprocessaria ~900 ads indiscriminadamente).
- Registrar a decisão (D-027) em `docs/specs/00-decisoes.md`.
- 100% offline nos testes.

## Não-objetivos (fora de escopo por ora)

- **Mapa Dell OptiPlex / Lenovo ThinkCentre → geração** (ex.: "OptiPlex 3080" →
  10ª geração). Heurístico e coberto pelo texto explícito na maioria dos casos;
  fica como evolução futura se o texto não disser a geração.
- **Mudar o schema/LLM**: `cpu_generation` continua derivada no pipeline; o
  schema do LLM (AdSpec) não ganha o campo (evita que a LLM chute geração).
- **Re-processo com backfill puramente determinístico** (rodar só o regex sem
  LLM): o usuário optou por **re-processo via LLM** (pode extrair modelo que o
  regex perdeu). O backfill determinístico fica como alternativa citada.
- **Voltar a permitir anúncios sem `cpu_generation`** no gráfico/ofertas.

---

## 1. `app/extractors/regex.py` — detecção de geração explícita

Novo padrão que aceita o número **antes** ou **depois** da palavra, com marcador
ordinal opcional (`ª` `º` `a` `o` `st` `nd` `rd` `th`) e as variações
`geração|geracao|generation|gen`:

```python
_GEN_RE = re.compile(
    r"(?<!\w)(?P<n1>1[0-4]|[1-9])\s*(?:ª|º|a|o|st|nd|rd|th)?\.?\s*(?:geração|geracao|generation|gen)\b"
    r"|\b(?:geração|geracao|generation|gen)\s+(?P<n2>1[0-4]|[1-9])(?!\d)",
    re.IGNORECASE,
)
```

- A faixa **1–14 é garantida pela própria regex** (`1[0-4]|[1-9]`): "15ª"/
  "20ª geração" **não** casam (evita geração implausível que depois cairia na
  faixa por família).
- `(?<!\w)` antes do número evita casar um dígito *dentro* de uma palavra ou de
  um número maior: "115ª geração" não casa "5", e o **dígito da família**
  ("i7 geração" → não casa "7", só "10" em "i7 10ª geração"/"geração 10").
- `(?!\d)` no ramo do número-depois evita "geração 105" casar "10".

`RegexResult` ganha o atributo **`cpu_generation: int | None = None`** e o
`extract_regex` o preenche:

```python
gen_m = _GEN_RE.search(text)
if gen_m:
    result.cpu_generation = int(gen_m.group("n1") or gen_m.group("n2"))
```

**Importante:** `cpu_generation` **não** entra em `resolved`. O `_merge`
(`pipeline.py`) converte cada campo de `resolved` no `AdSpec` pydantic — e
`cpu_generation` **não existe no schema** (Pydantic v2 com `extra='ignore'` o
descartaria silenciosamente). Mantê-lo fora de `resolved` deixa claro que é
**semântica do pipeline**, consumida diretamente (§2), e evita misturar com o
schema da LLM.

`as_specs()` fica inalterado (o AdSpec da LLM não tem `cpu_generation`).

## 2. `app/extractors/pipeline.py` — fallback determinístico

Em `extract_specs`, após `generation_from`, se não houver geração pelo modelo,
usar a geração explícita do regex **apenas se houver CPU no spec** e a família
for compatível:

```python
cpu_family, cpu_model = normalize_cpu(spec.cpu)
cpu_generation = generation_from(cpu_family, cpu_model)
if (
    cpu_generation is None
    and spec.cpu
    and regex.cpu_generation is not None
    and _gen_in_family(cpu_family, regex.cpu_generation)
):
    cpu_generation = regex.cpu_generation
```

Helper de faixa por família (mesma regra do `generation_from`):

```python
def _gen_in_family(family: str | None, gen: int) -> bool:
    if family and family.startswith("ryzen"):
        return 1 <= gen <= 9
    return 1 <= gen <= 14  # Intel (i*) ou família desconhecida
```

- `spec.cpu` exigido: ad sem nenhuma menção de CPU não ganha geração por um
  "10ª geração" genérico no texto.
- O `cpu_model` continua `None` (não inventamos modelo); gráfico/ofertas só
  precisam da geração.

## 3. Re-processo seletivo — `runner.py` + `cli.py`

### `run_process`

Ganha o parâmetro `missing_generation: bool = False`. A ordem de precedência
fica: `ad_id` → `missing_generation` → `force` → pendentes.

```python
elif missing_generation:
    query = (
        Ad.query.join(AdSpec, Ad.id == AdSpec.ad_id)
        .filter(
            AdSpec.cpu_generation.is_(None),
            Ad.description.isnot(None),
            Ad.description != "",
            Ad.is_active.is_(True),
        )
        .order_by(Ad.id.asc())
    )
    if limit:
        query = query.limit(limit)
    ads = query.all()
```

- Import de `AdSpec` em `runner.py` (`from app.models import Ad, AdSpec`).
- Reusa `extract_specs` + `save_specs` (regex **e** LLM) — com o §1/§2, ads com
  geração explícita passam a recebê-la; ads com modelo que a regex perdeu podem
  ser pegos pela LLM.

### CLI

```python
@click.option("--missing-generation", is_flag=True,
              help="Re-processar ads com specs mas sem cpu_generation.")
```

Delegando `run_process(current_app, limit, ad_id, force, missing_generation)`.
**Não** substitui `--force` (que reprocessa todos com descrição).

## 4. Comportamento esperado

| cenário | resultado |
|---|---|
| "Intel Core i5 de 10ª geração" (sem modelo) | `cpu="core i5"`, `cpu_family=i5`, `cpu_model=None`, **`cpu_generation=10`** → aparece no gráfico/ofertas |
| "10th gen", "10 gen", "geração 10" | idem (variações en/pt) |
| "i5-10500 10ª geração" | `cpu_model=10500`, `generation_from`→10 (regex explícito é redundante, não sobrescreve) |
| "20ª geração" / "15ª geração" | não casa (faixa da regex 1–14) → `cpu_generation=None` |
| "10ª geração" sem nenhuma menção de CPU | `spec.cpu` vazio → sem geração (guarda do §2) |
| `flask process --missing-generation` | reprocessa só ads com specs e `cpu_generation IS NULL` (esperado ~667 no RPi, custo ~US$0,11) |
| `flask process --force` | inalterado (todos com descrição) |

## 5. Custo / impacto

- **Regex/pipeline**: zero custo adicional por anúncio; a LLM continua a mesma
  (~US$0,00016/ad).
- **Re-processo**: ~667 ads × ~US$0,00016 ≈ **US$0,11**; ~10–20min (1 req/ad à
  DeepSeek, sem rate limit) — roda no RPi sem atrapalhar o autorun (uma run por
  vez via `RunManager`; o CLI é fora do `RunManager`, igual aos demais).
- **Sem migração de banco**; sem novas dependências.
- **Dado**: `cpu_model` de ads sem modelo permanece `None` (correto).

## 6. Casos de borda

- **Dígito dentro de palavra/número maior** ("115ª geração", "i7 geração"):
  `(?<!\w)` evita casar "5" dentro de "115" e o dígito da família ("7" de "i7").
- **"gen" como prefixo de palavra** ("genérico", "gerador"): `\b` após `gen` não
  casa com palavra que continua (é/ü são word chars).
- **Ryzen**: "Ryzen 5 de 4ª geração" → `_gen_in_family("ryzen5", 4)` ok;
  "10ª geração" num Ryzen → faixa ryzen 1–9 → descarta.
- **Ad sem CPU no texto**: geração explícita não é aplicada (guarda `spec.cpu`).
- **Ad duplicado re-cadastrado (D-026)**: re-processo com `--missing-generation`
  inclui os cadastrados manualmente (idem os do scrape).
- **`limit`**: aplicado ao query do `missing_generation` (pode rodar em lotes).

## 7. Testes (offline, sem rede)

`tests/test_regex.py`:
- `test_explicit_generation_pt`: "Intel Core i5 de 10ª geração" →
  `cpu_generation == 10`, `cpu == "core i5"`.
- `test_explicit_generation_variants`: "10th gen", "10 gen", "geração 10" → 10.
- `test_explicit_generation_after`: "Processador de 8ª geração" → 8.
- `test_explicit_generation_out_of_range`: "i5 de 20ª geração" e "15ª geração"
  → `None`.
- `test_explicit_generation_partial_digit`: "115ª geração" → `None`.
- `test_explicit_generation_family_digit`: "i7 geração 10" → 10 (não 7);
  "i7 de 10ª geração" → 10.

`tests/test_pipeline.py`:
- `test_extract_specs_explicit_generation`: LLM mock devolve `cpu="core i5"`,
  descrição "Intel Core i5 de 10ª geração" → `cpu_generation == 10`,
  `cpu_model is None`.
- `test_extract_specs_gen_requires_cpu`: descrição "10ª geração" sem CPU
  (LLM devolve `cpu=None`) → `cpu_generation is None`.
- `test_extract_specs_model_still_derives_gen`: "i5-10500" → `cpu_generation ==
  10` via modelo (caminho atual intacto).

`tests/test_runner.py`:
- `test_run_process_missing_generation_only`: ad A com specs e
  `cpu_generation=8`; ad B com specs e `cpu_generation=None` (LLM mock) →
  `run_process(missing_generation=True)` processa **só B** (`ok == 1`).
- `test_run_process_missing_generation_limit`: com `limit=1` e dois pendentes →
  `ok == 1`.

Rodar a suíte completa: `uv run pytest` (100% offline).

## 8. Documentação

- **D-027** em `docs/specs/00-decisoes.md` (fechada): geração explícita no
  texto como fallback determinístico de `cpu_generation` quando não há
  `cpu_model`; faixa por família (Intel 1–14, Ryzen 1–9); campo **fora** do
  `resolved` do regex (não vaza para o `AdSpec` pydantic); re-processo seletivo
  `flask process --missing-generation` (só ads com specs sem geração, regex+LLM).
  Alternativas descartadas: mapa OptiPlex/ThinkCentre→geração (heurístico,
  adiado), backfill só-regex (usuário optou por LLM).
- `docs/specs/06-extracao-llm.md` — nota sobre `cpu_generation` derivada e o
  fallback de geração explícita.
- `AGENTS.md` — seção Extração LLM: fallback de geração explícita + flag
  `--missing-generation`.

## 9. Checklist de entrega

1. `regex.py`: `_GEN_RE` + `RegexResult.cpu_generation` (fora de `resolved`).
2. `pipeline.py`: fallback `_gen_in_family` em `extract_specs`.
3. `runner.py`: `run_process(missing_generation=)` + import `AdSpec`.
4. `cli.py`: `flask process --missing-generation`.
5. Testes novos (`test_regex`, `test_pipeline`, `test_runner`) + `uv run pytest`
   verde.
6. Deploy RPi: `cd ~/olx_monitor && git pull` → `uv run flask process
   --missing-generation` → confirmar ad 888 no gráfico/ofertas.
7. Docs: D-027, 06, AGENTS.md.

## Perguntas em aberto

- [ ] **Mapa por modelo de máquina** (OptiPlex/ThinkCentre → geração) para ads
  sem geração no texto? (~532 ads restantes; heurístico, precisa validação.)
- [ ] Exibir **"geração estimada"** (badge) quando vier só do texto, distinguindo
  de `model` real? (Hoje `cpu_generation` não guarda origem.)
- [ ] Barra de progresso/parcialidade do `--missing-generation` (hoje CLI só
  loga stats no fim)?
