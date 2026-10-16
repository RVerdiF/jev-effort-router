# Routing grid — models, profiles, and effort mapping

Companion of SPEC.md. Every consumer reads this to know what Jev may choose, what each option means, and
how a chosen level lands on each model family's wire.

## Models offered to Jev

The Choice options are exactly these six, in this order. The option string is
`"<model-id>: <one-line profile>"`; the model id is everything before the first `:`.

Every model id in this default grid belongs to the **Ollama:Cloud** provider — by default this plugin routes `ollama-cloud`. When custom providers are configured under `routable_providers`, their corresponding provider-specific grids configured in `grids.<provider>` are used instead. The
profile text is sent to Jev verbatim, **in English**, and is part of the measured payload, so treat it
as data rather than documentation: it is not a display string, and rewriting or re-translating it
changes what Jev is choosing between. The criteria were moved from French to English in this pass; the
before/after measurement is recorded in the changelog.

| # | Model id (option prefix) | Profile line sent to Jev | Context | Benchmark evidence |
|---|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | the usual choice for general work: everyday writing, explanation, summarising, ordinary coding and tool use; 1M context; cheap for its size | 1M | Terminal-Bench near paid flagships; young model (Sep 2026), watch in production |
| 2 | `kimi-k3` | strongest at complex code and long agentic tasks: multi-file refactors, deep debugging, large repositories; slow and the most expensive | 1M | best open-weight on SWE-bench and GPQA across independent benches |
| 3 | `glm-5.3` | strongest at rigorous reasoning: mathematics, logic, science, quantitative and financial analysis, where a wrong answer is costly | — | GPQA Diamond 91.7 |
| 4 | `glm-5.3-flash` | best reasoning-per-cost on large text: drafting, summarising, translating and structured extraction over long documents; fast | — | highest measured Intelligence Index among flash models in real usage (41.8) |
| 5 | `minimax-m3` | fast tool calling: long sequences of API/CLI actions, repetitive automation, high throughput | — | 210 tok/s, good agentic score |
| 6 | `nemotron-3-nano:30b` | highest throughput and lowest cost: trivial single-step requests only; weak at reasoning and at long context | — | 346 TPS; avoid for reasoning |

**A profile line must name a task family, never praise the model in the abstract.** The two rules a
rewrite has to keep: no superlative with no task attached ("excellent value for money", "excellent in
real use for everyday tasks") and no cost adjective standing in for a task ("fast and economical"). A
line like that is a safe pick on *every* prompt, so the model carrying one swallows decisions that
belong to the others — the mechanism that kept `glm-5.3` and `glm-5.3-flash` off the route until the
grid was rewritten to name task families. `tests/test_grid.py::test_no_profile_is_a_task_free_superlative`
enforces it. Cost and speed may appear as a secondary clause, always beside the task the model is for;
pricing belongs in this file, not in the criterion text.

**The id here is the wire id, verbatim — tag included.** The tier was written as
`nemotron-3-nano` while the provider's catalog names it `nemotron-3-nano:30b`; the bare name
returned `HTTP 404: model "nemotron-3-nano" not found` and killed the turn outright. Verify every
grid entry against the provider's model list (the host caches it at
`<HERMES_HOME>/ollama_cloud_models_cache.json`) rather than against memory or pricing pages — a
provider-side rename is the one drift a passing offline suite cannot see.

Pricing (reference only — the plugin does not use it for decisions): deepseek-v4.1-flash $0.15/$0.60
off-peak first-party ($0.12/$0.48 on OpenRouter, doubled during UTC 01–04h and 06–10h on weekdays);
kimi-k3 $3/$15; glm-5.3 $1/$4; glm-5.3-flash $0.15/$0.50; minimax-m3 $0.60/$2; nemotron-3-nano:30b $0.06/$0.24.

The rest of the profile's Ollama:cloud catalog (`glm-5.1`, `glm-5.2`, `deepseek-v4-flash`,
`deepseek-v4-pro`, `kimi-k2.6`, `kimi-k2.7-code`, `nemotron-3-ultra`, `nemotron-3-super`, `gemma4`,
`qwen3.5`, `minimax-m2.7`, `mistral-large-3`, `gpt-oss`) is deliberately excluded: not benchmarked in
this pass, and a longer Choice list dilutes decision quality. Adding an option is a reviewed change to
this file plus a README note, not a config-only act.

## Effort options

Second Choice question, three options, in this order: `low`, `medium`, `high`. Default when confidence is
below threshold: `medium`.

## Provider-specific grids and effort maps

In v0.4.0+, the router supports multiple providers declared under `routable_providers`.

- **Default (`ollama-cloud`)**: Uses the built-in six-model grid and the built-in effort table below.
- **OpenCode Console (`opencode-console`)**: Uses the built-in two-model grid (`OPENCODE_CONSOLE_GRID`) and strict allowlist (`deepseek-v4.1-flash`, `mimo-v2.6-flash`).
- **Custom providers (e.g. `opencode-go`)**: Configured via `grids.<provider>` and `effort_maps.<provider>`.

### OpenCode Console Grid (`opencode-console`)

The OpenCode Console catalog is strictly gated to an initial allowlist of two models:

| # | Model id | Profile line sent to Jev | Scope / Notes |
|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | the usual choice for general work: everyday writing, explanation, summarising, ordinary coding and tool use; 1M context; cheap for its size | General work, code and reasoning |
| 2 | `mimo-v2.6-flash` | multi-modal, agentic coding and fast reasoning; high throughput | Standard non-free variant |

#### Gate & Privacy Decision (Owner Directive)
- **Owner Directive & Separation of Routes**: The owner explicitly mandated the standard non-free ID `mimo-v2.6-flash` for generation, strictly rejecting `mimo-v2.6-flash-free` and its data retention/model training terms (free tier is reserved only for Jev as decider, with fallback to paid Jev). As clarified by official documentation and the owner orientation (Comment 108), the products and endpoints are cleanly separated:
  - **Decider Route**: Jev Console operates via SystemOne (`https://opencode.ai/inference/systemone/v1/systemone`) using primary `jev-1.13-free` with automatic fallback to paid `jev-1.13`.
  - **Generation Route**: Generation of `deepseek-v4.1-flash` and `mimo-v2.6-flash` is routed to the `opencode-go` provider, where official OpenCode Go documentation confirms standard non-free **MiMo-V2.6-Flash** alongside DeepSeek V4.1 Flash with per-token pricing.
  - **Isolation**: Standard MiMo is not sent to the Console inference endpoint, and `mimo-v2.6-flash-free` is never substituted. Structurally, both `opencode-console` allowlist and `opencode-go` support `deepseek-v4.1-flash` and `mimo-v2.6-flash` offline without live/paid inference.
- **Explicit Reasoning Policy per Model**: The router distinguishes (1) the logical effort level chosen and returned by Jev (`effort_requested`), and (2) the reasoning parameter/mode accepted by the generation endpoint (`effort`). For `opencode-console`:
  - `deepseek-v4.1-flash`: Unconfirmed on OpenCode Console generation endpoint. Support on OpenCode Go (`/zen/go/v1`) or Ollama:cloud CANNOT be inferred. The wire parameter is explicitly omitted (`effort: null`) to prevent HTTP 400 rejection.
  - `mimo-v2.6-flash`: Standard non-free multimodal/agentic model with no documented reasoning effort contract. Wire parameter is explicitly omitted.
  - **Audit Semantics**: Logical effort evaluated by Jev is recorded in audit logs under `effort_requested` and `effort_choice`, but wire `effort` is recorded as `null`, without claiming that the downstream generation model received or executed the chosen reasoning.
  - **Custom Override**: Operators can supply custom `effort_maps` if an endpoint is verified, which is applied strictly without escalation.
- **Isolation**: OpenCode Console and Ollama:cloud remain fully isolated; the Ollama cache never inspects or filters Console models.

### Effort-to-wire mapping (Ollama:Cloud built-in)

For `ollama-cloud`, every route lands on the `ollama-cloud` provider profile, whose own profile code
(`plugins/model-providers/ollama-cloud/__init__.py`) already clamps to
`none | low | medium | high | max` and maps `xhigh → max`, and which emits the field only when the model
is resolved as reasoning-capable. The plugin's table exists to (a) state the family's real vocabulary
where Hermes' generic clamp would be wrong, and (b) omit the field instead of risking a 400.

| Model family | Wire parameter | Accepted levels | Mapping of `low` / `medium` / `high` | Source |
|---|---|---|---|---|
| DeepSeek (V4 / V4.1) | top-level `reasoning_effort` | low, medium, high, max | `low` → low, `medium` → medium, `high` → high | `DEEPSEEK_V4_EFFORTS`, `DEEPSEEK_V4_OVERRIDES` |
| Kimi K3 | top-level `reasoning_effort` | low, high, max (medium → **high** is the declared override; server default is high) | `low` → low, `medium` → high, `high` → high | `KIMI_K3_EFFORTS`, `KIMI_K3_OVERRIDES` |
| GLM-5.3 / GLM-5.3 Flash | top-level `reasoning_effort` | low, medium, high, max (graded, live-verified, monotonic) | passthrough | `GLM53_EFFORTS` |
| MiniMax M3 | top-level `reasoning_effort` | via the Ollama:cloud profile set (none, low, medium, high, max) | passthrough | `OLLAMA_CLOUD_EFFORTS` |
| Nemotron 3 Nano | top-level `reasoning_effort` | via the Ollama:cloud profile set | passthrough; the model is offered for simple tasks, not reasoning, so a `high` answer is recorded and honoured | `OLLAMA_CLOUD_EFFORTS` |

Rules that follow from the table:

1. **Never escalate.** When the requested level is not accepted, clamp to the nearest *weaker* accepted
   level; only send the weakest accepted level when nothing weaker exists. Hermes' `clamp_effort` already
   implements exactly this and is the reference implementation for the plugin's own table.
2. **Never invent a level.** A level outside `low | medium | high` (and the family's override targets) is
   dropped, and the request goes out with no `reasoning_effort` field at all rather than a 400.
3. **`none` is a disable, not a rung.** The plugin never routes to a reasoning-off state.
4. **`max` is not reachable by Jev's three-level question.** It is a valid target only as an override
   destination (`medium → high` for Kimi K3 is; `xhigh → max` is not reachable at all).

This table is the record of what has been verified. A family added later gets its row filled in from a
live probe before it joins the grid; until then the generic clamp applies and the audit record marks the
effort as `clamped`.

### Effort mapping for custom providers (e.g. OpenCode)

For providers other than `ollama-cloud` (e.g. `opencode-go`), effort mapping is strictly opt-in and based on explicit declaration in `effort_maps`:

1. **No speculation / No unverified levels:** Never declare reasoning effort levels without empirical verification via live probe or official provider specifications.
2. **Never escalate:** When a requested effort level is not accepted for a model, clamp only to a weaker accepted level.
3. **Omission on unverified or empty maps:** If an unverified model or an empty accepted list (`accepted: []`) is encountered, the router omits `reasoning_effort` from the request altogether. It never falls back to guessing `low`/`medium`/`high` on custom providers.
4. **Live probe evidence:** The opt-in live probe suite (`tests/test_live_probe.py`) verified against OpenCode (`https://opencode.ai/zen/go/v1`):
   - `deepseek-v4.1-flash`: accepts `null` (omitted), `low`, `medium`, and `high` with HTTP 200.

### Confidence semantics across deciders

Confidence scoring differs fundamentally between the two supported decider backends:

- **TypeSafe Jev (`jev`)**: Produces mathematically calibrated probability distributions across all choices in the question. The `confidence` value represents the concentration of probability on the winning option.
- **OpenAI-Compatible (`openai_compatible`)**: Produces an LLM self-reported heuristic score parsed from the JSON output. While constrained to `[0.0, 1.0]`, this score is uncalibrated and subjective to the model's self-assessment.

## Levers left, in the order they are worth trying

Measured findings and the order of expected return, so the next pass does not re-derive them.

1. **The confidence threshold is the second-order cause of an unused model.** A model can win the
   decision and still never serve a turn: below the 0.5 threshold the answer is discarded and the
   configured fallback model is applied instead. On the pre-rewrite grid, 3 of the 8 task families sat
   at mean confidence 0.40-0.42 and were degraded 5/5 — the grid was deciding and the threshold was
   throwing it away. A per-task threshold, or a threshold that only applies when the leading
   probability is not a clear margin over the runner-up, recovers those turns without loosening
   anything on the tasks that are already confident. Use `grid_coverage.below_threshold` in `status`
   to see how often it fires before changing the number.
2. **Effort can depend on the chosen model, but the endpoint cannot do it.** The two questions are
   evaluated independently and in parallel, so a `high` effort answer reaches a model chosen for
   throughput. Coupling them in code — asking for the effort *after* the model is known, or mapping
   `(model, effort)` through the per-family table instead of only clamping the level — is a behaviour
   change that needs its own measurement. There is no evidence yet that it pays.
3. **`route_per_turn: false` (once per session) is the wrong default for a mixed session.** A session
   that opens with a greeting and then asks for a refactor is served by one model the whole way. Routing
   per turn is the default because of this; the cost is one decision call per user turn, at p50 0.27 s.
4. **Do not add models, and do not reorder the grid, without a measurement.** Every extra option
   measurably dilutes a Choice decision, and the first-listed option has an advantage that is visible
   in the numbers (the generalist holds its position partly through wording, not only through fit).
   Both were measured here; changing either without re-running the 8-task × 5-call protocol makes the
   effect unattributable.
5. **Re-measure after any provider-side change.** Two independent sources of drift: the model ids
   themselves (`nemotron-3-nano` → `nemotron-3-nano:30b`) and the decision model's own calibration
   (`jev_model` pinned to `typesafe/jev-1.13` for exactly this reason). The measurement script pattern
   is: import the plugin package through `importlib`, swap the criterion text, POST to the live
   endpoint with the profile's key, 5 calls per task family, then compare chosen model, mean
   confidence and calls above the threshold.
