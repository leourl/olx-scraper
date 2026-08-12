# 17 — Filtro de CPU por família (Intel × Ryzen)

## Contexto e problema

O projeto trata `cpu_generation` como um número **globalmente comparável**, mas
ele é derivado de escalas **incompatíveis entre vendedores** (`app/extractors/regex.py`
`generation_from`):

- **Intel**: `modelo // 1000` → faixa **1–14** (i5-8500 → 8, i5-13500 → 13);
- **AMD Ryzen**: série comercial → faixa **1–9** (ryzen5 5600G → 5, ryzen5 8500G → 8).

Consequências dos filtros atuais (`AdFilters.gen_min/gen_max` em
`app/services/ad_service.py:_apply_filters`, l.202–205), que aplicam o mesmo
eixo sobre **todas** as famílias:

- **"Geração mín. 8"** mistura Intel 8ª (2017, Coffee Lake) com Ryzen 8000G
  (2024, Zen 4) no mesmo balde — eras distintas, preços de mercado distintos.
- **"Geração mín. 10"** exclui **todo** Ryzen silenciosamente (nenhum passa de 9).
- **"Geração 2"** significa 2011 (Intel Sandy Bridge) ou 2018 (Ryzen Zen+).
- O número do Ryzen nem é "geração" de verdade (a linha 4000 nunca existiu no
  desktop; 8000G é Zen 4, mesma era do 7000) — é série comercial.

O mesmo eixo global contaminado aparece em:

- `/chart` (`chart_data`, Y = `cpu_generation` misturando eras no scatter);
- `/offers` (`price_benchmarks` e `best_deals` via `_price_by_generation` — a
  "mediana da 8ª geração" mistura i5-8500 com ryzen5-8500G).

Dados reais de `instance/olx.db` (família → n → gerações):

```
i5 249 gens= 2,3,4,6,7,8,9,10,12,13,14
i3 137 gens= 2,3,4,6,7,8,9,10,12,14
i7  47 gens= 2,3,4,6,7,8,12
core2 28 gens= —      pentium 16 gens= —   celeron 6 gens= —
ryzen5 4 gens= 8      ryzen3 2 gens= 8     ryzen7 1 gens= 2   athlon 1 gens= —
```

## Objetivos

- **Escopar o filtro de geração por família**: `gen_min`/`gen_max` só se aplicam
  quando uma `cpu_family` está selecionada; sem família, são ignorados (fim da
  ambiguidade cross-vendor).
- **UI coerente**: select de família agrupado em **Intel/AMD** (`<optgroup>`);
  seletor de geração habilitado **somente** para famílias com geração suportada,
  com faixa dinâmica (Intel 1–14, Ryzen 1–9) e dica quando não aplicável.
- **`/chart` escopado por família**: sem família → aviso em vez do scatter
  (Y = geração só é honesto dentro de uma família).
- **`/offers` com benchmark por (família, geração)**: mediana/p25/p75 e o
  ranking de desconto passam a comparar dentro da mesma família+geração.
- Registrar a decisão (**D-029**) em `docs/specs/00-decisoes.md`.
- 100% offline nos testes.

## Não-objetivos (fora de escopo)

- **Corrigir a extração da geração do Ryzen** (8000G → 8ª "geração" é Zen 4).
  A regra `modelo // 1000` fica como está; o filtro passa a ser por família e a
  comparação se torna consistente. Registrado como pendência (ver §10).
- **Filtro por performance/era cross-vendor** (ex.: preset "moderno (Intel ≥8 /
  Ryzen ≥3)" ou ano de lançamento) — vira spec própria; aqui mantém-se geração.
- **Filtro por `cpu_model` na UI** (existe só na API hoje) — não é tocado.
- **Novo campo/coluna de banco** — nenhuma migração é necessária.

---

## 1. Constantes compartilhadas — `app/services/ad_service.py`

Próximo a `ALLOWED_FORM_FACTORS` (l.13):

```python
CPU_GROUPS: dict[str, list[str]] = {
    "Intel": ["i3", "i5", "i7", "i9", "core2", "pentium", "celeron"],
    "AMD": ["ryzen3", "ryzen5", "ryzen7", "ryzen9", "athlon"],
}

# Faixa plausível de geração por família (mesma regra do generation_from).
# Famílias sem geração (core2/pentium/celeron/athlon) → None.
GEN_RANGE: dict[str, tuple[int, int] | None] = {
    "i3": (1, 14), "i5": (1, 14), "i7": (1, 14), "i9": (1, 14),
    "ryzen3": (1, 9), "ryzen5": (1, 9), "ryzen7": (1, 9), "ryzen9": (1, 9),
    "core2": None, "pentium": None, "celeron": None, "athlon": None,
}


def gen_range_for(family: str | None) -> tuple[int, int] | None:
    """Faixa de geração aceita pela família (None = família sem geração).

    Normaliza `family.lower()`: as chaves de `GEN_RANGE` são minúsculas e a
    UI/API pode enviar "I5"/"Ryzen5" — sem o lower haveria falha silenciosa
    (retorna None → select desabilitado/gerador vazio indevidamente).
    """
    if not family:
        return None
    return GEN_RANGE.get(family.lower())


def cpu_group(family: str) -> str:
    """'Intel' ou 'AMD' para agrupar no select da UI (case-insensitive)."""
    return next((g for g, fams in CPU_GROUPS.items() if family.lower() in fams), "Intel")
```

- O grupo é a base para o `<optgroup>` do `_filters.html` e para rotular o
  gráfico/ofertas.

### Registro no Jinja2 (templates)

`_filters.html` chama `gen_range_for(...)` diretamente — a função precisa estar
exposta ao ambiente de templates. Registrar no blueprint da UI
(`app/blueprints/main/routes.py`, junto dos filtros/template filters):

```python
@bp.app_template_global()
def gen_range_for(family):
    """Expõe ad_service.gen_range_for aos templates (partial _filters.html)."""
    return ad_service.gen_range_for(family)
```

Cobre `/`, `/chart` e `/offers` (todos no `main` blueprint, todos reusam o
partial). Como o `cpu_group` é usado só para montar `cpu_family_groups` na rota
(não no template), ele **não** precisa de registro no Jinja2.

## 2. Semântica do filtro — `_apply_filters` (l.198–205)

Hoje:

```python
if f.cpu_family:
    query = _spec_join(query, joined).filter(AdSpec.cpu_family == f.cpu_family)
if f.cpu_model is not None:
    query = _spec_join(query, joined).filter(AdSpec.cpu_model == f.cpu_model)
if f.gen_min is not None:
    query = _spec_join(query, joined).filter(AdSpec.cpu_generation >= f.gen_min)
if f.gen_max is not None:
    query = _spec_join(query, joined).filter(AdSpec.cpu_generation <= f.gen_max)
```

Passa a ser (geraçao **dentro** do bloco de família):

```python
if f.cpu_family:
    query = _spec_join(query, joined).filter(AdSpec.cpu_family == f.cpu_family)
    if f.gen_min is not None:
        query = query.filter(AdSpec.cpu_generation >= f.gen_min)
    if f.gen_max is not None:
        query = query.filter(AdSpec.cpu_generation <= f.gen_max)
```

Regras resultantes:

- **`cpu_family` + `gen_min/gen_max`** → intervalo aplicado à geração daquela
  família (eixo local, semântica correta).
- **Sem `cpu_family`** → geração é **ignorada** (não restringe nada). O antigo
  comportamento global é removido de propósito.
- Faixa fora da válida da família retorna vazio naturalmente (`>=`/`<=`); o
  clamp é só na UI (opções do select), não no SQL.
- Famílias sem geração (`core2` etc.) + `gen_min` → retornam vazio (são `None`),
  comportamento correto.
- **Fabricante (D-030):** `cpu_family=intel|amd` filtra pelo grupo de famílias
  do fabricante e funciona como domínio de escala da geração (intel 1–14,
  amd 1–9) — ver `docs/specs/18-filtro-por-fabricante.md`.

**Parsers (API e UI) não mudam**: `blueprints/api/routes.py:42-43` e
`blueprints/main/routes.py:56` já repassam `gen_min`/`gen_max`. A mudança é só no
service. O filtro `cpu_model` (API) permanece independente (exato), como hoje.

## 3. UI — `app/blueprints/main/templates/_filters.html`

### Select de família agrupado

Substituir o loop plano (l.16–23) por `optgroup` + valor vazio para "qualquer":

```html
<label for="cpu_family">Família CPU</label>
<select id="cpu_family" name="cpu_family">
    <option value="">qualquer</option>
    {% for grupo, familias in cpu_family_groups.items() %}
    <optgroup label="{{ grupo }}">
        {% for fam in familias %}
        <option value="{{ fam }}" {% if filters.cpu_family == fam %}selected{% endif %}>{{ fam }}</option>
        {% endfor %}
    </optgroup>
    {% endfor %}
</select>
```

O template passa a receber `cpu_family_groups` (montado na rota a partir de
`ad_service.CPU_GROUPS`, filtrando só famílias presentes em `por_cpu_family` —
senão o select mostra famílias sem anúncio). Fallback: se a família selecionada
não estiver na lista de stats, incluí-la (para o estado `selected` continuar
coerente).

### Seletor de geração dependente da família

Renderizado server-side com a faixa da família selecionada:

```html
{% set gen_range = gen_range_for(filters.cpu_family) %}
<label for="gen_min">Geração mín. {% if filters.cpu_family %}({{ filters.cpu_family }}){% endif %}</label>
<select id="gen_min" name="gen_min"
        {% if not gen_range %}disabled title="selecione uma família com geração (i3/i5/i7/i9/ryzen3/5/7/9)"{% endif %}>
    <option value="">qualquer</option>
    {% for gen in range(gen_range[0], gen_range[1] + 1) if gen_range %}
    <option value="{{ gen }}" {% if filters.gen_min == gen %}selected{% endif %}>{{ gen }}ª</option>
    {% endfor %}
</select>
```

- Sem família → `disabled` + título explicativo ("selecione uma família").
- Família sem geração (`core2`/`pentium`/`celeron`/`athlon`) → `disabled`.
- Com `i5` selecionado → opções 1–14; com `ryzen5` → 1–9.

### Auto-submit ao trocar a família (`app/static/js/app.js`)

Como o form é GET e a faixa de geração é server-side, trocar a família precisa
re-renderizar. Adicionar em `app.js` (junto dos `init*`, dentro do
`DOMContentLoaded`):

```js
function initCpuFamilyFilter() {
    const sel = document.getElementById("cpu_family");
    const form = document.getElementById("filters-form");
    if (!sel || !form) return;
    sel.addEventListener("change", () => {
        // limpa geração para não mandar valor fora da faixa da nova família
        document.getElementById("gen_min").value = "";
        form.submit();
    });
}
```

- Sempre `submit()` normal do form (navegação GET com os outros filtros).
- Página `/chart`: como passa a exigir família, o auto-submit também serve para
  "destravar" o gráfico ao escolher a primeira família.

## 4. `/chart` — gráfico preço × geração escopado por família

### `app/services/ad_service.py`

`chart_data` já recebe `AdFilters` e aplica `_apply_filters`, então com a
semântica da §2 um chart com `cpu_family` já filtra a família. **Sem** família,
os pontos voltariam a misturar escalas — por isso a rota/template exigem família.

### `blueprints/main/routes.py` (`/chart`)

Passar para o template:

- `cpu_family_groups` (como na §3);
- `gen_range_for(filters.cpu_family)` (para o eixo Y min/max).

### `chart.html`

- `{% if not filters.cpu_family %}` → bloco de aviso no lugar do canvas:
  "Selecione uma família de CPU para o gráfico preço × geração (as gerações de
  Intel e Ryzen usam escalas diferentes)."
- Com família → canvas normal, mas:
  - legenda "Família: i5 · geração 1–14";
  - eixo Y com `min`/`max` da família (`gen_range_for`), para o scatter não
    abrir espaço morto (ex.: famílias sem geração na faixa);
  - cor dos pontos continua por marca (Dell/Lenovo/Outras) — agora dentro de
    uma família só, o eixo Y é comparável.

O `filters` já é passado ao template (`main/routes.py:104`).

## 5. `/offers` — benchmark e ranking por (família, geração)

### `app/services/ad_service.py`

`_price_by_generation` (l.265) agrupa só por geração → vira `_price_by_family_gen`,
chave `(cpu_family, cpu_generation)`:

```python
def _price_by_family_gen() -> dict[tuple[str, int], list[int]]:
    rows = (
        db.session.query(AdSpec.cpu_family, AdSpec.cpu_generation, Ad.price_cents)
        .join(Ad, AdSpec.ad_id == Ad.id)
        .filter(
            AdSpec.cpu_family.isnot(None),
            AdSpec.cpu_generation.isnot(None),
            Ad.price_cents.isnot(None),
            Ad.is_active.is_(True),
            Ad.user_disabled.is_(False),
        )
        .all()
    )
    groups: dict[tuple[str, int], list[int]] = {}
    for fam, gen, price in rows:
        groups.setdefault((fam, gen), []).append(price)
    return groups
```

### `price_benchmarks()`

Linha do benchmark passa a ter `family` + `generation` (ordem por família, depois
geração):

```python
[
  {"family": "i5", "generation": 6, "count": …, "p25": …, "p50": …, "p75": …},
  …
]
```

(Compatibilidade: manter a chave `generation`; adicionar `family`. Consumidores
existentes — `offers.html` — são atualizados nesta spec.)

### `best_deals()`

- `medians = {(fam, gen): median(prices)}` a partir de `_price_by_family_gen`.
- Cada deal usa a mediana da **própria família+geração** do ad
  (`(ad.spec.cpu_family, ad.spec.cpu_generation)`).
- O dict do deal ganha `"family": ad.spec.cpu_family`.

### `offers.html`

- Coluna "Geração" do benchmark e do ranking vira "Família · Geração"
  (ex.: `i5 · 8ª`); tooltip/subtexto do ranking: "desconto vs mediana da mesma
  família e geração".
- Descrição no topo: "comparado com a mediana da **mesma família e geração**".

## 6. Comportamento esperado

| cenário | resultado |
|---|---|
| `cpu_family=i5&gen_min=8` | só Intel i5 da 8ª geração em diante |
| `cpu_family=ryzen5&gen_min=8` | só Ryzen 5 série 8000 (2024) — não mistura com Intel 8ª |
| `gen_min=8` sem família | **ignorado** — lista todas as famílias (nenhum filtro de geração) |
| `cpu_family=core2&gen_min=6` | vazio (core2 não tem geração) |
| `/chart` sem família | aviso "selecione uma família" (sem scatter) |
| `/chart?cpu_family=i5` | scatter só de i5, eixo Y 1–14 |
| `/offers` | benchmark agrupado por família·geração; desconto vs mediana da mesma família+geração |
| API `GET /api/ads?gen_min=8` sem família | sem restrição de geração (comportamento novo) |

## 7. Custo / impacto

- **Sem custo monetário**; sem novas dependências; **sem migração de banco**.
- **Breaking change pequeno e intencional**: `gen_min`/`gen_max` sem `cpu_family`
  deixam de restringir (antes restringiam de forma incorreta/ambígua). A API é
  permissiva (ignora), não retorna erro.
- Nenhum request extra; apenas SQL local + re-render do form no auto-submit.

## 8. Casos de borda

- **Família selecionada que não existe nos stats**: a rota inclui a família
  selecionada no `cpu_family_groups` (senão o `<option selected>` fica órfão) e
  o filtro retorna vazio (nenhum ad daquela família).
- **`gen_min` > `gen_max`**: vazio (comportamento atual, natural de `>=`/`<=`).
- **Família sem geração + `gen_min` via API**: retorna vazio, não erro.
- **`/chart` com família mas sem dados**: mantém o canvas vazio atual + o texto
  "0 anúncio(s) com preço e geração plotados" já existente (chart.html l.7).
- **Ryzen 8500G (`ryzen5`, gen 8)**: entra no balde `ryzen5·8ª` — correto dentro
  da escala Ryzen; continua como qualidade de extração aberta (§10), não do filtro.
- **Auto-submit limpa `gen_min`**: trocar de família com geração selecionada
  descarta o valor antigo (evita mandar geração da família anterior). Se o
  usuário quiser, reescolhe após o reload.

## 9. Testes (offline, sem rede)

### `tests/seed.py`

Adicionar **1 anúncio Ryzen** ao seed (cobre cross-vendor nos testes de
chart/ofertas/filtro):

- `RawAd` 5: "Dell OptiPlex 5050 SFF ryzen5 5600G", preço `90000`, publicado;
- `AdSpecSchema` correspondente (`cpu="ryzen5 5600G"`, ram 8, ssd 256).

Ajustar asserções afetadas pelo novo total (5 ads):

- `test_api.py::test_list_ads_all` (l.4-10, total 4 → 5),
  `test_list_ads_filters` (l.13-49: preços/ids conforme o ad 5),
  `test_stats` (l.115-123: `total`, `com_specs`, `por_cpu_family` ganha
  `"ryzen5": 1`), `test_list_ads_pagination` (l.67-71: total 5, pág. 2 continua
  com 2 itens).

### `tests/test_api.py`

- `test_list_ads_filters` (l.29-36): `gen_min=8` **sem família** deixa de
  restringir — o trecho passa a pedir `cpu_family=i5` (ex.:
  `gen_min=8&cpu_family=i5` → só i5 ≥ 8ª; `gen_max=6&cpu_family=i5` → gens
  ≤ 6). Adicionar a checagem do caso sem família: `gen_min=8` sozinho retorna
  todas as famílias com specs.
- `test_cpu_generation_requires_family`: `gen_min=8` sem família ignora;
  `gen_min=5&cpu_family=ryzen5` pega o ad Ryzen (gen 5); `gen_min=6&cpu_family=ryzen5`
  retorna vazio (nenhum Ryzen 5 ≥ 6ª no seed).

### `tests/test_ad_service.py`

- `test_gen_filter_scoped_to_family`: com `AdFilters(cpu_family="i5", gen_min=8)`
  só i5 ≥ 8ª; com `AdFilters(gen_min=8)` sem família → total não filtrado por
  geração (retorna todas as famílias com specs).
- `test_benchmark_grouped_by_family_gen`: `price_benchmarks` tem linhas com
  `family` + `generation` (ex.: `i5·8` e `ryzen5·5`) e não mistura os preços.
- `test_best_deals_uses_family_gen_median`: deal expõe `family`; o desconto é vs
  mediana da mesma (família, geração).

### `tests/test_offers.py`

- `test_best_deals_filters` (l.36-40): `AdFilters(gen_min=8)` → muda para
  `AdFilters(cpu_family="i5", gen_min=8)` (sem família o filtro não restringe
  mais).
- `test_price_benchmarks` (l.6-15): com o seed novo, as linhas ganham `family`;
  o `next(b for b in benchmarks if b["generation"] == 8)` precisa qualificar a
  família (`b["family"] == "i5" and b["generation"] == 8`), senão há ambiguidade
  se o Ryzen do seed cair na mesma geração.
- `test_offers_page` (l.52-57): se for verificar a coluna, usar o novo header
  "Família · Geração".

### `tests/test_chart.py`

- `test_chart_data_filters` (l.20-27): `AdFilters(gen_min=8)` → `AdFilters(cpu_family="i5", gen_min=8)`.
- `test_chart_page` (l.30-37): `/chart` sem família passa a renderizar o **aviso**
  (sem `chart.js`/`"generation": 8` no HTML) → o teste precisa pedir
  `/chart?cpu_family=i5` (ou asserir o aviso).
- `test_chart_page_respects_own_filters` (l.40-46): `/chart?gen_min=8` →
  `/chart?cpu_family=i5&gen_min=8`.

### `tests/test_ui.py`

- `test_index_gen_filter` (l.46-52): `/?gen_min=8` → `/?cpu_family=i5&gen_min=8`
  (senão volta a mostrar ThinkCentre/qualquer família).
- `test_filters_family_optgroup`: `_filters.html` contém `<optgroup label="Intel">`
  e `<optgroup label="AMD">`; `#gen_min` tem `disabled` sem família.
- `test_chart_requires_family` (novo, pode morar em `test_chart.py`): `/chart`
  sem `cpu_family` mostra o aviso e não o canvas; `/chart?cpu_family=i5`
  renderiza o canvas.
- `test_offers_benchmark_column`: `offers.html` mostra "Família · Geração"
  (ex.: `i5 · 8ª`).

Rodar a suíte completa: `uv run pytest` (100% offline).

## 10. Documentação

- **D-029** em `docs/specs/00-decisoes.md` (fechada): geração vira filtro
  **dependente de família** (Intel 1–14, Ryzen 1–9; `gen_min`/`gen_max` ignorados
  sem `cpu_family`), select agrupado Intel/AMD, `/chart` escopado por família,
  `/offers` com benchmark por (família, geração). Alternativas descartadas:
  eixo global único (mistura eras — é o bug), preset de era cross-vendor
  (spec própria), filtro por `cpu_model` na UI (pouco aderente).
- **D-016** (l.98): atualizar o parágrafo de "Filtros `gen_min`/`gen_max`" para
  "filtros `gen_min`/`gen_max` **por família**".
- **D-018** (l.119) e **D-019** (l.107): nota de que chart/ofertas passam a ser
  família-escopados.
- `AGENTS.md` — parágrafo curto: filtro de CPU por família; geração ignorada sem
  família; benchmark de ofertas por família+geração.
- **API pública (`07-api-e-ui.md`)**: documentar o breaking change de
  `GET /api/ads?gen_min=X`/`gen_max=X` — a partir daqui exigem `cpu_family`
  para ter efeito; sem família, são **ignorados** (resposta permissiva, sem
  400, para não quebrar clientes existentes).

## 11. Checklist de entrega

1. `ad_service.py`: `CPU_GROUPS`, `GEN_RANGE`, `gen_range_for`, `cpu_group`
   (com `family.lower()`);
   `_apply_filters` com geração dentro do `if cpu_family`; `_price_by_family_gen`,
   `price_benchmarks` (+`family`), `best_deals` (+`family`).
2. `main/routes.py`: registra `gen_range_for` como `@bp.app_template_global()`;
   monta `cpu_family_groups` (Intel/AMD) + `gen_range_for(filters.cpu_family)`
   nas rotas `/`, `/chart`, `/offers`.
3. `_filters.html`: optgroup Intel/AMD + `#gen_min` dependente/disabled.
4. `app.js`: `initCpuFamilyFilter()` (auto-submit + limpa gen).
5. `/chart`: `chart.html` com aviso sem família e eixo Y por família.
6. `/offers`: `offers.html` coluna "Família · Geração" e textos atualizados.
7. Testes: seed + api + ad_service + offers + chart + ui; `uv run pytest` verde.
8. Docs: D-029, notas em D-016/D-018/D-019, `07-api-e-ui.md` (breaking change),
   AGENTS.md.

## Perguntas em aberto

- [ ] **Preset de era cross-vendor** ("moderno: Intel ≥8 / Ryzen ≥3", ou filtro
  por ano de lançamento) para comparar Intel e Ryzen sem escolher família? Vira
  spec própria.
- [ ] **Qualidade da geração do Ryzen 8000G** (8500G → "8ª" mas é Zen 4, mesma
  era do 7000): corrigir na extração (mapa série→era) em spec separada?
- [ ] **`gen_min` sem família**: ignorar (escolhido) ou retornar erro 400 na API
  para forçar a família? Silencioso mantém compatibilidade; documentamos o
  comportamento no README/API.
