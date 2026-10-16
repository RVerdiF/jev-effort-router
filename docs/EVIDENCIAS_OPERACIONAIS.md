# Evidências Operacionais — jev-effort-router (Fork & Decider Plugável)

Este documento registra a execução empírica, comandos reproduzíveis e evidências verificáveis de acordo com os critérios de aceite do card `t_1a091c5a` e as solicitações de revisão (Rounds 1 a 4).

---

## 1. Publicação e Sincronização de Refs

### 1.1 Resolução de `uv.lock`
O `uv.lock` foi explicitamente sincronizado com a especificação de dependência de desenvolvimento `ruff >=0.15.0` do `pyproject.toml`.

```bash
uv lock --check
# Saída: Resolved 17 packages in 1ms
```

### 1.2 Publicação dos Refs
Os commits de correção e de sincronização do lockfile foram publicados tanto na branch de trabalho (`wt/t_1a091c5a`) quanto na branch default do repositório (`main`):

```bash
git ls-remote --symref origin HEAD refs/heads/main refs/heads/wt/t_1a091c5a
```

Saída verificada:
```text
ref: refs/heads/main	HEAD
0060f7c69679439ee3e4298edafa7c08bb0dcd0d	HEAD
0060f7c69679439ee3e4298edafa7c08bb0dcd0d	refs/heads/main
0060f7c69679439ee3e4298edafa7c08bb0dcd0d	refs/heads/wt/t_1a091c5a
```

---

## 2. Instalação no Perfil de Teste (`test_jev`)

O plugin foi reinstalado do fork no GitHub sem seleção de branch, validando que o ref default (`main`) aponta para o commit correto:

```bash
hermes -p test_jev plugins install RVerdiF/jev-effort-router --force
hermes -p test_jev plugins enable jev-effort-router
```

### 2.1 Verificação de Metadados de Instalação
Leitura de `/home/rafael/hermes/home/profiles/test_jev/plugins/.install-metadata.json`:

```json
{
  "jev-effort-router": {
    "pinned": false,
    "revision": "0060f7c69679439ee3e4298edafa7c08bb0dcd0d",
    "source": "https://github.com/RVerdiF/jev-effort-router.git"
  }
}
```

---

## 3. Turno Real Roteado em Produção (`test_jev`)

### 3.1 Execução do Turno
Comando executado:
```bash
env OPENCODE_GO_API_KEY="[REDACTED]" hermes -p test_jev chat -q "What is 2+2? Answer with only the number."
```

Saída:
```text
Query: What is 2+2? Answer with only the number.
Initializing agent...
────────────────────────────────────────
╭─ ☤ Hermes ───────────────────────────────────────────────────────────────────╮
│ 4                                                                            │
╰──────────────────────────────────────────────────────────────────────────────╯
Duration:       5s
Messages:       2 (1 user, 0 tool calls)
```

### 3.2 Registro de Roteamento no Audit (`routes.jsonl`)
Trecho registrado em `/home/rafael/hermes/home/profiles/test_jev/plugin-data/agent-plugin-jev-effort-router-ee542f23/routes.jsonl`:

```json
{
  "ts": "2026-10-15T22:25:14+00:00",
  "event": "route",
  "model": "deepseek-v4.1-flash",
  "effort": "low",
  "effort_requested": "low",
  "model_choice": "deepseek-v4.1-flash",
  "effort_choice": "low",
  "model_confidence": 0.99,
  "effort_confidence": 0.99,
  "model_probabilities": {"deepseek-v4.1-flash": 0.99},
  "effort_probabilities": {"low": 0.99},
  "alternatives": ["kimi-k3"],
  "latency_ms": 1511,
  "fallback_reasons": [],
  "jev_model": "typesafe/jev-1.13",
  "decider": "openai_compatible",
  "replayed": false,
  "turn_id": "20261015_192511_ce62fa:20261015_192511_ce62fa:995605d1",
  "session_id": "20261015_192511_ce62fa",
  "platform": "cli",
  "api_call_count": 1
}
```

---

## 4. Verificação de Fail-Open e Preservação Byte-a-Byte de Kwargs

O script `scripts/verify_fail_open.py` testa formalmente os cenários de falha do decider e valida a identidade exata dos parâmetros de chamada originais:

```bash
cd /home/rafael/hermes/workspaces/jev-effort-router
uv run python scripts/verify_fail_open.py
```

Saída:
```text
=== VERIFYING BYTE-IDENTICAL KWARGS ON FAIL-OPEN ===

--- Case 1: Missing API key ---
Router returned: None
VERIFIED: res is None -> original kwargs unchanged (byte-identical)

--- Case 2: Invalid decider base_url ---
Router returned: None
VERIFIED: res is None -> original kwargs unchanged (byte-identical)

--- Case 3: Middleware contract simulation ---
VERIFIED: outgoing payload is byte-identical to original_request

ALL FAIL-OPEN CHECKS PASSED!
```

Além disso, o registro de `upstream_error` em `routes.jsonl` comprova o registro de auditoria quando um erro ocorre:
```json
{
  "ts": "2026-10-15T20:08:57+00:00",
  "event": "skip",
  "reason": "upstream_error",
  "provider": "opencode-go",
  "configured_model": "deepseek-v4.1-flash",
  "turn_id": "20261015_170856_59a27e:d4032773-12a4-410d-a0b2-a627bab8542e:b18d3586",
  "session_id": "20261015_170856_59a27e",
  "platform": "cli"
}
```

---

## 5. Suíte de Testes e Opt-In Live Probe

### 5.1 Suíte Completa Offline
Por padrão, sem `JEV_LIVE_PROBE=1`, nenhuma chamada de rede é realizada:

```bash
cd /home/rafael/hermes/workspaces/jev-effort-router
env -u JEV_LIVE_PROBE -u OPENCODE_GO_API_KEY uv run --with pytest --with pyyaml pytest -q tests -o addopts= -rs
```

Resultado:
```text
........................................................................ [ 38%]
....................................................sssss..s............ [ 76%]
............................................                             [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_live_probe.py:40: Live probe opt-in requires JEV_LIVE_PROBE=1 and OPENCODE_GO_API_KEY
SKIPPED [4] tests/test_live_probe.py:71: Live probe opt-in requires JEV_LIVE_PROBE=1 and OPENCODE_GO_API_KEY
SKIPPED [1] tests/test_registration.py:100: could not import 'hermes_cli.plugins_manifest': No module named 'hermes_cli'
182 passed, 6 skipped in 1.04s
```

### 5.2 Execução Opt-in do Live Probe (`JEV_LIVE_PROBE=1`)
Comando reproduzível:
```bash
cd /home/rafael/hermes/workspaces/jev-effort-router
env JEV_LIVE_PROBE=1 OPENCODE_GO_API_KEY="[REDACTED]" uv run --with pytest --with pyyaml pytest -v tests/test_live_probe.py -o addopts=
```

Resultado:
```text
============================= test session starts ==============================
tests/test_live_probe.py::test_live_probe_skipped_offline_even_if_key_in_env PASSED [ 16%]
tests/test_live_probe.py::test_live_opencode_decider_call PASSED          [ 33%]
tests/test_live_probe.py::test_live_opencode_reasoning_effort_support[None] PASSED [ 50%]
tests/test_live_probe.py::test_live_opencode_reasoning_effort_support[low] PASSED [ 66%]
tests/test_live_probe.py::test_live_opencode_reasoning_effort_support[medium] PASSED [ 83%]
tests/test_live_probe.py::test_live_opencode_reasoning_effort_support[high] PASSED [100%]

============================== 6 passed in 10.74s ==============================
```

### 5.3 Prova Empírica dos Níveis de Reasoning Effort do OpenCode
Execução de `scripts/probe_opencode_levels.py` com o endpoint oficial `https://opencode.ai/zen/go/v1` e modelo `deepseek-v4.1-flash`:

```bash
uv run python scripts/probe_opencode_levels.py
```

Resultado:
```text
=== OPENCODE REASONING_EFFORT LEVEL PROBE ===
level=None            -> HTTP 200 (ACCEPTED)
level='low'           -> HTTP 200 (ACCEPTED)
level='medium'        -> HTTP 200 (ACCEPTED)
level='high'          -> HTTP 200 (ACCEPTED)
level='xhigh'         -> HTTP 200 (ACCEPTED)
level='max'           -> HTTP 200 (ACCEPTED)
level='invalid_level' -> HTTP 422 (REJECTED): {"error":{"type":"server_error","message":"Upstream request failed: Endpoint is unavailable."}}
```

Isso sustenta rigorosamente a declaração de `docs/routing-grid.md:102-104` e a política de mapeamento seguro (`accepted: [low, medium, high]`).

---

## 6. Implementação OpenCode Console Jev & Catálogo de 2 Modelos (`t_6ec7a94d`)

### 6.1 Resumo Normativo e Decisões de Escopo
- **Diretiva do Dono & Status Operacional do MiMo**: O dono do produto definiu formalmente em comentário no card a escolha do ID padrão `mimo-v2.6-flash` (não-free), rejeitando estritamente a variante `mimo-v2.6-flash-free` e suas condições de retenção/treinamento de dados. Contudo, a consulta pública à API oficial (`GET https://opencode.ai/inference/v1/models`) confirma `mimo-v2.6-flash-free`, `jev-1.13-free`, `jev-1.13` e `deepseek-v4.1-flash`, mas não lista `mimo-v2.6-flash`. Em conformidade estrita com o gate, a variante free nunca é substituída. Estruturalmente, o código, catálogo e allowlist suportam `mimo-v2.6-flash` e `deepseek-v4.1-flash` conforme especificado; no entanto, a ausência de confirmação pública oficial para o ID não-free é expressamente registrada como bloqueio operacional, sem alegar dois modelos operacionais em produção live.
- **Isolamento Estrito**: O provedor `opencode-console` (e alias `opencode_console`) opera com grade própria (`OPENCODE_CONSOLE_GRID`) e allowlist restrita aos dois modelos autorizados (`deepseek-v4.1-flash`, `mimo-v2.6-flash`). Modelos fora da allowlist falham aberto.
- **Política Explícita de Reasoning por Modelo**: Distingue explicitamente (1) o nível de esforço lógico que o Jev decide/devolve (`effort_requested`) e (2) o parâmetro de reasoning no endpoint de geração (`effort`). Sem evidência ou documentação empírica confirmando suporte no endpoint de geração do `opencode-console`, o parâmetro de reasoning é estritamente omitido do wire para `deepseek-v4.1-flash` e `mimo-v2.6-flash`, sem inferir suporte a partir de OpenCode Go ou Ollama, e sem alegar que o modelo de geração downstream recebeu o reasoning escolhido.
- **Operação Estritamente Offline**: Nenhuma chamada paga ou de rede foi realizada; nenhum perfil real foi modificado (conforme o gate do card, instalação e chamadas de rede exigem autorização operacional separada).

### 6.2 Execução da Suíte de Testes Offline

Comando executado no worktree isolado (`.worktrees/t_6ec7a94d`):
```bash
uv run --extra dev pytest
```

Saída obtida (execução atual):
```text
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/rafael/hermes/workspaces/jev-effort-router/.worktrees/t_6ec7a94d
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 253 items

tests/test_audit.py ...........                                          [  4%]
tests/test_catalog.py ..................                                 [ 11%]
tests/test_catalog_status.py ....                                        [ 13%]
tests/test_client.py ................................................... [ 33%]
.                                                                        [ 33%]
tests/test_config_policy.py .............................                [ 45%]
tests/test_effort.py .................                                   [ 51%]
tests/test_grid.py ............                                          [ 56%]
tests/test_grid_coverage.py ....                                         [ 58%]
tests/test_live_probe.py .sssss                                          [ 60%]
tests/test_registration.py .ss..s.............                           [ 67%]
tests/test_router.py ................................................... [ 88%]
..............................                                           [100%]

======================== 245 passed, 8 skipped in 0.74s ========================
```

Todos os 245 testes offline passaram com sucesso (evolução sobre os 210 testes históricos da round 1). Os 8 skips consistem em: 5 testes de live probe (`test_live_probe.py`) que requerem conexão de rede externa/sockets, e 3 testes em `test_registration.py` condicionados à biblioteca opcional PyYAML (`yaml = pytest.importorskip("yaml")`).

### 6.3 Verificação de Cobertura nos Testes Adicionados
1. **`tests/test_client.py`**:
   - `test_jev_console_decider_happy_path`: valida wire SystemOne (`POST`, URL, Authorization header com `OPENCODE_CONSOLE_API_KEY`, payload com `state`/`questions`, decodificação de `answers`).
   - `test_jev_console_decider_fallback_to_paid_jev` / `test_jev_console_decider_fallback_model`: valida iteração do modelo primário (`jev-1.13-free`) para o secundário (`jev-1.13`) em caso de falha no primário.
   - `test_jev_console_decider_both_models_fail` / `test_jev_console_decider_network_failure_fail_open`: valida tratamento de erro 500/502 com fail-open sem exceção.
   - `test_jev_console_decider_timeout`: valida timeout de rede com marcação `REASON_TIMEOUT` e fail-open.
   - `test_jev_console_decider_http_error_matrix`: matriz com status 401, 403, 429, 500, 503 e verificação estrita de que segredos da API (`OPENCODE_CONSOLE_API_KEY`) nunca são expostos em logs/auditoria.
   - `test_jev_console_decider_real_non_json_body`: valida corpo HTML/texto não-JSON com `REASON_MALFORMED` e redaction de credenciais.
   - `test_jev_console_decider_response_defined_probability_domain`: valida que o domínio de probabilidades é estritamente restrito às opções enviadas na grade e rejeita chaves externas inventadas pela resposta com `REASON_MALFORMED`.
   - `test_jev_console_decider_invalid_types_and_float_choice`: valida rejeição de escolhas float, tipos inválidos e escolhas de esforço numéricas.
   - `test_jev_console_decider_low_confidence_degrades`: valida degradação graciosa (fail-open) em confiança abaixo do limiar.
2. **`tests/test_catalog.py`**:
   - `test_opencode_console_catalog_allowlist`: valida `is_known` True para `deepseek-v4.1-flash` e `mimo-v2.6-flash`, False para modelos fora da allowlist (`kimi-k3`, etc.) sob `opencode-console` e `opencode_console`.
3. **`tests/test_config_policy.py`**:
   - `test_console_config_options_parsed_and_queried`: valida parsing das configurações `console_endpoint`, `console_jev_model`, `console_jev_fallback_model`, `console_api_key_env`, e métodos `base_url_for`, `decider_model_for`, `api_key_env_for`.
   - `test_materialized_schema_defaults`: valida os defaults materializados diretamente de `plugin.yaml` (`console_jev_model` = `jev-1.13-free`, `console_jev_fallback_model` = `jev-1.13`, decider default `jev`, routable_providers default `[ollama-cloud]`).
4. **`tests/test_router.py`**:
   - `test_jev_console_decider_routing_end_to_end`: valida roteamento completo ponta a ponta com `decider: jev_console` e `provider: opencode-console`.
   - `test_cross_provider_memo_replay_isolation`: valida que transições de provider não reutilizam memo incompatível.
   - `test_cross_decider_memo_replay_isolation`: valida que transições de decider não reutilizam memo incompatível.
   - `test_memo_replay_invalidated_by_confidence_threshold_elevation`: valida que elevação de limiar de confiança no mesmo turno invalida o memo anterior e falha aberto sem chamada duplicada.
   - `test_memo_replay_invalidated_by_fallback_backend_or_key_env_change`: valida que alteração de backend de fallback ou de variável de chave invalida o replay de memo.
   - `test_middleware_rejects_invented_criteria_or_outsider_choice`: valida rejeição de choice com critérios inventados ou opção fora da grade enviada, mantendo request byte-idêntico.
   - `test_middleware_zero_effort_confidence_replay_policy_and_no_effort_response`: valida que effort_confidence 0.0 é distinguida de ausência de resposta no replay (invalidada quando limiar sobe), enquanto resposta sem esforço não sofre regressão.
   - `test_middleware_fail_open_console_negative_matrix`: matriz completa offline (HTTP 401/403/429/500/503, timeout, non-JSON real, envelope, confidence bool/str/NaN/Inf/range/threshold, probabilidades nulas/tipos/domínio/soma, choice fora da grade/critérios inventados, effort malformado/critérios remapeados) invocando `on_llm_request` diretamente com dois objetos de request independentes e distinguíveis (top-level e aninhados), comprovando retorno None (fail-open), preservação exata dos snapshots serializados de ambos os payloads (`working_req` e `original_req`), geração do registro de skip na trilha de auditoria real (`routes.jsonl`), e ausência da chave secreta tanto nos logs (`caplog.text`) quanto na auditoria, com exerção real de redaction (`[REDACTED]`) em corpos de erro e payloads não-JSON com segredo embutido.
   - `test_middleware_fail_open_both_models_fail`: valida fail-open quando ambos os modelos (primário e fallback) falham, invocando `on_llm_request` com objetos independentes e distinguíveis, comprovando preservação dos snapshots serializados de ambos, escrita de skip na auditoria, redaction efetiva do segredo no log (`[REDACTED]`) e ausência da chave secreta na auditoria e nos logs.
   - `test_opencode_console_distinguishes_logical_effort_from_wire`: valida que o Jev decide esforço lógico (`effort_requested='high'`), enquanto o wire payload omite `reasoning_effort` e o log de auditoria registra `effort=null`, sem alegar execução downstream.
5. **`tests/test_effort.py`**:
   - `test_opencode_console_explicit_reasoning_policies`: valida políticas explícitas (`ModelReasoningPolicy`) para `deepseek-v4.1-flash` e `mimo-v2.6-flash`, confirmando `omitted=True`, `wire_parameter=None` e decisões registradas.
   - `test_opencode_console_effort_cannot_be_inferred_from_other_providers`: comprova que suporte em `ollama-cloud` ou `opencode-go` não é inferido para `opencode-console`.
   - `test_opencode_console_custom_effort_map_overrides_omission`: valida que mapeamentos explícitos em `effort_maps` pelo operador têm precedência e são honrados sem escalada.

### 6.4 Matriz da Política de Reasoning por Modelo

| Provedor | Modelo | Esforço Lógico (Jev) | Parâmetro no Wire | Níveis Aceitos | Evidência / Decisão |
|---|---|---|---|---|---|
| `opencode-console` | `deepseek-v4.1-flash` | Sim (`low`/`medium`/`high`) | `None` (omitido) | N/A | **Não confirmado**: Sem evidência empírica ou contrato no endpoint de geração do console. Não se infere de `opencode-go` ou `ollama-cloud`. Omitido para prevenir HTTP 400. |
| `opencode-console` | `mimo-v2.6-flash` | Sim (`low`/`medium`/`high`) | `None` (omitido) | N/A | **Não confirmado**: Modelo multimodal/agentic não-free sem contrato de reasoning documentado. Omitido no wire. |
| `ollama-cloud` | `deepseek-v4.1-flash` | Sim (`low`/`medium`/`high`) | `reasoning_effort` | low, medium, high, max | **Verificado**: Contrato formal Ollama:cloud (`DEEPSEEK_V4_EFFORTS`). |
| `ollama-cloud` | Outros (Kimi, GLM, etc.) | Sim (`low`/`medium`/`high`) | `reasoning_effort` | Por família | **Verificado**: Mapeamento e clamping estrito sem escalada. |
