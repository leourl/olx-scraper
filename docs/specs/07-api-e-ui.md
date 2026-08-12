# 07 — API e UI

## Visão

Duas frentes sobre o mesmo banco:
- **API REST** (`/api/...`) para scripts e futuras integrações;
- **UI web** (templates Flask) para navegar e filtrar no navegador.

## API REST (implementado)

| método | rota              | descrição |
|--------|-------------------|-----------|
| GET    | `/api/ads`        | listar anúncios (filtros + paginação) |
| GET    | `/api/ads/<id>`   | anúncio + specs + imagens + descrição crua |
| POST   | `/api/ads/import` | cadastrar anúncio por link direto da OLX (D-026) |
| POST   | `/api/ads/<id>/disabled` | toggle "Disponível" — ocultar/exibir anúncio manualmente (`{disabled}`) (D-028) |
| GET    | `/api/stats`      | resumo (total, com/sem specs, faixa de preço, por cpu_family, ocultos) |

### Parâmetros de busca (em `/api/ads`)

```
q=           texto livre (título/descrição)
brand=       dell | lenovo
model=       optiplex 7050
cpu_family=  i3 | i5 | i7 | i9 | ryzen3..  (agrupado Intel/AMD na UI) —
             **intel | amd** (D-030): todas as famílias do fabricante
cpu_model=   ex.: 8500
gen_min/     filtro de geração **por família ou fabricante** (Intel: geração
gen_max=     1–14; Ryzen: série 1–9; intel: 1–14; amd: 1–9). Ex.:
             cpu_family=i5&gen_min=8 → i5 da 8ª em diante;
             cpu_family=amd&gen_min=5 → Ryzen série ≥ 5.
             ⚠️ **D-029/D-030:** sem `cpu_family`, gen_min/gen_max são
             **ignorados** (as escalas Intel/Ryzen são incomparáveis; resposta
             permissiva, sem erro, para não quebrar clientes antigos).
ram_min=     filtro ram_gb >= N
storage_min= filtro storage_gb >= N
price_min/price_max= preço em centavos
form_factor= mini | sff | tower | notebook | all-in-one
has_specs=   true | false
confidence_min= 0.0–1.0
sort=        newest | price_asc | price_desc | confidence
page= / per_page=  (per_page máx. 100, default 20)
```

### Formato de resposta

```json
{
  "items": [
    {
      "id": 1,
      "title": "...",
      "price_cents": 285000,
      "url": "...",
      "city": "São Paulo",
      "state": "SP",
      "published_at": "2026-07-31T18:25:00+00:00",
      "images": ["https://img/...jpg"],
      "specs": {
        "brand": "Dell", "model": "OptiPlex 7050", "form_factor": "sff",
        "cpu": "i5-8500", "cpu_family": "i5", "cpu_model": 8500,
        "cpu_generation": 8,
        "ram_gb": 16, "storage_gb": 512, "storage_type": "ssd",
        "gpu": null, "confidence": 0.9, "extraction_method": "regex+llm"
      }
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 20
}
```

`/api/ads/<id>` inclui ainda `description` (texto cru). Preço é sempre
`price_cents` (int). Sem `currency` (D-013).

### Cadastro manual — `POST /api/ads/import`

Body: `{"url": "https://.../anuncio-1234567890", "process": true}` (`process`
default `true` = extrair specs na hora). Valida o link, busca o detalhe e faz o
upsert (duplicado re-busca e atualiza). Sucesso:

```json
{
  "status": "ok",
  "created": true,
  "processed": true,
  "ad": { "id": 123, "title": "...", "price_cents": 285000, "specs": {...} }
}
```

- `201` criado · `200` atualizado (já existia) · `400` link inválido ·
  `410` anúncio removido na OLX · `422` página sem JSON-LD ·
  `503` OLX bloqueou (403) · `502` erro de rede.

## UI web (implementado)

- `GET /` — listagem com filtros (q, marca, cpu_family, gen_min, ram_min,
  storage_min, price_max, form_factor, sort) + paginação; estilizado com **Pico CSS** (CDN)
- `GET /ads/<id>` — detalhe: galeria de imagens, specs, descrição crua, link OLX
- `GET /chart` — **gráfico de dispersão preço × geração** (Chart.js via CDN,
  cor por marca, hover com tooltip, clique abre o anúncio); **exige uma
  família de CPU ou fabricante** (aviso no lugar do canvas sem família — D-029;
  fabricante via `cpu_family=intel|amd` — D-030)
- `GET /offers` — **análise de compra**: benchmark de preço por
  **(família, geração)** (p25/p50/p75) + ranking de ofertas por desconto vs
  mediana da mesma família e geração, com bandeiras de alerta (peça/sucata,
  muito barato, acima/abaixo do mercado) — D-029
- `GET /review` — anúncios sem specs ou com `confidence < 0.6`

Templates em `app/blueprints/main/templates/` (filtros compartilhados em `_filters.html`).
