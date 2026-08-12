# 16 — Ocultar anúncio manualmente (toggle "Disponível")

## Contexto e problema

O re-processo de geração (D-027) e o cadastro manual (D-026) trouxeram para o
banco muitos anúncios que o usuário considera **sucata/peças** (ou simplesmente
não quer ver). Esses anúncios continuam aparecendo no gráfico, ofertas, revisão
e na busca, poluindo a análise.

Não existe forma de o usuário **esconder um anúncio manualmente**: o `is_active`
existente é controlado pelo `run_check` (404/410 → removido) e, pior, o
`upsert_raw` **re-ativa** (`is_active=True`) qualquer anúncio que reapareça numa
listagem — e sucata costuma ficar publicada na OLX. Reusar `is_active` para o
toggle significaria que o próximo `scrape` traria o anúncio de volta.

## Objetivos

- Adicionar um **toggle "Disponível"** na página `/ads/<id>` que oculta o
  anúncio de **lista, gráfico, ofertas, revisão e stats** (e o tira de
  `enrich`/`check`/`process`), sem reativá-lo por scrape.
- Flag **separada e persistente** (`Ad.user_disabled`, bool default `False`):
  o scrape **não** mexe nela; o anúncio fica oculto até o usuário reativar.
- `include_inactive` passa a revelar também os ocultos (para revisão).
- Registrar a decisão (D-028) em `docs/specs/00-decisoes.md`.
- 100% offline nos testes.

## Não-objetivos (fora de escopo por ora)

- **Ação em massa** (esconder vários anúncios de uma vez na listagem) — vira
  spec própria; aqui é 1 toggle por anúncio, como pedido.
- **Remover o anúncio do banco** — ocultar preserva os dados; reativar mostra
  tudo de novo.
- **Eliminar/refazer specs** ao ocultar — não são tocadas.
- **Auto-detecção de sucata** — o `is_parts`/`best_deals` já flageia
  ("peça/sucata"); o ocultamento é decisão manual.

---

## 1. Modelo — `app/models/ad.py`

Nova coluna (independente de `is_active`):

```python
user_disabled = db.Column(db.Boolean, nullable=False, default=False, server_default=text("0"))
```

- `False` = disponível (default). `True` = oculto manualmente pelo usuário.
- O `scrape` (`upsert_raw`) **não** altera essa coluna — reativar `is_active`
  de um anúncio visto na listagem não o desoculta.
- Requer **migração**: `uv run flask db migrate -m "user_disabled (oculto manualmente) em ads"` → `uv run flask db upgrade`.

## 2. `app/services/ad_service.py` — filtros, serializer, stats

### `_apply_filters` (l.188)

Todos os caminhos que passam por aqui (lista, `/chart`, `/offers`, `/review`)
passam a ocultar por padrão:

```python
if not f.include_inactive:
    query = query.filter(Ad.is_active.is_(True), Ad.user_disabled.is_(False))
```

`include_inactive=True` revela removidos **e** ocultos.

### Filas de trabalho (pulam ocultos)

- `list_pending_extraction` (l.123) e `list_missing_description` (l.133):
  adicionar `Ad.user_disabled.is_(False)` — não gastar request/LLM com lixo.

### `_price_by_generation` (l.259)

Adicionar `Ad.user_disabled.is_(False)` — benchmarks de ofertas ignoram ocultos.

### `stats()` (l.367–380)

- Totais (`total`, `com_specs`, `sem_specs`, `preco_cents`, `por_cpu_family`)
  excluem `user_disabled=True`.
- `removidos` segue contando `is_active=False` (semântica "removido da OLX").
- Novo contador **`ocultos`** = `Ad.user_disabled.is_(True)` (visível no `/` e
  na API, permite saber quantos foram escondidos).

### `ad_to_dict` (l.410)

Expor o estado para a UI/API:

```python
"user_disabled": ad.user_disabled,
```

### Helper novo

```python
def set_user_disabled(ad_id: int, disabled: bool) -> Ad | None:
    ad = db.session.get(Ad, ad_id)
    if ad is None:
        return None
    if ad.user_disabled != disabled:
        ad.user_disabled = disabled
        db.session.commit()
    return ad
```

## 3. `app/services/runner.py` — pulam ocultos

- `run_check` (l.192): `Ad.query.filter(Ad.is_active.is_(True), Ad.user_disabled.is_(False))` — não verificar disponibilidade de lixo oculto.
- `run_process`:
  - `missing_generation` (l.271): + `Ad.user_disabled.is_(False)`.
  - `force` (l.279): + `Ad.user_disabled.is_(False)`.
- `upsert_raw` **inalterado** (não toca `user_disabled`).

## 4. API — `POST /api/ads/<int:ad_id>/disabled`

`app/blueprints/api/routes.py`:

```python
@bp.post("/ads/<int:ad_id>/disabled")
def set_ad_disabled(ad_id: int):
    data = request.get_json(silent=True) or {}
    disabled = data.get("disabled")
    if not isinstance(disabled, bool):
        return jsonify({"error": "campo 'disabled' deve ser booleano"}), 400
    ad = ad_service.set_user_disabled(ad_id, disabled)
    if ad is None:
        return jsonify({"error": "não encontrado"}), 404
    return jsonify({"ok": True, "disabled": ad.user_disabled,
                    "ad": ad_service.ad_to_dict(ad, include_description=True)})
```

## 5. UI — página `/ads/<id>`

### `app/blueprints/main/templates/ad_detail.html`

Badge de oculto + switch "Disponível" (reuso do CSS `.switch` do autorun):

```html
{% if ad.user_disabled %}
<p><span class="badge oculto">oculto manualmente</span></p>
{% endif %}
...
<label class="switch">
    <input type="checkbox" id="ad-available-toggle" {% if not ad.user_disabled %}checked{% endif %}>
    <span class="switch-slider"></span>
    <span class="switch-label">Disponível</span>
</label>
<span id="ad-toggle-status" class="muted" hidden></span>
```

O badge "removido" existente (l.9) permanece independente — um ad pode ser
`is_active=False` **e** `user_disabled=True` (mostra os dois badges).

### `app/static/js/app.js` — `initAdDetail()`

Chamado no `DOMContentLoaded` (padrão de `initRunPage`/`initAddAd`):

- No `change` do `#ad-available-toggle`: `POST /api/ads/<id>/disabled` com
  `{disabled: !checked}`.
- Sucesso → atualiza badge "oculto manualmente" (mostra/esconde) e mostra
  "salvo ✓"; erro → reverte o checkbox e mostra a mensagem.

### `index.html` (l.37) + `_filters.html` (l.64)

- Card da listagem: badge "oculto" quando `ad.user_disabled` (visível só com
  `include_inactive`).
- Label do filtro: "incluir removidos" → "incluir removidos/ocultos".

### CSS

Badge `.badge.oculto` (tons âmbar, distinto de `.removed`).

## 6. Comportamento esperado

| ação | resultado |
|---|---|
| Toggle OFF no detalhe | `user_disabled=True`, badge "oculto manualmente"; some de lista/gráfico/ofertas/review/stats |
| Toggle ON | `user_disabled=False`; volta a aparecer (specs intactas) |
| Anúncio oculto visto na listagem do scrape | `upsert_raw` re-ativa `is_active` mas **não** desoculta |
| `run_check`/`process`/`enrich` | pulam ocultos (sem request/LLM gasto) |
| `include_inactive=1` | revela removidos **e** ocultos (badges distintos) |
| `stats.ocultos` | conta quantos o usuário escondeu |
| Anúncio oculto com URL direta | `/ads/<id>` continua acessível (toggle + badge) |

## 7. Custo / impacto

- **Sem custo monetário**; sem novas dependências.
- **Migração de banco** (coluna nova com `server_default` — backfill sem dor).
- **Nenhum request extra**: os filtros são SQL locais; o toggle é 1 request.
- **Concorrência**: não interfere com `RunManager`/autorun.

## 8. Casos de borda

- **Ad removido da OLX (`is_active=False`) e depois oculto pelo usuário**: flags
  independentes; badges somam. Reativar o toggle não restaura `is_active`.
- **Ad oculto que ainda está publicado**: `run_check` pula (não marca nada);
  `upsert_raw` de scrape re-ativa `is_active` mas ele continua oculto.
- **Toggle em ad inexistente**: `set_user_disabled` retorna `None` → 404.
- **Body sem `disabled` / tipo errado**: 400.
- **Ocultar antes das specs**: specs não são apagadas; reativar mostra tudo.
- **`include_inactive` + `has_specs=false`** (revisão): revela ocultos sem specs
  para o usuário decidir.

## 9. Testes (offline, sem rede)

`tests/test_ad_service.py`:
- `test_list_hides_user_disabled`: ad oculto fora da lista default; entra com
  `include_inactive=True`.
- `test_chart_and_deals_exclude_user_disabled`: ad com preço+geração e
  `user_disabled=True` não aparece em `chart_data`/`best_deals`/`price_benchmarks`.
- `test_stats_exclude_user_disabled`: `total`/`com_specs`/`por_cpu` ignoram
  oculto; `ocultos == 1`.
- `test_pending_and_enrich_skip_user_disabled`: oculto fora de
  `list_pending_extraction`/`list_missing_description`.
- `test_set_user_disabled`: liga/desliga; id inexistente → `None`.

`tests/test_api.py`:
- `test_set_ad_disabled`: POST on/off → 200 + `ad.user_disabled`; 400 sem bool;
  404 id inexistente.

`tests/test_ui.py`:
- `test_ad_detail_has_available_toggle`: `/ads/1` contém `#ad-available-toggle`
  e texto "Disponível".
- badge "oculto" no card com `include_inactive`.

`tests/test_check.py` (ou `test_runner.py`):
- `test_run_check_skips_user_disabled`: ad oculto não é checado (sem request).
- `test_run_process_missing_generation_skips_user_disabled`.

Rodar a suíte completa: `uv run pytest` (100% offline).

## 10. Documentação

- **D-028** em `docs/specs/00-decisoes.md` (fechada): toggle "Disponível" com
  flag separada `Ad.user_disabled` (persistente; scrape não mexe), filtros
  `is_active=True AND user_disabled=False` em lista/gráfico/ofertas/review/stats,
  filas de trabalho (`check`/`process`/`enrich`) pulam ocultos, `include_inactive`
  revela removidos+ocultos, stats ganha `ocultos`. Alternativa descartada:
  reusar `is_active` (scrape re-ativaria na próxima listagem).
- `docs/specs/07-api-e-ui.md` — endpoint `POST /api/ads/<id>/disabled`.
- `AGENTS.md` — nota sobre `user_disabled` e o toggle.

## 11. Checklist de entrega

1. Modelo + migração (`user_disabled`).
2. `ad_service.py`: `_apply_filters`, filas de trabalho, `_price_by_generation`,
   `stats` (`ocultos`), `ad_to_dict`, `set_user_disabled`.
3. `runner.py`: `run_check`/`missing_generation`/`force` pulam ocultos.
4. API: `POST /api/ads/<id>/disabled`.
5. UI: `ad_detail.html` (toggle + badge), `app.js` (`initAdDetail`),
   `index.html` (badge), `_filters.html` (label), CSS `.badge.oculto`.
6. Testes novos + `uv run pytest` verde.
7. Deploy RPi: `git pull` → `uv run flask db upgrade` → reiniciar serviço.
8. Docs: D-028, 07-api-e-ui, AGENTS.md.

## Perguntas em aberto

- [ ] **Ação em massa** (ocultar vários anúncios de uma vez na listagem /
  "ocultar toda sucata")? Vira spec própria.
- [ ] Persistir **quem/quando** ocultou (`hidden_at`) para auditoria? Requer
  coluna adicional.
- [ ] Mostrar o `ocultos` na página inicial (stats) além da API?
