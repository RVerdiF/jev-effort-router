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
