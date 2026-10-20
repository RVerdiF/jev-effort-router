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
- **OpenCode Console (`opencode-console`)**: Pinned to its own catalog (`OPENCODE_CONSOLE_GRID`): the one normal generation id the Console list carries, `deepseek-v4.1-flash`.
- **OpenCode Go generation (`opencode-go`)**: While `decider: jev_console` is active, the route is capped to its one-model catalog (`OPENCODE_GO_GRID`: `deepseek-v4.1-flash`). With a legacy decider (`jev`, `openai_compatible`) the route keeps whatever `grids.<provider>` the operator declared, exactly as before.
- **CommandCode (`commandcode`)**: Opt-in OpenAI-compatible route with its own one-model catalog (`COMMANDCODE_GRID`: `deepseek/deepseek-v4.1-flash`, the provider's namespaced id). Capped the same way — only while `decider: jev_console` is active; a legacy decider keeps the operator's grid.
- **OpenAI Codex (`openai-codex`, aliases `codex` / `openai_codex`)**: The ChatGPT OAuth route, which speaks `codex_responses` (the Responses API) rather than `chat_completions`. Its catalog is `CODEX_GRID`, four picker ids across two families. Same opt-in cap as Go and CommandCode.

### OpenCode Console catalog (`opencode-console`)

| # | Model id | Profile line sent to Jev | Scope / Notes |
|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | the usual choice for general work: everyday writing, explanation, summarising, ordinary coding and tool use; 1M context | The Console's own generation id |

Every OpenCode/CommandCode generation catalog in this plugin names **one** model. A decision naming any
other model on `opencode-console` fails open — the request keeps the model the operator configured — and
the same holds for the opt-in Go and CommandCode routes below. The Codex route below is the exception: it
carries two families, whose own ladders differ.

### OpenCode Go generation catalog (`opencode-go`, opt-in)

| # | Model id | Profile line sent to Jev | Scope / Notes |
|---|---|---|---|
| 1 | `deepseek-v4.1-flash` | the usual choice for general work: everyday writing, explanation, summarising, ordinary coding and tool use; 1M context | Normal, per-token |

The cap is a *cap*: a configured `grids.opencode-go` entry outside the catalog is dropped, and a grid
the cap empties falls back to the one-model catalog. Choice, fallback, alternatives and memo replay all
read their options from the capped grid, so no path can select a second model.

### CommandCode generation catalog (`commandcode`, opt-in)

| # | Model id | Profile line sent to Jev | Scope / Notes |
|---|---|---|---|
| 1 | `deepseek/deepseek-v4.1-flash` | the usual choice for general work: everyday writing, explanation, summarising, ordinary coding and tool use; 1M context | The provider's namespaced id, verified against its public unauthenticated `/models` list |

The id is namespaced on purpose: `deepseek/deepseek-v4.1-flash` is CommandCode's own contract, while the
bare `deepseek-v4.1-flash` used by the OpenCode routes is a different one. Neither the bare id nor the
`deepseek/deepseek-v4.1-flash-fast` variant is in the allowlist, so neither can be selected, offered as an
alternative or replayed on this route. The cap mechanics are identical to Go: configured entries outside
the allowlist are dropped from the effective grid, and a surface policy grid cannot widen it.

### OpenAI Codex generation catalog (`openai-codex`, opt-in)

| # | Model id | Profile line sent to Jev | Scope / Notes |
|---|---|---|---|
| 1 | `gpt-6-luna` | general work, coding, explanation and tool use | GPT-6 Luna; ladder `none..max` |
| 2 | `gpt-6-luna-900k` | general work and coding over a 900k context window (large repos, long documents) | Picker alias of `gpt-6-luna`; wire id is the base slug |
| 3 | `gpt-6.1-sol` | harder reasoning, architecture, planning and long-context analysis | GPT-6.1 Sol; ladder `low..max` (no `none`) |
| 4 | `gpt-6.1-sol-900k` | harder reasoning and long-context analysis over a 900k context window | **Not served by this backend** — see below |

Two things about this catalog are easy to get wrong, and both are enforced in code rather than assumed:

- **`-900k` is a Hermes-side picker suffix, not a backend id.** The transport strips it while *building*
  the body (`agent/transports/codex.py`, `strip_codex_context_variant_suffix`), and the large window it
  opts into is resolved from `agent.model` when the session is built — a middleware-time route cannot
  grant it. So `Entry.wire_id` records the id the endpoint actually serves and the router writes **that**,
  never the alias; an operator `grids.openai-codex` entry carries a description only, which is why the
  lookup is answered by the catalog (`router.wire_model_for`) and not by the configured grid entry. A grid
  pinned to `gpt-6-luna-900k` therefore sends `model: gpt-6-luna` — verified live.
- **`gpt-6.1-sol-900k` is not an id this backend lists.** It is absent from the account's model cache
  (`provider_models_cache.json` → `openai-codex`) and from the core's `-900k` eligible bases
  (`agent/model_metadata.py::_CODEX_900K_ELIGIBLE_BASES`, which lists `gpt-6-luna`, `gpt-6-sol`,
  `gpt-6-astra`, the `gpt-5.6-*` tier bases, `gpt-5.4` and `gpt-daybreak-blue-latest` — not `gpt-6.1-sol`).
  A live turn against it returns HTTP 400 "model entitlement" and Hermes falls back to another model, so
  the route is recorded in the audit while the request that answered belonged to a different model. It is
  in `CODEX_GRID`/`CODEX_ALLOWLIST` because the routing card names it, but the delivered profile grids
  leave it out: a grid must not name a model the provider does not serve.

Two families, two effort vocabularies:

| Model | Accepted levels | Source |
|---|---|---|
| `gpt-6-luna`, `gpt-6-luna-900k` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | `CODEX_GPT56_EFFORTS` (`agent/reasoning_effort.py`) |
| `gpt-6.1-sol`, `gpt-6.1-sol-900k` | `low`, `medium`, `high`, `xhigh`, `max` — **no `none`** | `CODEX_ASTRA_EFFORTS`, reached through `NO_DISABLE_TIER_PREFIXES` |

A `none` request on the Sol family is **omitted**, never sent: that route rejects a disable with a 400, and
an omitted `reasoning` field re-enables the model's default effort instead of switching it off. This is the
same rule the rest of this plugin follows — only a declared level goes out, and nothing is invented or
clamped onto another rung.

#### Gate & Privacy Decision (Owner Directive)
- **Owner Directive & Separation of Routes**: The owner's route-reconciliation directive (Comment 108) separated the products and endpoints: Jev SystemOne decides, and generation runs on a dedicated provider. The later single-model directive removed the normal MiMo variant from every generation catalog — no route serves MiMo, and `mimo-v2.6-flash-free` is never substituted. As clarified by official documentation and the owner orientation:
  - **Decider Route**: Jev Console operates via SystemOne (`https://opencode.ai/inference/systemone/v1/systemone`) using primary `jev-1.13-free` with automatic fallback to paid `jev-1.13`.
  - **Generation Route**: Generation of `deepseek-v4.1-flash` is routed to the `opencode-go` provider; the id comes from the official OpenCode Go model list (`https://opencode.ai/v2/docs/console/go`). The opt-in `commandcode` route generates `deepseek/deepseek-v4.1-flash`, an id confirmed from the provider's public unauthenticated `/models` endpoint. The Console decider model is a separate SystemOne id (`jev-1.13-free` / `jev-1.13`). No credential was read to establish either mapping and no paid inference was performed.
  - **Isolation and single-model catalogs**: Each generation catalog carries exactly one model, so choosing a model is degenerate by construction and the decider effectively chooses only the reasoning level. A model outside a route's catalog is never sent, offered as an alternative or replayed; the turn fails open with its payload intact. All catalogs are enforced offline.
- **Explicit Reasoning Policy per Model**: The router distinguishes (1) the logical effort level chosen and returned by Jev (`effort_requested`), and (2) the reasoning parameter/mode accepted by the generation endpoint (`effort`). Each opt-in route states its own contract, and only a probe-confirmed parameter is ever sent:
  - `opencode-console` / `deepseek-v4.1-flash`: unconfirmed on the Console generation endpoint. Support on OpenCode Go (`/zen/go/v1`) or Ollama:cloud cannot be inferred.
  - `opencode-go` / `deepseek-v4.1-flash`: **sends** the decided level as the top-level `reasoning_effort`, on a probe of `POST https://opencode.ai/zen/go/v1/chat/completions` (the `x-opencode-session` header is mandatory) run on 2026-10-20: no field → HTTP 200, and `reasoning_effort=low`, `medium`, `high`, `xhigh` and `max` → HTTP 200, while `reasoning={"effort":"high"}` → HTTP 200 and `thinking=high` → HTTP 400 (`thinking must be an object`). Every accepted response carried `reasoning_content` and `usage.completion_tokens_details.reasoning_tokens` (~25–61 tokens across the probe calls), so the model does emit reasoning on this endpoint. The declared set is the one the Hermes core already drives on this same wire for `deepseek-v4*` ids (`core/plugins/model-providers/opencode-zen` → `DEEPSEEK_V4_EFFORTS = (low, medium, high, max)`, `DEEPSEEK_V4_OVERRIDES = {xhigh: max}`): `low`/`medium`/`high`/`max` pass verbatim, `xhigh` is mapped onto `max` by the same declared override, and anything else (`minimal`, `none`, an unknown string) is omitted instead of being clamped or invented. Two limitations stay on the record: the route does **not** validate the value at all (an out-of-vocabulary string also returned HTTP 200, and one sample per level showed no monotonic reasoning-token scaling), so acceptance is not calibration; and the `thinking` object form is not used at all, because the Go route rejects a scalar there.
  - `commandcode` / `deepseek/deepseek-v4.1-flash`: **sends** the decided level as the top-level `reasoning_effort`, on a probe of `POST https://api.commandcode.ai/provider/v1/chat/completions` run on 2026-10-20: `reasoning_effort=low`, `medium`, `high`, `xhigh` and `max` all returned HTTP 200. The route has **two** reasoning components and they now have **two** authors. The `extra_body.thinking` toggle stays with the provider layer: `build_api_kwargs_extras` (`core/plugins/model-providers/commandcode/__init__.py:42-59`) delegates `deepseek/*` ids to the DeepSeek profile (`deepseek/__init__.py:28-43`), whose `thinking_toggle_extras(..., always_emit_toggle=True)` (`core/agent/reasoning_effort.py:197-219`) emits it from the params-level `reasoning_config`. The top-level `reasoning_effort` is the **plugin's**, with the level Jev decided: the Hermes core builds `api_kwargs` from the profile *before* the `llm_request` middleware runs and then uses the middleware payload for the call (`core/agent/turn_api_request.py::build_api_request`), and the routed payload is built from the pre-middleware `original_request` and rewrites only `model` and `reasoning_effort`. The plugin's level therefore lands **after** the profile's derivation — it overrides the level the profile would have sent and leaves the toggle untouched, so the wire carries `thinking: {"type": "enabled"}` **plus** `reasoning_effort: <level chosen by Jev>`, never one at the expense of the other. The declared set is the same DeepSeek V4 contract the core declares (`low`/`medium`/`high`/`max`, `xhigh`→`max`); anything else (`minimal`, `none`, an unknown string) is omitted rather than clamped or invented. The limitation stays on the record: a 200 proves the endpoint **accepts** the field, not that it grades the level or calibrates depth to it.
  - `openai-codex` / the four `gpt-6*` ids: **sends** the decided level as **`reasoning.effort`**, not as a top-level `reasoning_effort`. This route speaks the Responses API, so the shape is the opposite of the two above: the transport decodes the params-level `reasoning_config` into `reasoning: {"effort": ..., "summary": "auto"}` **while it builds the body** (`core/agent/transports/codex.py::_reasoning_fields`, called from `build_kwargs`), and the `llm_request` middleware runs *after* that build (`core/agent/turn_api_request.py::build_api_request`) — so the payload the plugin receives is already the Responses body, and a top-level `reasoning_effort` in it is not a Responses field at all (the SDK call raises `TypeError: unexpected keyword argument` before a byte is sent). Captured live with `HERMES_DUMP_REQUESTS=1` (`--provider openai-codex`): the body carried `"reasoning": {"effort": "high", "summary": "auto"}`, `"include": ["reasoning.encrypted_content"]` and no `reasoning_effort` key. The plugin rewrites only `reasoning.effort`; `summary`, `include` and the whole object stay the transport's, and a route the transport judged as taking no reasoning field gets none invented (the audit then reports `effort: null`, with `effort_requested` keeping Jev's choice). Vocabularies are per family — `CODEX_GPT56_EFFORTS` (`none`/`low`/`medium`/`high`/`xhigh`/`max`) for the Luna ids, `CODEX_ASTRA_EFFORTS` (`low`/`medium`/`high`/`xhigh`/`max`, **no `none`**) for the Sol ids — and the two `-900k` ids inherit their base's set through the same slug prefix, exactly as the core's `codex_supported_efforts` does. A `none` request on the Sol family is omitted rather than sent: that route 400s on a disable, and an omitted field re-enables the default effort instead of switching it off.
  - **Audit Semantics**: Logical effort evaluated by Jev is recorded in audit logs under `effort_requested` and `effort_choice`. Wire `effort` records the value actually put on the wire — the decided level on `opencode-go`, `commandcode` and `openai-codex`, and `null` on the routes that omit the field (`opencode-console`, and any Codex route the transport judged as taking no `reasoning` field) — so a `null` is a statement about the wire, never about what the model executed. A route record also names its `provider`, the same key a skip record carries. On `opencode-go`, `commandcode` and `openai-codex` an HTTP 200 proves the parameter was accepted, not that the level was graded.
  - **Custom Override**: Operators can supply custom `effort_maps` if an endpoint is verified. The map takes precedence over the built-in policy and is applied strictly without escalation. Because it *replaces* the policy's vocabulary rather than extending it, an entry for a route whose policy already declares the contract drops the policy's declared override: `xhigh` then answers with the nearest weaker accepted level instead of `max`, and `ultra` is clamped instead of omitted. Add an entry only when deliberately narrowing a route to a smaller accepted set — the reference profile's `commandcode` route has none, so its built-in policy (and its `xhigh`→`max`) is what runs.
- **Isolation from the Ollama cache**: The Ollama catalog never inspects or filters models of any opt-in route; and no opt-in route can inherit an Ollama grid entry.


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
   destination (`medium → high` for Kimi K3 is; `xhigh → max` is not reachable at all here, and where a
   route declares it — as the `opencode-go` policy does — only a direct API call can ask for `xhigh`).

This table is the record of what has been verified. A family added later gets its row filled in from a
live probe before it joins the grid; until then the generic clamp applies and the audit record marks the
effort as `clamped`.

### Effort mapping for custom providers (e.g. OpenCode)

For providers other than `ollama-cloud` (e.g. `opencode-go`), the plugin resolves effort from a built-in per-model policy where one exists, and otherwise from an explicit `effort_maps` declaration. The built-in routes are matched exactly: `opencode-go` writes the decided level as `reasoning_effort` for `low`/`medium`/`high`/`max`, maps `xhigh` onto `max` through a declared override (the core's `DEEPSEEK_V4_OVERRIDES`), and omits any other level rather than clamping it — the family table would otherwise land `minimal`/`ultra`/an unknown string on an Ollama rung this route never declared.

1. **No speculation / No unverified levels:** Never declare reasoning effort levels without empirical verification via live probe or official provider specifications.
2. **Never escalate:** When a requested effort level is not accepted for a model, clamp only to a weaker accepted level (custom `effort_maps`); on a built-in route an out-of-vocabulary level is omitted instead.
3. **Omission on unverified or empty maps:** If an unverified model or an empty accepted list (`accepted: []`) is encountered, the router omits `reasoning_effort` from the request altogether. It never falls back to guessing `low`/`medium`/`high` on custom providers.
4. **Live probe evidence:** The opt-in live probe suite (`tests/test_live_probe.py`) verified against OpenCode (`https://opencode.ai/zen/go/v1`):
   - `deepseek-v4.1-flash`: accepts `null` (omitted), `low`, `medium`, and `high` with HTTP 200. The 2026-10-20 direct probe recorded in the Go reasoning policy above is the evidence the built-in policy cites.

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
