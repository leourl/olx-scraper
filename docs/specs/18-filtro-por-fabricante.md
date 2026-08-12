# 18 — Filtro por fabricante de CPU (Intel / AMD)

## Contexto e problema

O filtro de CPU implementado em D-029 exige escolher uma **família** específica
(`i3`, `i5`, `ryzen5`…). Não há como o usuário ver **todos** os anúncios Intel
**ou** todos os AMD de uma vez — ele teria que combinar cada família na mão ou
deixar "qualquer" (que mistura os dois fabricantes).

O usuário quer: *"filtrar apenas Intel ou apenas AMD"*.

O modelo atual já suporta isso conceitualmente: `CPU_GROUPS` em
`app/services/ad_service.py` agrupa as famílias por fabricante
(`Intel` = i3/i5/i7/i9/core2/pentium/celeron, `AMD` = ryzen3/5/7/9/athlon).
Falta expor isso no filtro.

Detalhe importante (que viabiliza a geração): a geração é comparável **dentro
do fabricante**, porque as famílias de um mesmo fabricante compartilham a mesma
escala — Intel `modelo//1000` → 1–14 (i3/i5/i7/i9) e Ryzen série → 1–9
(ryzen3/5/7/9). Então `intel`/`amd` podem funcionar como **domínio de escala**
da geração, igual a uma família, habilitando o filtro `gen_min`/`gen_max`.

## Objetivos

- Adicionar os valores especiais **`intel`** e **`amd`** ao filtro `cpu_family`
  (API + UI), que filtram por **todas** as famílias do fabricante.
- O filtro de geração passa a valer **por fabricante ou família** (domínio de
  escala): `intel` → 1–14, `amd` → 1–9; sem nenhum dos dois → continua ignorado.
- UI mínima: duas opções `Intel (qualquer)` / `AMD (qualquer)` no topo do select
  de família existente (D-029), acima dos optgroups.
- `/chart` passa a aceitar `cpu_family=intel|amd` (eixo Y na faixa do fabricante).
- Registrar a decisão (**D-030**) em `docs/specs/00-decisoes.md` e atualizar a
  spec 17/D-029, `07-api-e-ui.md`, wiki e AGENTS.md.
- 100% offline nos testes.

## Não-objetivos (fora de escopo)

- **Select separado "Fabricante"** na UI — descartado por opção do usuário;
  o select único com `intel`/`amd` no topo é mais simples (zero controles novos).
- **Novo campo/parâmetro de API** (`cpu_vendor=`) — `intel`/`amd` são valores do
  `cpu_family` existente; não há migração de banco nem mudança de schema.
- **Filtro por ano/era cross-vendor** ("moderno") — permanece como pendência da
  spec 17 (§ Perguntas em aberto).

---

## 1. `app/services/ad_service.py` — domínio de escala

### Constantes novas (junto de `CPU_GROUPS`/`GEN_RANGE`, l.16–26)

```python
# Valores especiais de cpu_family que significam "todas as famílias do fabricante".
VENDOR_ALIASES: dict[str, str] = {"intel": "Intel", "amd": "AMD"}

# Faixa de geração por fabricante (todas as famílias do grupo usam a mesma escala).
VENDOR_RANGE: dict[str, tuple[int, int]] = {"intel": (1, 14), "amd": (1, 9)}
```

> Não usar `family.title()` para mapear `intel`→`Intel`/`amd`→`AMD`: `title()`
> geraria `"Amd"` e não bateria com a chave `"AMD"` de `CPU_GROUPS`. O alias
> explícito evita esse bug.

### `gen_range_for(family)` (l.29)

Passa a checar o fabricante antes da família:

```python
def gen_range_for(family: str | None) -> tuple[int, int] | None:
    if not family:
        return None
    key = family.lower()
    if key in VENDOR_RANGE:
        return VENDOR_RANGE[key]
    return GEN_RANGE.get(key)
```

Sem mudança no registro do Jinja2 (`main/routes.py` já expõe via
`@bp.app_template_global()`).

### `cpu_group(family)` (l.40)

Mapear o alias para o grupo do `<optgroup>` (hoje cairia no fallback "Intel"):

```python
def cpu_group(family: str) -> str:
    key = family.lower()
    if key in VENDOR_ALIASES:
        return VENDOR_ALIASES[key]
    return next((g for g, fams in CPU_GROUPS.items() if key in fams), "Intel")
```

### `_apply_filters` (l.227–236)

Dentro do `if f.cpu_family:` — se for alias de fabricante, filtra pelo grupo:

```python
if f.cpu_family:
    fam = f.cpu_family.lower()
    if fam in VENDOR_ALIASES:
        query = _spec_join(query, joined).filter(
            AdSpec.cpu_family.in_(CPU_GROUPS[VENDOR_ALIASES[fam]])
        )
    else:
        query = _spec_join(query, joined).filter(AdSpec.cpu_family == fam)
    # gen_min/gen_max continuam dentro do bloco: o domínio de escala agora
    # pode ser o fabricante (intel 1–14, amd 1–9) ou a família.
    if f.gen_min is not None:
        query = query.filter(AdSpec.cpu_generation >= f.gen_min)
    if f.gen_max is not None:
        query = query.filter(AdSpec.cpu_generation <= f.gen_max)
```

Regras resultantes:

- `cpu_family=intel` → `cpu_family IN (i3, i5, i7, i9, core2, pentium, celeron)`.
- `cpu_family=amd` → `cpu_family IN (ryzen3, ryzen5, ryzen7, ryzen9, athlon)`.
- `cpu_family=intel&gen_min=8` → Intel com geração ≥ 8 (core2/pentium/celeron
  ficam de fora por terem `cpu_generation = NULL`, como esperado).
- `cpu_family=ryzen5` → comportamento de D-029 (exato, inalterado).

## 2. UI — `app/blueprints/main/templates/_filters.html`

### Select de família com fabricantes no topo (l.17–26)

```html
<select id="cpu_family" name="cpu_family">
    <option value="">qualquer</option>
    <option value="intel" {% if filters.cpu_family == 'intel' %}selected{% endif %}>Intel (qualquer)</option>
    <option value="amd" {% if filters.cpu_family == 'amd' %}selected{% endif %}>AMD (qualquer)</option>
    {% for grupo, familias in cpu_family_groups.items() %}
    <optgroup label="{{ grupo }}">
        {% for fam in familias %}
        <option value="{{ fam }}" {% if filters.cpu_family == fam %}selected{% endif %}>{{ fam }}</option>
        {% endfor %}
    </optgroup>
    {% endfor %}
</select>
```

- Os aliases são **hardcoded** (sempre disponíveis, mesmo sem famílias na base).
- O auto-submit de D-029 (`initCpuFamilyFilter` em `app.js`) já cobre — trocar
  para `intel`/`amd` re-renderiza a faixa da geração sem JS novo.

### Seletor de geração (l.29–39)

Sem mudança de template: o `{% set gen_range = gen_range_for(filters.cpu_family) %}`
já resolve `(1, 14)` para `intel` e `(1, 9)` para `amd`. O label mostra
`Geração mín. (intel)`/`(amd)` via `{{ filters.cpu_family }}`. O `disabled`
(quando `gen_range` é `None`) continua valendo para "qualquer" e famílias sem
geração.

## 3. `app/blueprints/main/routes.py` — `_cpu_family_groups`

Não deixar o alias cair no fallback que insere a família selecionada no grupo
(senão `intel` viraria uma `<option>` duplicada dentro do optgroup "Intel"):

```python
if selected and selected.lower() not in VENDOR_ALIASES \
        and not any(selected in fams for fams in groups.values()):
    groups.setdefault(ad_service.cpu_group(selected), []).append(selected)
```

## 4. `/chart` — aceita fabricante

- `chart_data` já aplica `_apply_filters`, então `cpu_family=intel` filtra pelo
  `IN(group)` automaticamente.
- O eixo Y já usa `gen_range_for(filters.cpu_family)` (1–14 / 1–9).
- Ajustes de texto em `chart.html`:
  - subtítulo (l.7): "Selecione uma família ou fabricante de CPU no filtro";
  - aviso do `.notice` (l.13): "Escolha uma família de CPU **ou fabricante**
    (Intel/AMD) acima".

## 5. Parsers — API e UI

`blueprints/api/routes.py` e `blueprints/main/routes.py`: **nenhuma mudança**.
`cpu_family` já é pass-through (`args.get("cpu_family")`); `_apply_filters`
normaliza com `.lower()` e trata o alias.

## 6. Comportamento esperado

| cenário | resultado |
|---|---|
| `cpu_family=intel` | só anúncios com família Intel (i3/i5/i7/i9/core2/pentium/celeron) |
| `cpu_family=amd` | só anúncios com família AMD (ryzen3/5/7/9/athlon) |
| `cpu_family=intel&gen_min=8` | Intel com geração ≥ 8 (sem Ryzen; core2 etc. excluídos por `NULL`) |
| `cpu_family=amd&gen_min=5` | Ryzen série ≥ 5 (sem Intel) |
| `cpu_family=intel&gen_max=4` | Intel geração ≤ 4 (ex.: i5-4200, i3-4130) |
| `/chart?cpu_family=intel` | scatter só de Intel, eixo Y 1–14 |
| `cpu_family=i5` | inalterado (D-029, família exata) |
| `cpu_family=core2&gen_min=6` | vazio (core2 sem geração) |

## 7. Custo / impacto

- **Sem custo monetário**; sem novas dependências; **sem migração de banco**.
- **Sem breaking change**: valores novos são aditivos; `cpu_family` com família
  exata continua idêntico.
- Nenhum request extra (SQL local + re-render do auto-submit já existente).

## 8. Casos de borda

- **`cpu_family=INTEL` / `AMD` (caixa alta)**: `_apply_filters` e
  `gen_range_for` normalizam com `.lower()` → funcionam.
- **`cpu_family=intel` sem ads Intel na base**: `_cpu_family_groups` não insere
  o alias nos optgroups (é hardcoded no topo); filtro retorna vazio.
- **`cpu_family=intel&cpu_family=amd` (duplicado na query)**: GET não permite
  dois valores; se enviado via string repetida, `args.get` retorna o primeiro —
  sem erro, comportamento idempotente.
- **Alias + geração fora da faixa** (`cpu_family=amd&gen_min=10`): vazio
  naturalmente (nenhum Ryzen ≥ 10ª), sem clamp no SQL.
- **Athlon** no grupo AMD não tem geração → excluído de `gen_min` (NULL),
  correto; aparece com `cpu_family=amd` sem geração.

## 9. Testes (offline, sem rede)

### `tests/test_ad_service.py`

- `test_gen_range_for_vendor`: `gen_range_for("intel") == (1, 14)`,
  `gen_range_for("amd") == (1, 9)`, `gen_range_for("AMD") == (1, 9)`,
  `gen_range_for("i5") == (1, 14)`, `gen_range_for("core2") is None`.
- `test_list_ads_by_vendor`: `AdFilters(cpu_family="intel")` → só ids
  Intel ({1, 2, 4} no seed); `AdFilters(cpu_family="amd")` → {5}.
- `test_gen_by_vendor`: `AdFilters(cpu_family="intel", gen_min=8)` → {1}
  (i5-8500); `AdFilters(cpu_family="amd", gen_min=5)` → {5}; `gen_min=6` → ∅.

### `tests/test_api.py`

- `test_cpu_family_vendor`: `/api/ads?cpu_family=intel` → só famílias Intel;
  `/api/ads?cpu_family=amd` → só AMD; `intel&gen_min=8` → total 1 (i5-8500);
  `amd&gen_min=5` → total 1 (ryzen5); `amd&gen_min=6` → total 0.
- (Seed atual: i3, i5, i7, core2 ausente, ryzen5 — cobrem ambos os grupos.)

### `tests/test_ui.py`

- `test_filters_vendor_options`: `/` contém `<option value="intel">Intel
  (qualquer)</option>` e `<option value="amd">AMD (qualquer)</option>`.
- `test_filters_vendor_enables_gen`: com `/?cpu_family=intel`, `#gen_min` **não**
  está `disabled` e contém opções até 14ª; com `/?cpu_family=amd`, até 9ª.

### `tests/test_chart.py`

- `test_chart_page_vendor`: `/chart?cpu_family=intel` renderiza `chart.js` e
  contém `"generation": 8`; `/chart?cpu_family=amd` contém `"generation": 5`
  e **não** `"id": 1` (Intel).

Rodar a suíte completa: `uv run pytest` (100% offline).

## 10. Documentação

- **D-030** em `docs/specs/00-decisoes.md` (fechada): valores especiais
  `intel`/`amd` em `cpu_family` filtram pelo grupo de famílias do fabricante;
  geração passa a valer por **fabricante ou família** (domínio de escala:
  `VENDOR_RANGE` 1–14/1–9); UI com `Intel (qualquer)`/`AMD (qualquer)` no topo
  do select de D-029; `/chart` aceita fabricante. Alternativas descartadas:
  select separado `Fabricante` (controle a mais, exclusão mútua com família),
  parâmetro novo `cpu_vendor` (API mais verbosa, sem ganho — `cpu_family` já é
  livre), filtro por era cross-vendor (spec própria).
- **Spec 17 / D-029**: atualizar o parágrafo de domínio de escala (família **ou**
  fabricante) e a referência ao `gen_range_for`.
- `docs/specs/07-api-e-ui.md`: documentar `cpu_family=intel|amd` e que
  `gen_min/gen_max` passam a valer também com fabricante.
- `docs/wiki/api-e-ui.md`: idem (curto).
- `AGENTS.md` — nota na seção D-029: fabricante via `cpu_family=intel|amd`;
  `VENDOR_ALIASES`/`VENDOR_RANGE`.

## 11. Checklist de entrega

1. `ad_service.py`: `VENDOR_ALIASES`, `VENDOR_RANGE`, `gen_range_for`,
   `cpu_group`, `_apply_filters` (alias → `IN(group)`).
2. `_filters.html`: opções `Intel (qualquer)`/`AMD (qualquer)` no topo.
3. `main/routes.py`: `_cpu_family_groups` ignora aliases no fallback.
4. `chart.html`: textos "família ou fabricante".
5. Testes: ad_service + api + ui + chart; `uv run pytest` verde.
6. Docs: D-030, spec 17, `07-api-e-ui.md`, wiki, AGENTS.md.

## Perguntas em aberto

- [ ] **Preset de era cross-vendor** ("moderno: Intel ≥8 / Ryzen ≥3", ou filtro
  por ano de lançamento) para comparar Intel e Ryzen sem escolher família nem
  fabricante? Vira spec própria (pendência da spec 17).
- [ ] **Qualidade da geração do Ryzen 8000G** (8500G → "8ª" mas é Zen 4): corrigir
  na extração (mapa série→era) em spec separada? (pendência da spec 17)
