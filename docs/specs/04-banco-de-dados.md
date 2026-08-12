# 04 — Banco de dados

## Stack

- SQLite (arquivo em `instance/`), via SQLAlchemy + Alembic (migrações)
- (Decisão futura se valer Postgres — ver D-002)

## Modelo implementado

### Tabela `ads` (dados brutos do anúncio)

| coluna        | tipo        | restrições                         |
|---------------|-------------|------------------------------------|
| id            | int         | PK, autoincrement                  |
| olx_id        | str         | id do anúncio na OLX              |
| title         | str         |                                    |
| description   | text        | texto cru, sempre salvo            |
| price_cents   | int         | preço em centavos; null = sob consulta |
| url           | str         | **unique** (dedup)                 |
| city          | str         |                                    |
| state         | str         | UF                                 |
| published_at  | datetime    | data de publicação (UTC)           |
| scraped_at    | datetime    | quando coletou                      |
| extracted_at  | datetime    | null se ainda não extraiu specs    |

### Tabela `ad_images` (galeria do anúncio — 1:N)

| coluna    | tipo   | restrições |
|-----------|--------|------------|
| id        | int    | PK         |
| ad_id     | int    | FK -> ads.id, index |
| url       | str    |            |
| position  | int    | ordem na galeria |

### Tabela `ad_specs` (specs estruturados — um-para-um com ads)

| coluna        | tipo   | restrições |
|---------------|--------|------------|
| id            | int    | PK         |
| ad_id         | int    | FK -> ads.id, unique |
| brand         | str    | ex.: Dell, Lenovo |
| model         | str    | ex.: OptiPlex 7050 |
| form_factor   | str    | mini / sff / tower / notebook |
| cpu           | str    | texto (ex.: "i5-8400") |
| cpu_family    | str    | i3 / i5 / i7 / i9 / ryzen3..9 (D-012) |
| cpu_model     | int    | ex.: 8500, 13500 |
| cpu_generation | int   | Intel: geração (8500→8); Ryzen: série (5600→5) (D-016) |
| ram_gb        | int    | ex.: 16 |
| storage_gb    | int    | ex.: 512 |
| storage_type  | str    | ssd / hdd / nvme |
| gpu           | str    | null se integrada |
| confidence    | float  | 0–1, confiança da extração |
| extraction_method | str | regex+llm / llm |
| extracted_at  | datetime | |

## Perguntas em aberto

- [x] ~~Preço~~ → **int centavos** (`price_cents`) — D-007
- [x] ~~D-002~~ → **colunas normalizadas** (tabela `ad_specs` implementada)
- [x] ~~Moeda/ano/condição~~ → **removidos** (D-013)
- [x] ~~Imagens~~ → **todas**, tabela `ad_images` (D-011)
- [x] ~~Data de publicação~~ → `published_at` UTC (D-014)
- [ ] Guardar imagens localmente ou só a URL? → hoje: só URL

## Migrações

- Alembic: `flask db init`, `migrate`, `upgrade`
- (arquivos ficam em `migrations/`)
