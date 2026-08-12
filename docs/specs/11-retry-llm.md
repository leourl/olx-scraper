# 11 — Retry automático de extração LLM (JSON inválido)

## Contexto e problema

A LLM (DeepSeek V4 Flash) ocasionalmente devolve **JSON inválido** ao preencher
o schema do anúncio. Caso típico — a modelo **ecoa a definição do schema** em
vez de dados:

```
JSON da LLM inválido (1 validation error for AdSpec
  Invalid JSON: trailing characters at line 2 column 1 [type=json_invalid,
  input_value='{"description": "Specs e...ull, "confidence": 0.9}', ...])
```

Isso acontece porque a saída vem como `{"description": "...", "properties":
{...}, "title": ...}` (a definição do schema) + lixo de caracteres extras.

**Comportamento atual** (sem retry):

1. `DeepSeekClient._parse_output` (`app/extractors/llm.py:114`) tenta
   `AdSpec.model_validate_json(text)` → falha → loga e retorna `None`.
2. `extract_specs` levanta `LlmError("resposta sem content/mensagem válida")`.
3. `pipeline.extract_specs` captura (`except Exception`) e retorna `None` →
   `run_process` conta o ad como **falha**.
4. O ad falho **não** recebe `extracted_at` (o `save_specs` só roda no sucesso),
   então o próximo `process`/ciclo do autorun **já o re-tenta automaticamente**.
   Na prática, uma falha transitória = o anúncio **atrasa um ciclo** e aparece
   como `falha` nas stats. O `--force` (`flask process --ad <id>`) só serve
   para reprocessar anúncios **já concluídos** — não é o caminho do falho.

O prompt já tem uma linha anti-echo ("Responda unicamente com o objeto JSON
preenchido — sem o schema, sem texto extra"), mas não basta: o fallback precisa
de **re-tentativa automática no mesmo run** (elimina o atraso de um ciclo).

## Objetivos

- Quando a LLM devolver JSON inválido / falhar a validação pydantic, **re-tentar
  automaticamente no mesmo run** (até N vezes) com uma **nota corretiva**
  explícita (anti-echo) anexada ao input.
- Somar `LlmUsage` (tokens) entre tentativas para manter o custo real —
  **inclusive no caminho de falha**: hoje `pipeline.extract_specs` descarta o
  uso acumulado (retorna `LlmUsage()`), então tentativas falhas não aparecem
  nas stats. O `LlmError` passa a carregar o uso acumulado.
- Reportar **quantas retries** aconteceram nas stats do `run_process`/CLI.
- Sempre terminar em `LlmError` se todas as tentativas falharem → ad permanece
  pendente (comportamento atual preservado como último recurso).

## Não-objetivos (fora de escopo por ora)

- Retry de **erros HTTP** (5xx/429/timeout) da API — escopo desta spec é a
  falha de **parse/validação** do JSON (pedido do usuário). Ver perguntas em
  aberto.
- Fila/reprocessamento automático assíncrono (D-003) — a spec cobre retry
  imediato síncrono no mesmo `flask process`.
- Sanitização "cosmética" do JSON ecoado (regex de limpeza) — frágil; o retry
  corretivo é mais robusto. Ver perguntas em aberto.

---

## 1. Configuração

`app/config.py`:

```python
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
```

- `LLM_MAX_RETRIES` = **tentativas extras após a primeira** (default `2` →
  até **3 chamadas** por anúncio). `0` = sem retry (comportamento atual).
- TestConfig em `tests/conftest.py`: `LLM_MAX_RETRIES = 2`.

## 2. `DeepSeekClient`

### `LlmUsage` ganha contador

`app/extractors/llm.py:19` — novo campo `retries: int = 0` (default = compatível
com todos os usos atuais). Acumulado entre tentativas.

### Construtor

Novo parâmetro `max_retries: int = 0` (default preserva o comportamento atual
de 1 chamada). `run_process` passa `max_retries=cfg["LLM_MAX_RETRIES"]`.

### Loop em `extract_specs`

Refatorar para até `1 + max_retries` tentativas:

```
tentativa 1:  body normal (INSTRUCTIONS + input_text)
tentativa N>1: mesma INSTRUCTIONS (constante!), input_text += nota corretiva
```

**Detalhe de cache (importante):** a nota corretiva é anexada ao **final do
`input_text`** (depois do título/descrição), **nunca** às `instructions`. O
cache de prefixo da DeepSeek (instruções + começo do input) permanece **hit**,
e só o trecho final (a nota) é novo — o retry fica barato (~reuse do cache).
Mudar `instructions` quebraria o prefixo e derrubaria o cache-miss para a
chamada inteira.

Nota corretiva (constante, `RETRY_NOTE`, em pt-BR):

> Sua resposta anterior não passou na validação: o JSON estava inválido
> (provavelmente você ecoou a definição do schema — 'description', 'properties',
> 'title', 'anyOf' — ou adicionou caracteres extras). Responda APENAS com o
> objeto JSON preenchido com os valores dos campos (ex.:
> {"brand": "Dell", "ram_gb": 16}), sem mais nada.

Fluxo por tentativa:

1. POST `/responses` → `raise_for_status()` → `data` (erros HTTP continuam
   levantando `LlmError` **imediatamente**, sem retry — escopo).
2. `usage = _parse_usage(...)` → **somar** em `total_usage`.
3. `spec = _parse_output(data)`:
   - sucesso → retorna `(spec, total_usage)`.
   - `None` (JSON inválido / sem mensagem) → `total_usage.retries += 1`, loga
     `tentativa k/N`, anexa `RETRY_NOTE` ao `input_text` e tenta de novo.
4. Tentativas esgotadas → `raise LlmError(msg, usage=total_usage)` — o erro
   carrega o **uso acumulado** das tentativas falhas (ver §3).

A validação falha pode ser de dois tipos (ambos retryáveis): `json_invalid`
(parse) ou `validation error` do pydantic (tipos/enums fora do schema). O
`_parse_output` já captura ambos num único `except`.

### `LlmError` carrega o uso

`app/extractors/llm.py:15` — `LlmError(RuntimeError)` ganha atributo opcional
`usage: LlmUsage | None = None` (default `None`, compatível com os `raise
LlmError("...")` existentes).

## 3. Integração

### `app/services/runner.py` — `run_process`

- Constrói o client com `max_retries=cfg["LLM_MAX_RETRIES"]`.
- Nova chave `retries` nas stats (soma de `usage.retries`).
- **Caminho de falha passa a somar uso também**: no branch `spec is None`, somar
  `usage.input_tokens/output_tokens/cached_tokens/retries` nas totais (hoje
  descarta). Como o `pipeline` retorna o uso carregado no `LlmError`, tentativas
  falhas entram em `tokens_*`, `retries` e `custo`.
- O custo (`custo`) já usa os tokens somados por `LlmUsage` — retries entram
  no custo automaticamente (inputs de retry têm cache-hit alto, ver §5).

### CLI (`app/cli.py`)

`flask process` passa a ecoar `| retries: N` junto com as demais métricas.

### `app/extractors/pipeline.py`

No caminho de falha, retornar o uso acumulado em vez de `LlmUsage()`:

```python
except LlmError as e:
    return None, (e.usage or LlmUsage()), "", None, None, None
except Exception:
    return None, LlmUsage(), "", None, None, None
```

(`test_pipeline_llm_failure_returns_none` usa 500 → sem uso → `LlmUsage()`
vazio → assert `input_tokens == 0` continua válido.)

## 4. Comportamento esperado

| cenário | resultado |
|---------|-----------|
| 1ª chamada válida | 1 chamada, `retries: 0` (zero overhead no caso comum) |
| 1ª inválida, 2ª válida | 2 chamadas, `retries: 1` na stats; input da 2ª com nota corretiva |
| todas inválidas (≤ max) | `LlmError` (com `usage` acumulado) após `1 + max_retries` chamadas → ad falha e fica pendente; uso/retries entram nas stats |
| HTTP 5xx/429/timeout | `LlmError` imediato (sem retry nesta spec) |

## 5. Custo

- Caso comum (JSON válido de primeira): **zero custo extra**.
- Retry: apenas quando há falha. A nota corretiva no fim do input preserva o
  cache de prefixo → retry custa basicamente o trecho novo + output rejeitado
  (~poucos centavos de milésimo). Mesmo com `max_retries=2`, o custo médio por
  anúncio permanece ~US$0.00016 na prática (falhas são raras).

## 6. Casos de borda

- **`LLM_MAX_RETRIES=0`**: comportamento idêntico ao atual (compatibilidade).
- **JSON válido mas com `ram_gb`/`storage_gb` = 0**: não é retryável — sanitizado
  por `pipeline._merge`/regex (comportamento existente, sem mudança).
- **Eco de schema persistente** em todas as tentativas: `LlmError` ao final →
  ad fica pendente para reprocesso manual; stats mostram `falhas` e `retries`.
- **Timeouts**: `httpx.HTTPError` → `LlmError` imediato (sem consumo de retries
  de JSON); ad fica pendente.
- **Contadores**: `total_usage.retries` soma só tentativas **falhas** (a
  chamada final bem-sucedida não incrementa).

## 7. Testes (offline, `tests/test_llm.py` + ajustes)

- `test_extract_specs_ok` — inalterado (1 chamada, `retries == 0`).
- `test_extract_specs_invalid_json_raises` — continua valendo: o `make_client`
  atual usa `max_retries=0` (default) → 1 chamada → `LlmError`. Os **novos**
  testes de retry constroem clientes com `max_retries` explícito.
- **Novo** `test_retries_then_succeeds`: 1ª resposta inválida, 2ª válida →
  spec retornado; `usage.retries == 1`; tokens **somados** entre as 2 chamadas.
- **Novo** `test_retry_appends_corrective_note`: handler captura os bodies;
  na 2ª chamada `input` contém `RETRY_NOTE` e `instructions` permanece **igual**
  (verifica preservação do prefixo de cache).
- **Novo** `test_max_retries_calls_limit`: contador de chamadas == `1 + max`
  quando sempre inválido.
- **Novo** `test_http_error_not_retried`: 429 na 1ª chamada → `LlmError` com
  exatamente **1** request.
- **Novo** `test_failure_carries_usage`: sempre JSON inválido (com `max_retries`),
  `pipeline.extract_specs` retorna `spec is None` e `usage.input_tokens > 0`
  (uso acumulado das tentativas), `usage.retries == max_retries`.
- `test_run_process` (`tests/test_runner.py`): stats contém `retries`;
  com MockTransport válido → `retries == 0`.
- TestConfig: `LLM_MAX_RETRIES = 2`.

## 8. Documentação

- **D-023** em `docs/specs/00-decisoes.md` (fechada, formato do registro).
- `docs/specs/06-extracao-llm.md` — seção "Confiabilidade": retry automático +
  `LLM_MAX_RETRIES`.
- `AGENTS.md` — seção "Extração LLM (DeepSeek)": falha de JSON agora com retry
  automático; nota corretiva anexada ao input (cache preservado).
- `docs/specs/08-operacao.md` — variável de ambiente `LLM_MAX_RETRIES`.

## 9. Checklist de entrega

1. `config.py` + `TestConfig`: `LLM_MAX_RETRIES`.
2. `LlmUsage.retries` + `DeepSeekClient(max_retries)` + loop corretivo
   (`RETRY_NOTE` anexado ao input, `instructions` constante).
3. `run_process` passa `max_retries` e expõe `retries` nas stats; CLI ecoa.
4. Testes novos + suíte completa verde.
5. Docs (D-023, 06, 08, AGENTS.md).

## Perguntas em aberto

- [ ] Retry também para **HTTP 5xx/429/timeout** (com backoff) — escopo futuro.
- [ ] Sanitizar JSON ecoado (strip da definição do schema) como 1º fallback
  **antes** do retry, para economizar a 2ª chamada?
- [ ] Persistir `retries` por anúncio (coluna) para auditar taxa de falha?
