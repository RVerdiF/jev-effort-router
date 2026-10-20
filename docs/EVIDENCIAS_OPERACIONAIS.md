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

Isso sustenta rigorosamente a declaração da seção "Effort-to-wire mapping (Ollama:Cloud built-in)" de `docs/routing-grid.md` e a política de mapeamento seguro (`accepted: [low, medium, high]`). **Atenção:** o conjunto aceito citado aqui ficou superado pelo §8 (`t_a0a4c186`) para a rota `opencode-go`, que passa a declarar `low`/`medium`/`high`/`max` com `xhigh`→`max`, o mesmo contrato que o core aplica nesse wire; o registro de `invalid_level` também não é estável entre execuções (ver §8.1).

---

## 6. Implementação OpenCode Console Jev & Catálogo de 2 Modelos (`t_6ec7a94d`)

> **Superado pela seção 7 (`t_14209379`)**: o catálogo Go passou a ter **um único modelo**, o MiMo foi removido de todas as grades/allowlists e a rota `commandcode` foi adicionada. As afirmações de 6.1–6.4 registram o estado entregue no card `t_6ec7a94d` e ficam mantidas como histórico; a verdade corrente está na seção 7. A política de reasoning do Go registrada em 6.1/6.4 também foi superada, agora pela **seção 8** (`t_a0a4c186`).

### 6.1 Resumo Normativo e Decisões de Escopo
- **Diretiva do Dono & Separação de Rotas (Console Decider / Go Geração)**: O dono do produto definiu formalmente a escolha do ID padrão `mimo-v2.6-flash` (não-free) na geração, rejeitando estritamente a variante `mimo-v2.6-flash-free` e suas condições de retenção/treinamento de dados. Conforme orientado pelo dono (Comentário 108), as fontes oficiais distinguem claramente os produtos:
  - **Decider Jev Console**: Opera via SystemOne (`https://opencode.ai/inference/systemone/v1/systemone`) com modelo primário gratuito `jev-1.13-free` e fallback automático para o Jev pago (`jev-1.13`).
  - **Geração OpenCode Go**: A geração dos modelos `deepseek-v4.1-flash` e `mimo-v2.6-flash` é atendida pelo provider `opencode-go`, onde a documentação oficial do OpenCode Go confirma explicitamente MiMo-V2.6-Flash normal e DeepSeek V4.1 Flash com tarifação por token.
  - **Isolamento e Segurança**: O MiMo normal não é enviado ao endpoint Console, e a variante `mimo-v2.6-flash-free` nunca é substituída. Ambas as rotas permanecem estritamente isoladas e verificadas offline sem chamadas pagas nem inferência live.
- **Fontes Oficiais e Mapping dos IDs Go**: Os IDs de geração (`deepseek-v4.1-flash`, `mimo-v2.6-flash`) vêm da lista oficial de modelos do OpenCode Go (`https://opencode.ai/v2/docs/console/go`, que nomeia MiMo-V2.6-Flash normal e DeepSeek V4.1 Flash com preço por token) e são distintos do id do decider SystemOne (`jev-1.13-free` / `jev-1.13`). A listagem pública `https://opencode.ai/inference/v1/models` é a do Console e não serve para confirmar os modelos Go. Nenhuma credencial foi lida para estabelecer esse mapping e nenhuma inferência paga foi executada; a verificação da documentação oficial foi registrada na revisão independente que pediu esta separação.
- **Catálogo Console (um modelo)**: `opencode-console` (e alias `opencode_console`) é regido por `OPENCODE_CONSOLE_MODELS = ("deepseek-v4.1-flash",)`. O MiMo normal **não** pertence a esse catálogo: o Console não o serve, e uma decisão que o nomeie nessa rota falha aberto, mantendo o modelo configurado pelo operador.
- **Catálogo Go de dois modelos (opt-in)**: `OPENCODE_GO_MODELS = ("deepseek-v4.1-flash", "mimo-v2.6-flash")` é aplicado a `opencode-go`/`opencode_go` **somente enquanto `decider: jev_console` está ativo**. O limite é um teto: entradas de `grids.opencode-go` fora do catálogo são descartadas, grade que fica vazia cai no catálogo embutido de dois modelos, e grade de política de superfície (`platform_overrides.<surface>.grid`) passa pelo mesmo corte. Deciders legados (`jev`, `openai_compatible`) preservam a grade Go que o operador declarou, sem alteração.
- **Onde o teto é aplicado**: `config.route_allowlist(provider, decider)` é a única fonte de verdade; `Settings.grid_for` / `EffectiveSettings` cortam a grade (choice, alternativas, fallback e replay leem dessa grade), `ModelCatalog.is_known(..., decider=...)` responde `False` para modelo fora do catálogo da rota, e `JevConsoleDecider._interpret` rejeita a escolha fora da allowlist antes de qualquer aplicação.
- **Política Explícita de Reasoning por Modelo**: Distingue explicitamente (1) o nível de esforço lógico que o Jev decide/devolve (`effort_requested`) e (2) o parâmetro de reasoning no endpoint de geração (`effort`). Sem contrato confirmado por modelo no endpoint de geração de cada rota, o parâmetro é estritamente omitido do wire nas duas rotas opt-in (`deepseek-v4.1-flash` no Console; `deepseek-v4.1-flash` e `mimo-v2.6-flash` no Go), sem inferir suporte de Ollama ou de outra rota e sem alegar que o modelo downstream recebeu o reasoning escolhido.
- **Operação Estritamente Offline**: Nenhuma chamada paga ou de rede foi realizada; nenhum perfil real foi modificado (conforme o gate do card, instalação e chamadas de rede exigem autorização operacional separada).

### 6.2 Execução da Suíte de Testes Offline

Comando executado no worktree isolado (`.worktrees/t_6ec7a94d`):
```bash
uv run --extra dev pytest
```

Saída obtida (execução atual, 2026-10-17):
```text
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/rafael/hermes/workspaces/jev-effort-router/.worktrees/t_6ec7a94d
configfile: pyproject.toml
plugins: anyio-4.15.1
collected 263 items

tests/test_audit.py ...........                                          [  4%]
tests/test_catalog.py ...................                                [ 11%]
tests/test_catalog_status.py ....                                        [ 12%]
tests/test_client.py ................................................... [ 32%]
.                                                                        [ 32%]
tests/test_config_policy.py ................................             [ 44%]
tests/test_effort.py ...................                                 [ 52%]
tests/test_grid.py ............                                          [ 56%]
tests/test_grid_coverage.py ....                                         [ 58%]
tests/test_live_probe.py .sssss                                          [ 60%]
tests/test_registration.py .ss..s.............                           [ 67%]
tests/test_router.py ................................................... [ 87%]
..................................                                       [100%]

======================== 255 passed, 8 skipped in 0.61s ========================
```

Todos os 255 testes offline passaram (evolução sobre os 246 da entrega anterior: 9 testes novos, nenhum removido sem substituto). Os 8 skips são exatamente: 5 testes de live probe (`test_live_probe.py`) que exigem `JEV_LIVE_PROBE=1` e chave real, e 3 testes em `test_registration.py` condicionados à biblioteca opcional PyYAML (`yaml = pytest.importorskip("yaml")`). Nenhum skip é justificado por socket bloqueado; a suíte inteira também roda com `socket.socket.connect` e `socket.create_connection` bloqueados.

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
   - `test_opencode_console_catalog_allowlist`: valida `is_known` True para `deepseek-v4.1-flash`, False para `mimo-v2.6-flash`, `mimo-v2.6-flash-free` e `kimi-k3` sob `opencode-console` e `opencode_console`.
   - `test_opencode_go_catalog_is_capped_only_for_the_console_decider`: valida que `is_known` para `opencode-go`/`opencode_go` responde True aos dois modelos autorizados, False ao MiMo free e a `kimi-k3` quando `decider="jev_console"`, e `None` (sem veto) sob deciders legados.
3. **`tests/test_config_policy.py`**:
   - `test_console_config_options_parsed_and_queried`: valida parsing das configurações `console_endpoint`, `console_jev_model`, `console_jev_fallback_model`, `console_api_key_env`, e métodos `base_url_for`, `decider_model_for`, `api_key_env_for`.
   - `test_opencode_console_grid_resolution`: grade efetiva do Console contém apenas `deepseek-v4.1-flash`.
   - `test_opencode_go_grid_is_the_two_model_catalog_under_the_console_decider`: grade efetiva de `opencode-go`/`opencode_go` é exatamente os dois modelos autorizados, sem herdar nenhum modelo Ollama.
   - `test_opencode_go_grid_cap_applies_to_operator_grids_without_widening_them`: `grids.opencode-go` com `unauthorized-model` é cortada; grade só com modelos fora do catálogo cai no catálogo embutido; grade de política de superfície também é cortada.
   - `test_opencode_go_grid_untouched_for_legacy_deciders`: decider legado mantém a grade Go configurada (e a grade global quando não há grade).
   - `test_materialized_schema_defaults`: valida os defaults materializados diretamente de `plugin.yaml` (`console_jev_model` = `jev-1.13-free`, `console_jev_fallback_model` = `jev-1.13`, decider default `jev`, routable_providers default `[ollama-cloud]`).
4. **`tests/test_router.py`**:
   - `test_jev_console_decider_routing_end_to_end`: valida roteamento completo ponta a ponta com `decider: jev_console` e `provider: opencode-console`.
   - `test_jev_console_decider_generation_route_go_uses_the_two_model_catalog`: usa a configuração efetiva (sem grade injetada) — `{decider: jev_console, routable_providers: [opencode-go]}` — e comprova: grade = dois modelos autorizados, `default_model = deepseek-v4.1-flash`, escolha 2 aplica `mimo-v2.6-flash`, chamada do decider vai para o SystemOne (nunca para o `base_url` Go), `reasoning_effort` ausente do wire, `effort_requested='high'` + `effort=None` na auditoria, alternativas restritas a `deepseek-v4.1-flash`, snapshots serializados de `request` e `original_request` idênticos (objetos independentes, um deles com `reasoning_config`), e replay no mesmo turno sem segunda chamada.
   - `test_opencode_go_route_cap_rejects_models_outside_the_two_model_catalog`: grade customizada com `unauthorized-model` é cortada antes da pergunta; resposta nomeando o modelo inventado é rejeitada nos dois Jev (free e pago) e o payload original permanece byte-idêntico (skip registrado).
   - `test_console_route_no_longer_applies_normal_mimo`: no Console, resposta apontando a posição 2 (MiMo normal) falha aberto, com a grade efetiva contendo só `deepseek-v4.1-flash`.
   - `test_opencode_go_legacy_decider_keeps_its_configured_grid`: com decider legado, a grade Go configurada é preservada.
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
   - `test_opencode_console_explicit_reasoning_policies`: valida a política explícita (`ModelReasoningPolicy`) do `deepseek-v4.1-flash` no Console, com `omitted=True`, `wire_parameter=None` e decisão registrada.
   - `test_opencode_go_explicit_reasoning_policies`: (**superado pelo §8**) valida a política explícita dos dois modelos Go (`omitted=True`, `wire_parameter=None`) e a omissão de um modelo fora do catálogo Go.
   - `test_opencode_go_effort_cannot_be_inferred_from_other_providers`: (**superado pelo §8**) comprova que o mesmo modelo honra o nível em `ollama-cloud` e continua omitido em `opencode-go`.
   - `test_opencode_console_effort_cannot_be_inferred_from_other_providers`: comprova que suporte em `ollama-cloud` ou `opencode-go` não é inferido para `opencode-console`.
   - `test_opencode_console_custom_effort_map_overrides_omission`: valida que mapeamentos explícitos em `effort_maps` pelo operador têm precedência e são honrados sem escalada.

### 6.4 Matriz da Política de Reasoning por Modelo

| Provedor | Modelo | Esforço Lógico (Jev) | Parâmetro no Wire | Níveis Aceitos | Evidência / Decisão |
|---|---|---|---|---|---|
| `opencode-console` | `deepseek-v4.1-flash` | Sim (`low`/`medium`/`high`) | `None` (omitido) | N/A | **Não confirmado**: sem evidência empírica ou contrato no endpoint de geração do console. Não se infere de `opencode-go` ou `ollama-cloud`. Omitido para prevenir HTTP 400. |
| `opencode-go` | `deepseek-v4.1-flash` | Sim (`low`/`medium`/`high`) | `reasoning_effort` | low, medium, high, max (`xhigh`→`max`) | **Superado pelo §8** (card `t_a0a4c186`): o probe direto de 2026-10-20 confirmou o parâmetro no endpoint Go; o nível decidido vai no wire no vocabulário do core e qualquer valor fora do conjunto declarado (`minimal`, `none`, desconhecido) é omitido. |
| `opencode-go` | `mimo-v2.6-flash` | Sim (`low`/`medium`/`high`) | `None` (omitido) | N/A | **Não confirmado**: modelo multimodal/agentic normal, sem knob de reasoning documentado. Omitido no wire. |
| `ollama-cloud` | `deepseek-v4.1-flash` | Sim (`low`/`medium`/`high`) | `reasoning_effort` | low, medium, high, max | **Verificado**: Contrato formal Ollama:cloud (`DEEPSEEK_V4_EFFORTS`). |
| `ollama-cloud` | Outros (Kimi, GLM, etc.) | Sim (`low`/`medium`/`high`) | `reasoning_effort` | Por família | **Verificado**: Mapeamento e clamping estrito sem escalada. |

---

## 7. Rota `commandcode` e catálogos de geração de um modelo (`t_14209379`)

### 7.1 Escopo entregue
- Rota `commandcode` registrada como provedor opt-in de geração, com catálogo de **um** modelo namespaced.
- `mimo-v2.6-flash` removido de todas as grades, allowlists e catálogos: `OPENCODE_GO_MODELS = ("deepseek-v4.1-flash",)`. Nenhum catálogo de geração cita MiMo.
- Efeito pretendido: com um modelo por rota a escolha de modelo é degenerada; o decider passa a decidir, na prática, apenas o nível de raciocínio.

### 7.2 Fonte do id do modelo `commandcode` (sem ler credencial)
- Endpoint público e **não autenticado** `GET https://api.commandcode.ai/provider/v1/models` (o próprio profile do provider o declara como público): resposta `HTTP 200`, 18.707 bytes, sem header `Authorization`; a lista contém `"id":"deepseek/deepseek-v4.1-flash"` e também `"deepseek/deepseek-v4.1-flash-fast"`.
- Profile do provider (`/home/rafael/hermes/core/plugins/model-providers/commandcode/__init__.py`): `api_mode="chat_completions"`, `base_url="https://api.commandcode.ai/provider/v1"`, aliases `("commandcode-chat",)`, env `COMMANDCODE_API_KEY`.
- **Id literal usado**: `deepseek/deepseek-v4.1-flash`. É namespaced de propósito: o id bare `deepseek-v4.1-flash` das rotas OpenCode é outro contrato e não é aceito nesta rota. A variante `-fast` não foi autorizada e não entra na allowlist.

### 7.3 Catálogos vigentes
- `OPENCODE_CONSOLE_MODELS = ("deepseek-v4.1-flash",)` (inalterado; rota sempre pinada).
- `OPENCODE_GO_MODELS = ("deepseek-v4.1-flash",)` (MiMo removido).
- `COMMANDCODE_MODELS = ("deepseek/deepseek-v4.1-flash",)` (novo; opt-in sob `decider: jev_console`, como o Go).
- Com deciders legados (`jev`, `openai_compatible`) nenhuma das rotas opt-in é capada: a grade declarada pelo operador continua valendo, byte a byte como antes.

### 7.4 Mecanismo e correção de assimetria
- O teto usa o mecanismo existente: `config.route_allowlist(provider, decider)` é a fonte única, `route_catalog` devolve `COMMANDCODE_GRID`/`OPENCODE_GO_GRID`, e `Settings.grid_for`/`EffectiveSettings` cortam choice, alternativas, fallback e replay. `ModelCatalog.is_known(..., decider=...)` responde `False` para modelo fora da allowlist e `JevConsoleDecider._interpret` rejeita a escolha fora dela antes de qualquer aplicação.
- `_GRID_ALIASES` passou a listar `commandcode` e agora também alimenta `is_provider_routable` por uma tabela única (`_provider_group`), corrigindo a assimetria anterior: `opencode-go` não casava com `opencode_go` e a rota era pulada em silêncio. `commandcode`, `commandcode-chat` e `commandcode_chat` normalizam entre si.

### 7.5 Política explícita de reasoning por rota/modelo
| Provedor | Modelo | Parâmetro no wire | Evidência / Decisão |
|---|---|---|---|
| `opencode-console` | `deepseek-v4.1-flash` | `None` (omitido) | Inalterado: contrato não confirmado no endpoint de geração do Console. |
| `opencode-go` | `deepseek-v4.1-flash` | `reasoning_effort` (`low`/`medium`/`high`/`max`, `xhigh`→`max`) | **Superado pelo §8** (card `t_a0a4c186`): o probe direto de 2026-10-20 confirmou o parâmetro no endpoint Go. Vigente: o nível decidido vai no wire no vocabulário do core; qualquer valor fora do conjunto declarado é omitido. |
| `commandcode` | `deepseek/deepseek-v4.1-flash` | `None` (o plugin omitia o seu) | **Superado pelo §9** (card `t_1906910b`): o probe direto de 2026-10-20 confirmou `reasoning_effort` no endpoint CommandCode, e o plugin passa a enviar o nível decidido no campo top-level, ao lado do `extra_body.thinking` do profile. Histórico desta rodada: **contrato de dois componentes, rastreado offline** (§7.5.1), com omissão deliberada do campo do plugin e `effort=null` no wire. |

#### 7.5.1 Rastreio do contrato `commandcode` (correção da revisão, rodada 1)

A revisão apontou que a justificativa anterior — "`reasoning_effort` não faz parte do contrato" — era falsa. Rastreio das fontes, sem importar o core, sem rede e sem ler credencial:

1. `core/plugins/model-providers/commandcode/__init__.py:42-59` — `build_api_kwargs_extras` reconhece ids `deepseek/*` e delega ao profile `deepseek` via registry, repassando `reasoning_config` e o id **sem** o prefixo `deepseek/`.
2. `core/plugins/model-providers/deepseek/__init__.py:28-43` — para ids `deepseek-v4*` chama `thinking_toggle_extras(reasoning_config, DEEPSEEK_V4_EFFORTS, DEEPSEEK_V4_OVERRIDES, always_emit_toggle=True)`.
3. `core/agent/reasoning_effort.py:197-219` — no caminho de nível aceito, a linha 218 devolve **dois** valores: `({"thinking": {"type": "enabled"}}, {"reasoning_effort": <nível>})`.
4. `core/agent/reasoning_effort.py:81-83` — vocabulário V4: `low`/`medium`/`high`/`max`, com `xhigh → max`.
5. `core/agent/transports/chat_completions.py:562-583` — os extras do profile entram em `api_kwargs` (linha 568) **antes** de `request_overrides` (linhas 579-583); um `reasoning_effort` deixado no payload roteado sobrescreveria a derivação do profile.

Execução offline das funções **reais** dessa fonte (AST: apenas os nós de `thinking_toggle_extras`, `clamp_effort`, `requested_effort` e as constantes; nenhum `import agent`, nenhuma chave), com os mesmos argumentos que o profile DeepSeek usa:

```text
$ uv run --offline python <scratch>/t_14209379_contract.py
DEEPSEEK_V4_EFFORTS=('low', 'medium', 'high', 'max')  DEEPSEEK_V4_OVERRIDES={'xhigh': 'max'}
  reasoning_config={'effort': 'high'} -> ({'thinking': {'type': 'enabled'}}, {'reasoning_effort': 'high'})
  reasoning_config={'effort': 'xhigh'} -> ({'thinking': {'type': 'enabled'}}, {'reasoning_effort': 'max'})
  reasoning_config={'effort': 'none'} -> ({'thinking': {'type': 'enabled'}}, {})
  reasoning_config={} -> ({'thinking': {'type': 'enabled'}}, {})
  reasoning_config={'enabled': False} -> ({'thinking': {'type': 'disabled'}}, {})
```

Decisão registrada em `COMMANDCODE_REASONING_POLICIES` (`effort.py`): os dois componentes pertencem ao profile; o plugin omite o seu próprio campo e remove um `reasoning_effort` top-level preexistente para não sobrepor a derivação do profile. A incerteza declarada é estritamente sobre o **endpoint CommandCode graduar o nível escolhido pelo Jev** — o profile prova só que o Hermes envia o campo. Nenhum probe pago foi executado.

> **Superado pelo §9 (`t_1906910b`)**: o probe direto mostrou que o endpoint aceita `reasoning_effort`, então o plugin passou a ser o autor do campo **top-level** com o nível do Jev, mantendo o `thinking` como propriedade do profile. O rastreio acima continua correto quanto à ordem do transporte (profile → middleware) e é o que sustenta a precedência documentada no §9.2.

Regressão offline: `test_commandcode_keeps_the_profile_native_controls_and_drops_a_pre_existing_effort` (`tests/test_router.py`) parte de um payload no formato real do profile (`extra_body.thinking` + `reasoning_effort`) e comprova a remoção deliberada, `extra_body.thinking` e `reasoning_config` intactos, `effort_requested='high'` com `effort=None`, snapshots byte-idênticos e replay estável sem segunda chamada ao decider.

### 7.6 Evidência de execução offline
Suíte canônica, no worktree `.worktrees/t_14209379`, sem grade artificial (configuração efetiva):
```text
$ uv sync --offline --extra dev      # pytest 9.1.1 + deps do cache local
$ uv run --offline --extra dev pytest
collected 272 items
...
======================= 264 passed, 8 skipped in 0.74s ========================
```
Mesma suíte com `socket.socket.connect`, `socket.socket.connect_ex` e `socket.create_connection` bloqueados (interpreter da venv + patch, sem rede):
```text
======================= 264 passed, 8 skipped in 0.71s ========================
```
Os 8 skips são os mesmos de sempre: 5 do live probe (exigem `JEV_LIVE_PROBE=1` e chave real) e 3 de `test_registration.py` condicionados a PyYAML. Nenhum skip decorre de socket bloqueado.

Testes novos/ajustados que sustentam os critérios:
- `test_commandcode_grid_is_the_single_namespaced_catalog_under_the_console_decider`, `test_commandcode_grid_cap_applies_to_operator_grids_without_widening_them`, `test_commandcode_grid_untouched_for_legacy_deciders` (`tests/test_config_policy.py`).
- `test_is_provider_routable_normalises_every_route_alias` — cobre a assimetria `opencode-go`/`opencode_go` e os aliases de `commandcode`.
- `test_commandcode_catalog_is_capped_only_for_the_console_decider` e o Go atualizado para um modelo (`tests/test_catalog.py`).
- `test_commandcode_explicit_reasoning_policy` (`tests/test_effort.py`) — agora exige os **dois** componentes do contrato no texto da política (`extra_body.thinking` + `reasoning_effort`), a remoção deliberada e a incerteza do endpoint, e rejeita a formulação antiga (campo ausente do contrato).
- `test_jev_console_decider_generation_route_commandcode_uses_the_namespaced_catalog` e `test_commandcode_route_cap_rejects_models_outside_the_catalog` (`tests/test_router.py`): grade efetiva de um modelo, pergunta com um único critério, decider no SystemOne (nunca no `base_url` de geração), `reasoning_effort` ausente, `effort_requested='high'` + `effort=None`, snapshots serializados de `request`/`original_request` byte-idênticos (sem mutação in-place) e replay no mesmo turno sem segunda chamada; no caminho de rejeição, dois Jev (free e pago) recusam a escolha inventada e o payload permanece intacto.
- `test_commandcode_keeps_the_profile_native_controls_and_drops_a_pre_existing_effort` (`tests/test_router.py`) — regressão da rodada de revisão: parte do formato real do profile (`extra_body.thinking` + `reasoning_effort` top-level), comprova a remoção deliberada do campo preexistente, `extra_body.thinking`/`reasoning_config` intactos, snapshots byte-idênticos e replay estável.
- Go atualizado para o catálogo de um modelo: `test_jev_console_decider_generation_route_go_uses_the_single_model_catalog` e `test_opencode_go_route_cap_rejects_models_outside_the_catalog`.

Fail-open continua byte-idêntico também fora do pytest: `scripts/verify_fail_open.py` executado com os mesmos sockets bloqueados imprime `ALL FAIL-OPEN CHECKS PASSED!` (3 casos, `res is None`, payload serializado idêntico ao `original_request`).

Nenhuma alteração no core Hermes, nenhum PR upstream, nenhuma inferência paga. A única chamada de rede desta entrega foi o `GET` público e não autenticado de `/models`, usado apenas para confirmar o id.

---

## 8. `reasoning_effort` aplicado no wire do `opencode-go` (`t_a0a4c186`)

Esta seção **supera** a política do Go registrada em 6.1/6.4 e em 7.5: o `opencode-go` deixa de omitir o campo e passa a enviar o nível decidido pelo Jev. Console continua omitindo; `commandcode` também omitia nesta rodada e passa a enviar no §9 (`t_1906910b`), na sua própria evidência.

### 8.1 Probe que sustenta a mudança (2026-10-20, reexecutado nesta rodada)

`POST https://opencode.ai/zen/go/v1/chat/completions` (header `x-opencode-session` obrigatório), modelo `deepseek-v4.1-flash`. Duas execuções nesta rodada: `scripts/probe_opencode_levels.py` (só status) e uma variante que também mede `usage.completion_tokens_details.reasoning_tokens` com `max_tokens=1024`.

| Campo enviado | Resultado |
|---|---|
| nenhum | HTTP 200 |
| `reasoning_effort=low` | HTTP 200 |
| `reasoning_effort=medium` | HTTP 200 |
| `reasoning_effort=high` | HTTP 200 |
| `reasoning_effort=xhigh` | HTTP 200 |
| `reasoning_effort=max` | HTTP 200 |
| `reasoning_effort=invalid_level` | HTTP 200 |
| `reasoning={"effort":"high"}` | HTTP 200 |
| `thinking=high` | HTTP 400 (`thinking must be an object ...`) |

As respostas aceitas trouxeram `reasoning_content` e `usage.completion_tokens_details.reasoning_tokens` (25–61 tokens medidos), ou seja, o modelo emite reasoning nesse endpoint. Duas observações delimitam o que o probe prova:

- **o endpoint não valida o valor:** `invalid_level` também retornou HTTP 200. O §5.3, em outra execução, registrou 422 para esse mesmo caso, mas com a mensagem `Upstream request failed: Endpoint is unavailable` — erro de upstream, não de validação. Logo o conjunto aceito é uma decisão nossa (a do core), não uma fronteira de 400.
- **200 não gradua nada:** com uma amostra por nível, `reasoning_tokens` não escalou de forma monotônica (low 61, medium 36, high 25, max 50, xhigh 60, sem campo 60).

### 8.2 Política embutida (`effort.py`)

`OPENCODE_GO_REASONING_POLICIES["deepseek-v4.1-flash"]`: `wire_parameter="reasoning_effort"`, `accepted_levels=("low","medium","high","max")`, `overrides={"xhigh":"max"}`, `omitted=False`, com `evidence` citando endpoint, data, os HTTP 200 de `low`/`medium`/`high`/`xhigh`/`max`, os campos de reasoning da resposta, o HTTP 400 de `thinking` e a referência ao contrato do core. A `decision` registra que o nível declarado vai no wire, que `xhigh` é mapeado para `max` pela mesma override declarada do core, que nada fora do conjunto declarado é enviado, e que o 200 prova aceitação do parâmetro — não profundidade calibrada.

O vocabulário não foi escolhido por probe isolado: é o contrato que o próprio core aplica nesse mesmo wire. `core/agent/reasoning_effort.py` declara `DEEPSEEK_V4_EFFORTS = ("low","medium","high","max")` e `DEEPSEEK_V4_OVERRIDES = {"xhigh": "max"}`; `core/plugins/model-providers/opencode-zen/__init__.py` reconhece `deepseek-v4.1-flash` como modelo de thinking (`_is_deepseek_thinking_model`: `deepseek-v*` que não seja `deepseek-v3*`) e chama `thinking_toggle_extras(reasoning_config, DEEPSEEK_V4_EFFORTS, DEEPSEEK_V4_OVERRIDES)`, que emite o `reasoning_effort` **top-level** com o nível clampado a esse vocabulário. O plugin passa a declarar exatamente o mesmo conjunto, em vez de uma versão mais estreita.

### 8.3 Regra do resolvedor (`resolve_effort`)

Ordem efetiva: (1) `effort_maps` do operador tem precedência e mantém o clamp existente para o nível mais fraco; (2) rotas de política embutida (`opencode-console`/`opencode_console`, `opencode-go`/`opencode_go`, `commandcode`/`commandcode-chat`/`commandcode_chat`) enviam o nível **somente** se ele for membro de `accepted_levels`, ou se uma `overrides` declarada na própria política o mapear para um nível membro — qualquer outro valor é omitido; (3) `ollama-cloud` segue intocado no `family_for` + `clamp` (Kimi `medium`→`high` continua valendo). O ramo (2) existe porque o fallback de família responderia um nível que a rota nunca declarou (`minimal` desce para `low`, `ultra` sobe para `max`), e a override só vale quando o alvo é um nível declarado: `{"xhigh": "max"}` vale, uma override para `ultra` seria ignorada.

### 8.4 Verificação offline do resolvedor e da config do perfil

```
$ uv run --offline --with pyyaml python <scratch>/t_a0a4c186_verify_r2.py
== built-in policy matrix (worktree effort.py) ==
opencode-go       deepseek-v4.1-flash   wire='reasoning_effort' accepted=('low', 'medium', 'high', 'max') omitted=False overrides={'xhigh': 'max'}
                  resolve -> {'low': 'low', 'medium': 'medium', 'high': 'high', 'max': 'max', 'xhigh': 'max', 'minimal': None, 'none': None, 'bogus': None, 'ultra': None}
opencode_go       deepseek-v4.1-flash   wire='reasoning_effort' accepted=('low', 'medium', 'high', 'max') omitted=False overrides={'xhigh': 'max'}
                  resolve -> {'low': 'low', 'medium': 'medium', 'high': 'high', 'max': 'max', 'xhigh': 'max', 'minimal': None, 'none': None, 'bogus': None, 'ultra': None}
opencode-console  deepseek-v4.1-flash   wire=None               accepted=() omitted=True overrides={}
                  resolve -> todos None
commandcode       deepseek/deepseek-v4.1-flash wire=None         accepted=() omitted=True overrides={}
                  resolve -> todos None
opencode-go       kimi-k3               wire=None               accepted=() omitted=True overrides={}
                  resolve -> todos None

== live profile config: effort_maps is parsed and honoured ==
raw effort_maps from config.yaml: {'opencode-go': {'deepseek-v4.1-flash': {'accepted': ['low', 'medium', 'high', 'max']}}}
  config path resolve_effort('low') -> 'low'
  config path resolve_effort('medium') -> 'medium'
  config path resolve_effort('high') -> 'high'
  config path resolve_effort('max') -> 'max'
  config path resolve_effort('xhigh') -> 'high'
  config path resolve_effort('minimal') -> None
  config path resolve_effort('none') -> None
  config path resolve_effort('bogus') -> None
  config path resolve_effort('ultra') -> 'max'
```

`effort_maps.opencode-go.deepseek-v4.1-flash.accepted` em `~/.hermes/profiles/code/config.yaml` passou de `[low, medium, high]` para `[low, medium, high, max]` (uma linha; permissões 600 preservadas; backup em `<scratch>/settings-backup-r2.txt`). A entrada tem duas funções: faz o plugin **hoje instalado** — que só conhece o caminho de `effort_maps` — continuar aplicando o nível, e mantém o perfil alinhado ao conjunto declarado. Depois de merge + reinstall ela é redundante para `low`/`medium`/`high`/`max`, que a política embutida já envia verbatim.

Divergências conhecidas entre os dois caminhos, ambas **inalcançáveis** pelo decider atual (`EFFORT_LEVELS = ("low","medium","high")` em `config.py` valida criteria, `default_effort` e `max_effort`), portanto sem efeito em turno real:

- `xhigh`: a política embutida mapeia para `max` (override declarada, igual ao core); o caminho `effort_maps` mantém a regra anti-escalada do caminho custom e desce para `high`.
- `ultra`: a política embutida omite; o caminho `effort_maps` desce para `max` (mesma regra: nunca sobe, mas o nível mais forte aceito é `max`).

### 8.5 Testes

Novos/ajustados:

- `test_opencode_go_explicit_reasoning_policies` (`tests/test_effort.py`) — política `omitted=False`, `wire_parameter='reasoning_effort'`, `accepted_levels=('low','medium','high','max')`, `overrides={'xhigh':'max'}`, marcadores do probe e da referência ao core na `evidence`, `low`/`medium`/`high`/`max` resolvidos verbatim nos dois aliases (`opencode-go`/`opencode_go`), `xhigh` → `max`, `minimal`/`none`/`bogus`/`ultra`/vazio → `None`, e modelo fora do catálogo Go continua omitido.
- `test_declared_override_cannot_invent_a_level_outside_the_policy` — a override só vale quando o alvo é um nível declarado: com `accepted=('low','medium')` e `overrides={'xhigh':'ultra','high':'medium'}`, `xhigh` → `None` (nada inventado) e `high` → `medium`.
- `test_operator_effort_map_takes_precedence_over_the_go_builtin_policy` — map do operador vence a política embutida e mantém o clamp (`high` → `low` com `accepted: [low]`).
- `test_unmapped_provider_defaults_to_omitting_effort` — substitui o teste antigo que afirmava omissão no Go; usa `openrouter` como provider sem política.
- `test_jev_console_decider_generation_route_go_uses_the_single_model_catalog` (`tests/test_router.py`) — exige `request["reasoning_effort"] == "high"`, `record["effort"] == "high"`, `effort_requested == "high"` e `fallback_reasons == []`, preservando uma única chamada ao SystemOne, ausência de mutação in-place e replay sem segunda chamada.
- `test_opencode_go_route_lands_every_decidable_level_on_the_wire` — parametrizado `low`/`medium`/`high` (os níveis que o decider pode produzir) com `decider: jev_console` + `routable_providers: [opencode-go]`: o campo vai no request e o audit registra o mesmo nível (nunca `null`).

Não regredidos: `test_opencode_console_*` seguem exigindo omissão explícita (o `commandcode` deixou de omitir no §9, e o teste dele foi reescrito lá), e `test_opencode_go_effort_cannot_be_inferred_from_other_providers` continua comprovando que o Go não herda a família Ollama para um modelo fora do catálogo.

### 8.6 Suíte

```text
$ cd .worktrees/t_a0a4c186
$ uv sync --offline --extra dev
$ uv run --offline --extra dev pytest -q
273 passed, 8 skipped in 0.85s

$ uv run --offline --extra dev pytest -q tests/test_effort.py tests/test_router.py -k "opencode_go or go_route or route_go or operator_effort_map" -v
9 passed, 104 deselected in 0.05s
```

Os 8 skips são os mesmos de sempre (5 do live probe sem `JEV_LIVE_PROBE=1` e 3 de `test_registration.py` condicionados a PyYAML).

### 8.7 Limitações e pendências

- **HTTP 200 não gradua nada:** nesta rodada o endpoint respondeu 200 para todos os níveis testados, inclusive `invalid_level` — o §5.3, em outra execução, registrou 422 para esse mesmo caso com a mensagem de upstream indisponível, não de validação. A medição de `reasoning_tokens` (uma amostra por nível) também não escalou de forma monotônica. Logo o 200 prova que o campo é aceito, não que o nível mude a profundidade do raciocínio; por isso o audit continua afirmando apenas "o campo foi enviado".
- **`thinking` não é usado:** o Go exige objeto (`{type: enabled|disabled}`) e rejeita escalar; o parâmetro aplicado é o top-level `reasoning_effort`.
- **`max` e `xhigh` são contrato, não caminho de produção:** o decider só oferece `low`/`medium`/`high` (`EFFORT_LEVELS`), então `max` e o mapeamento `xhigh`→`max` só se aplicam a chamadas diretas à API do plugin ou a um decider futuro. Estão cobertos por teste, não por turno real.
- **Verificação end-to-end pós-merge** (plugin reinstalado + turno real com `"effort": "<nível>"` no audit e `reasoning_effort` no request) fica para o dono, porque o merge é dele: nenhum turno real com o plugin reinstalado foi executado nesta entrega. As chamadas de rede desta rodada foram apenas os probes de aceitação do endpoint (§8.1).

---

## 9. `reasoning_effort` aplicado no wire do `commandcode` (`t_1906910b`)

Esta seção **supera** a política do `commandcode` registrada em §7.5/§7.5.1 (e a nota do §8 que dizia que a rota continuava omitindo): a rota passa a ser **roteada** e o nível decidido pelo Jev vai no `reasoning_effort` **top-level**, ao lado do `extra_body.thinking` que o profile do provider deriva. O `opencode-console` continua omitindo; o `opencode-go` não muda.

### 9.1 Probe que sustenta a mudança (2026-10-20)

`POST https://api.commandcode.ai/provider/v1/chat/completions`, modelo `deepseek/deepseek-v4.1-flash`:

| Campo enviado | Resultado |
|---|---|
| `reasoning_effort=low` | HTTP 200 |
| `reasoning_effort=medium` | HTTP 200 |
| `reasoning_effort=high` | HTTP 200 |
| `reasoning_effort=xhigh` | HTTP 200 |
| `reasoning_effort=max` | HTTP 200 |

O que o probe prova é **aceitação**, não calibração: `xhigh` também retorna 200 (o vocabulário é uma decisão nossa, a do core) e nenhuma medição de profundidade foi feita. É o mesmo limite registrado para o Go no §8.1/§8.7.

### 9.2 Quem escreve o quê no wire (a ordem do transporte)

A rota tem **dois** componentes de reasoning e eles passaram a ter **dois** autores:

1. `extra_body.thinking` — **profile do provider**. `core/plugins/model-providers/commandcode/__init__.py:42-59` delega ids `deepseek/*` ao profile `deepseek`; `deepseek/__init__.py:28-43` chama `thinking_toggle_extras(reasoning_config, DEEPSEEK_V4_EFFORTS, DEEPSEEK_V4_OVERRIDES, always_emit_toggle=True)` (`core/agent/reasoning_effort.py:197-219`), que emite o toggle a partir do `reasoning_config` da sessão.
2. `reasoning_effort` **top-level** — **plugin**, com o nível decidido pelo Jev.

A precedência vem da ordem real de montagem, rastreada em `core/agent/turn_api_request.py::build_api_request`:

- `api_kwargs = agent._build_api_kwargs(api_messages)` (linha 122/124) → o profile já colocou `extra_body.thinking` e o **seu próprio** `reasoning_effort` (via `chat_completions.py:562-568`) no payload;
- `apply_llm_request_middleware(...)` (linhas 143-151) roda **depois** e `api_kwargs = _llm_request_mw.payload` — o payload devolvido pelo middleware é o que vai para a chamada.

O plugin monta o payload roteado a partir do `original_request` (pré-middleware, que já contém `extra_body.thinking`) e reescreve apenas `model` e `EFFORT_WIRE_KEY` (`router.py::_apply`). Consequência exata:

- o `reasoning_effort` do plugin **sobrescreve** o que o profile derivaria do `reasoning_config`;
- o `extra_body.thinking` **não é tocado** (`extra_body` é preservado inteiro);
- o wire fica `thinking: {"type": "enabled"}` **+** `reasoning_effort: <nível do Jev>`.

Não foi preciso alterar `_apply` nem o core: o transport já aplica o campo do plugin **depois** do profile. Isso está fixado por teste (§9.6, `test_commandcode_lands_the_jev_level_beside_the_profile_thinking_toggle`), que parte de um payload no formato real do profile (`thinking` enabled + `reasoning_effort` derivado de `reasoning_config: {effort: low}`) e comprova o nível do Jev (`high`) no wire com o toggle e o `reasoning_config` intactos.

### 9.3 Política embutida (`effort.py`)

`COMMANDCODE_REASONING_POLICIES["deepseek/deepseek-v4.1-flash"]`: `wire_parameter="reasoning_effort"`, `accepted_levels=("low","medium","high","max")`, `overrides={"xhigh":"max"}`, `omitted=False`, com `evidence` citando o endpoint, a data, os cinco HTTP 200, a ordem `profile → middleware` e o contrato do core (`DEEPSEEK_V4_EFFORTS`/`DEEPSEEK_V4_OVERRIDES`). A `decision` registra que `low`/`medium`/`high`/`max` vão verbatim, `xhigh`→`max` pela override declarada, que nada fora do conjunto é enviado (nem clamp, nem invenção), que `extra_body.thinking`/`reasoning_config` ficam como o profile os montou, e que o 200 prova aceitação — não profundidade.

### 9.4 Auditoria: `provider` no registro de rota

O registro `event: route` não nomeava a rota (só o `skip` nomeava). Com duas rotas servindo `deepseek*/deepseek-v4.1-flash` e ambas enviando `reasoning_effort`, o registro ficou ambíguo. `router.py::_record_route` passou a gravar `"provider": where.provider`, a mesma chave que o `skip` já usava. Consumidores usam `record.get(...)`, então nenhum leitor quebrou.

### 9.5 Config do perfil `code` (aplicada nesta rodada)

`/home/rafael/hermes/home/profiles/code/config.yaml` (backup em `<scratch>/config.yaml.bak-t_1906910b`, permissões `600` preservadas):

```yaml
        routable_providers:
          - opencode-go
          - commandcode
        grids:
          opencode-go:
            - 'deepseek-v4.1-flash: general work, explanation, ordinary coding and tool use'
          commandcode:
            - 'deepseek/deepseek-v4.1-flash: general work, explanation, ordinary coding and tool use'
        effort_maps:
          opencode-go:
            deepseek-v4.1-flash:
              accepted: [low, medium, high, max]
          commandcode:
            deepseek/deepseek-v4.1-flash:
              accepted: [low, medium, high, max]
```

Leitura da config real pelo próprio `load_settings` do worktree:

```text
$ uv run --offline --with pyyaml python <scratch>/t_1906910b_verify_settings.py
routable_providers: ('opencode-go', 'commandcode')
commandcode routable: True
commandcode-chat routable: True
opencode-go routable: True
commandcode grid: ['deepseek/deepseek-v4.1-flash']
commandcode effective default_model: deepseek/deepseek-v4.1-flash

== effort_maps path (o que a config viva declara) ==
  commandcode/low: resolve='low'      omitted=False
  commandcode/medium: resolve='medium' omitted=False
  commandcode/high: resolve='high'    omitted=False
  commandcode/max: resolve='max'      omitted=False
  commandcode/xhigh: resolve='high'   omitted=False
  commandcode/minimal|none|bogus|<empty>: resolve=None omitted=True
  commandcode/ultra: resolve='max'    omitted=False

== built-in policy path (sem effort_maps: o que o plugin mergeado responde) ==
  policy: wire='reasoning_effort' accepted=('low','medium','high','max') overrides={'xhigh':'max'} omitted=False
  low/medium/high/max -> verbatim; xhigh -> 'max'; minimal/none/bogus/ultra/'' -> None

== sem regressão nas rotas já vivas ==
  opencode-go/deepseek-v4.1-flash: {'low':'low','medium':'medium','high':'high','max':'max','xhigh':'high'}
  opencode-console/deepseek-v4.1-flash: todos None
```

A entrada em `effort_maps` tem a mesma função dupla da rodada do Go (§8.4): faz o plugin **hoje instalado** (que só conhece o caminho de `effort_maps`) já honrar a rota, e mantém o perfil explícito. Divergências conhecidas entre os dois caminhos, ambas **inalcançáveis** pelo decider atual (`EFFORT_LEVELS = ("low","medium","high")` valida criteria, `default_effort` e `max_effort`): `xhigh` → `max` pela política embutida e `high` pelo caminho `effort_maps` (regra anti-escalada do caminho custom); `ultra` → omitido pela política embutida e `max` pelo `effort_maps`.

### 9.6 Testes

Novos/reescritos:

- `test_commandcode_explicit_reasoning_policy` (`tests/test_effort.py`) — agora exige `wire_parameter='reasoning_effort'`, `accepted_levels=('low','medium','high','max')`, `overrides={'xhigh':'max'}`, `omitted=False`, marcadores do probe e da ordem do transporte (`turn_api_request`, `extra_body.thinking`) na `evidence`, `low`/`medium`/`high`/`max` verbatim nos três nomes de provider, `xhigh`→`max`, `minimal`/`none`/`bogus`/`ultra`/vazio → `None`, e modelo fora do catálogo continua omitido.
- `test_jev_console_decider_generation_route_commandcode_uses_the_namespaced_catalog` (`tests/test_router.py`) — passa a exigir `request["reasoning_effort"] == "high"` e `record["effort"] == "high"` (antes `None`), mantendo grade de um modelo, pergunta de um critério, decider no SystemOne, ausência de mutação in-place e replay sem segunda chamada.
- `test_commandcode_lands_the_jev_level_beside_the_profile_thinking_toggle` — a regressão do §9.2: payload no formato real do profile, nível do Jev vencendo o derivado (`low`→`high`), `extra_body.thinking` e `reasoning_config` intactos, snapshots byte-idênticos, replay estável.
- `test_commandcode_route_lands_every_decidable_level_on_the_wire` — parametrizado `low`/`medium`/`high`: o campo vai no request e o audit registra `event=route`, `provider=commandcode`, `decider=jev_console`, `effort` igual ao escolhido.

### 9.7 Suíte

```text
$ cd .worktrees/t_1906910b
$ uv sync --offline --extra dev
$ uv run --offline --extra dev pytest -q
276 passed, 8 skipped in 0.76s
```

Os 8 skips são os mesmos de sempre (5 do live probe sem `JEV_LIVE_PROBE=1` e 3 de `test_registration.py` condicionados a PyYAML). A linha de base desta branch era `273 passed, 8 skipped`; o delta são os três casos parametrizados novos.

### 9.8 Limitações e pendências

- **HTTP 200 não gradua nada:** o probe prova que o campo é aceito, não que o nível mude a profundidade do raciocínio. O audit segue afirmando apenas "o campo foi enviado".
- **`thinking` explícito desabilitado pelo usuário não é reinterpretado:** quando o `reasoning_config` da sessão tem `enabled: false`, o profile emite `thinking: {"type": "disabled"}` e nenhum `reasoning_effort`; o plugin ainda aplica o nível do Jev nesse caso, exatamente como já fazia no `opencode-go` desde o §8 (o plugin é dono do nível, o profile é dono do toggle). Nenhum turno real dessa forma foi executado; o comportamento é o mesmo das duas rotas, não uma divergência nova.
- **`max` e `xhigh` são contrato, não caminho de produção:** o decider só oferece `low`/`medium`/`high`.

