# Decider API wires — verified wire contracts

Companion of SPEC.md. Verified against TypeSafe Jev Decisions API and OpenAI-compatible endpoints (including OpenCode `opencode-go`).

The plugin supports three pluggable decider backends configured via `decider`:
1. `jev` (default): TypeSafe Jev on the OpenRouter Decisions API.
2. `openai_compatible`: OpenAI-compatible Chat Completions endpoint (e.g. OpenCode Go).
3. `jev_console`: OpenCode Console SystemOne API (`https://opencode.ai/inference/systemone/v1/systemone`).

---

## Wire 1: TypeSafe Jev Decisions API (`decider: jev`)

### Endpoint

```
POST https://openrouter.ai/api/alpha/decisions
Authorization: Bearer $OPENROUTER_API_KEY
Content-Type: application/json
```

Optional ranking headers: `HTTP-Referer`, `X-Title`. Jev is **not** served by the OpenAI-compatible chat
completions endpoint; chat SDKs do not work against it. Single provider (TypeSafe): $0.042/M input, output
free, 32K context, p50 latency 0.27 s (p99 0.55 s over the last week), 100% uptime / 99.88% availability
over three days. `typesafe/jev-latest` tracks the newest Jev; this plugin pins `typesafe/jev-1.13` by
default so a silent model swap cannot change routing behaviour under the operator.

## Request shape

```json
{
  "model": "typesafe/jev-1.13",
  "state": { "user_message": "...", "recent_context": "...", "surface": "cli", "provider": "ollama-cloud" },
  "questions": {
    "model_route": {
      "type": "choice",
      "instructions": "Which model is best suited to handle this task?",
      "criteria": {
        "1": "deepseek-v4.1-flash: the usual choice for general work: everyday writing…",
        "2": "kimi-k3: strongest at complex code and long agentic tasks…",
        "...": "..."
      }
    },
    "reasoning_effort": {
      "type": "choice",
      "instructions": "Which level of reasoning effort does this message require?",
      "criteria": { "low": "...", "medium": "...", "high": "..." }
    }
  }
}
```

`state` may be a string, an object, or an array; the object form is used here because the recommended
pattern is to send only the narrow context each question needs and to reference nested values with
backticked paths.
`instructions` is the question; `criteria` is a map for `choice`, a list for `score`, a `{true, false}`
map for `noul`. Questions are evaluated independently and in parallel — a decision cannot be conditioned
on another answer at the wire level, which is why the effort question is asked independently and any
coupling is resolved in code.

## Answer shape

```json
{
  "answers": {
    "model_route":      { "type": "choice", "choice": "<one criterion key>", "probabilities": {"<key>": 0.0}, "confidence": 0.87 },
    "reasoning_effort": { "type": "choice", "choice": "medium",         "probabilities": {"medium": 0.79}, "confidence": 0.79 }
  }
}
```

- `choice` is the winning criterion **key**, not the key's description text.
- `probabilities` is the full distribution over the supplied options.
- `confidence` summarises how concentrated that distribution is; it is not a safety signal.
- `noul` answers carry a `noul` probability in [0,1]; `score` answers carry `score` on the ordered rubric.
- Probabilities vary slightly between identical calls (calibrated, not deterministic), so a decision-based
  test asserts a route landed in the allowed set, never an exact string equality of confidences.

Because `choice` returns the criterion key, the plugin builds `criteria` as `{"1": "<model-id>: <profile>", ...}`
and maps the returned key to the model id at index/key lookup, so the human-readable profile text can be
rewritten without breaking the mapping. The brief's example, which put the description into the option
value and split on `":"`, is replaced by this keyed form.

## Failure modes the plugin must handle

| Wire condition | Behaviour |
|---|---|
| HTTP 4xx/5xx, timeout, DNS failure | no route; send an audit record with `fallback_reason`; return `None` |
| `answers` missing a question, or wrong `type` | no route; `fallback_reason: "malformed_answer"` |
| `confidence` below the configured threshold | no route; `fallback_reason: "low_confidence"` |
| `choice` key not in the criteria we sent | no route; `fallback_reason: "unknown_choice"` |
| chosen model not in the configured grid | no route; `fallback_reason: "model_not_in_grid"` |
| `OPENROUTER_API_KEY` unset | plugin inert; one INFO line at registration, no per-turn noise |
| 402 / insufficient credits | no route; `fallback_reason: "upstream_error"` with the status code in the log |

The error body is logged truncated and with the `Authorization` value never included. A route is always
best-effort: the turn must be byte-identical to an unrouted turn when anything in this table fires.

---

## Wire 2: OpenAI-Compatible Decider Endpoint (`decider: openai_compatible`)

### Endpoint

```
POST {decider_base_url}/chat/completions
Authorization: Bearer ${DECIDER_API_KEY_ENV}
Content-Type: application/json
User-Agent: Hermes-Agent/0.21.4 (jev-effort-router)
x-opencode-session: jev-{timestamp}   # added when base_url contains 'opencode'
```

For example, when routing via OpenCode:
- `decider_base_url: https://opencode.ai/zen/go/v1`
- `decider_model: deepseek-v4.1-flash`
- `decider_api_key_env: OPENCODE_GO_API_KEY`

### Request shape

```json
{
  "model": "deepseek-v4.1-flash",
  "messages": [
    {
      "role": "system",
      "content": "You are an expert LLM routing decider for Hermes Agent..."
    },
    {
      "role": "user",
      "content": "Available models for provider opencode-go:\n- deepseek-v4.1-flash: general coding\n- kimi-k3: complex multi-file refactoring\n\nUser prompt:\nrefactor the payment module\n\nPick the model and effort. Output JSON only."
    }
  ],
  "temperature": 0.0,
  "response_format": {"type": "json_object"}
}
```

### Answer shape

The decider model must respond with valid JSON adhering to:

```json
{
  "model": "kimi-k3",
  "effort": "high",
  "confidence": 0.95
}
```

### Strict validation rules on Wire 2

Unlike unstructured chat responses, the decider's output is subjected to strict type and membership validation:

1. **JSON Object**: The payload must parse as a JSON dictionary (handling Markdown code blocks ````json ... ```` if emitted).
2. **Grid Membership**: `model` is required and **must strictly belong to the grid sent for that provider**. If the LLM proposes an unoffered model (e.g. `gpt-4o` when only `opencode-go` models were supplied), it is rejected immediately.
3. **Valid Effort**: `effort` is required and must be one of `low`, `medium`, or `high`.
4. **Finite Numeric Confidence**: `confidence` must be a finite float between `0.0` and `1.0`. String values, NaN, infinity, or values out of bounds trigger immediate validation failure without hallucinating a default confidence.
5. **Fail-Open Policy**: Any validation violation, HTTP error, timeout, or missing key immediately aborts routing (`decide` returns `(None, reason)`), keeping the original turn request 100% byte-identical.

---

## Wire 3: OpenCode Console SystemOne API (`decider: jev_console`)

### Comparison: OpenCode Console vs OpenCode Go vs OpenRouter

| Feature / Aspect | OpenCode Console (`jev_console`) | OpenCode Go (`openai_compatible`) | OpenRouter (`jev`) |
|---|---|---|---|
| **Endpoint** | `POST https://opencode.ai/inference/systemone/v1/systemone` | `POST https://opencode.ai/zen/go/v1/chat/completions` | `POST https://openrouter.ai/api/alpha/decisions` |
| **Protocol** | SystemOne typed decisions (`model`, `state`, `questions` -> `answers`) | OpenAI Chat Completions JSON mode | OpenRouter Decisions API |
| **Credential Env** | `OPENCODE_CONSOLE_API_KEY` (or `console_api_key_env`) | `OPENCODE_GO_API_KEY` (or `decider_api_key_env`) | `OPENROUTER_API_KEY` |
| **Decider Model** | `jev-1.13-free` (primary) with fallback to `jev-1.13` (paid) | `deepseek-v4.1-flash` (or custom chat model) | `typesafe/jev-1.13` |
| **Target Generation Provider** | `opencode-console` (generation model: `deepseek-v4.1-flash` only — normal MiMo is a Go model) | `opencode-go` (catalog: `deepseek-v4.1-flash`, `mimo-v2.6-flash`) | `ollama-cloud` (by default) |
| **Decider Role** | Steering decider only, not generation model | Steering decider only | Steering decider only |

### Endpoint

```
POST {console_endpoint:-https://opencode.ai/inference/systemone/v1/systemone}
Authorization: Bearer ${OPEN...KEY}
Content-Type: application/json
User-Agent: Hermes-Agent/0.21.4 (jev-effort-router)
```

### Request Shape

```json
{
  "model": "jev-1.13-free",
  "state": {
    "user_message": "...",
    "recent_context": "...",
    "surface": "cli",
    "provider": "opencode-console"
  },
  "questions": {
    "model_route": {
      "type": "choice",
      "instructions": "Which model is best suited to handle this task?",
      "criteria": {
        "1": "deepseek-v4.1-flash: general work, ordinary coding and tool use; 1M context",
        "2": "mimo-v2.6-flash: multi-modal, agentic coding and fast reasoning"
      }
    },
    "reasoning_effort": {
      "type": "choice",
      "instructions": "Which level of reasoning effort does this message require?",
      "criteria": { "low": "...", "medium": "...", "high": "..." }
    }
  }
}
```

### Answer Shape & Fallback Model Iteration

The decider responds with SystemOne envelope carrying `answers`:

```json
{
  "answers": {
    "model_route": {
      "type": "choice",
      "choice": "1",
      "probabilities": {"1": 0.95, "2": 0.05},
      "confidence": 0.95
    },
    "reasoning_effort": {
      "type": "choice",
      "choice": "low",
      "probabilities": {"low": 0.90, "medium": 0.10},
      "confidence": 0.90
    }
  }
}
```

When the primary `console_jev_model` fails (HTTP error, timeout, malformed payload), the backend iterates to `console_jev_fallback_model` (e.g. standard/paid Jev on OpenCode Console). If both fail or confidence falls below threshold, the router fails open gracefully leaving the original request intact.
