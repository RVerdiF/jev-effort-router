# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Opt-in `commandcode` generation route.** A new OpenAI-compatible provider (`commandcode`, aliases
  `commandcode-chat` / `commandcode_chat`) carries its own one-model catalog, `COMMANDCODE_GRID`:
  `deepseek/deepseek-v4.1-flash`. The namespaced id was confirmed from the provider's public,
  unauthenticated `/models` endpoint (`https://api.commandcode.ai/provider/v1/models`); the bare
  OpenCode id of the same model is a different contract and is rejected on this route. The cap is
  applied by the existing `route_allowlist` / `route_catalog` mechanism and is opt-in under
  `decider: jev_console`, like the Go route; legacy deciders keep the operator's grid.
- **Provider alias normalisation in `is_provider_routable`.** One table (`_GRID_ALIASES`) now drives
  both the grid lookups and the routable check, so a canonical name and any alias of the same route
  match in both directions. This also fixes a pre-existing asymmetry: a declared `opencode-go` never
  matched the `opencode_go` the host reports, so the route was skipped silently.

### Changed

- **`opencode-go` now applies the decided level as `reasoning_effort`.** The built-in policy for
  `deepseek-v4.1-flash` on that route stops claiming "no confirmed contract": a probe of
  `POST https://opencode.ai/zen/go/v1/chat/completions` (mandatory `x-opencode-session` header) on
  2026-10-20 returned HTTP 200 for `reasoning_effort` at `low`, `medium`, `high`, `xhigh` and `max`,
  with `reasoning_content` and `usage.completion_tokens_details.reasoning_tokens` in the response,
  while `thinking=high` returned HTTP 400 (`thinking must be an object`). The declared vocabulary is
  the one the Hermes core already drives on this same wire for `deepseek-v4*` ids —
  `DEEPSEEK_V4_EFFORTS = ("low","medium","high","max")` with `DEEPSEEK_V4_OVERRIDES = {"xhigh":"max"}`
  — so the policy declares `wire_parameter="reasoning_effort"`,
  `accepted_levels=("low","medium","high","max")`, `overrides={"xhigh":"max"}` and `omitted=False`.
  `resolve_effort` passes a declared level verbatim, honours the declared override, and omits anything
  else (`minimal`, `none`, an unknown string) instead of clamping it, because the family fallback would
  have answered a level this route never declared. Wire `effort` is no longer `null` on this route;
  `effort_requested` keeps recording the Jev choice. `opencode-console` keeps omitting the plugin's own
  field (the `commandcode` route did too until the entry below), and an operator `effort_maps` entry still
  takes precedence. What the probe proves is acceptance (HTTP 200 — even for an out-of-vocabulary level, so
  the route does not validate the value), not calibrated depth: the audit claim stays at "the field was
  sent".
- **Every generation catalog now names a single model.** `OPENCODE_GO_MODELS` is
  `("deepseek-v4.1-flash",)`; `mimo-v2.6-flash` was removed from all grids, allowlists and catalogs.
  With one model per route the model question is degenerate by construction and the decider
  effectively chooses only the reasoning level.
- **`commandcode` now applies the decided level as `reasoning_effort`, beside the profile's `thinking`
  toggle.** The route's reasoning contract has two components with two authors: the provider profile keeps
  emitting `extra_body.thinking` (CommandCode delegates `deepseek/*` ids to the DeepSeek profile, whose
  `thinking_toggle_extras(..., always_emit_toggle=True)` derives it from `reasoning_config`), while the
  plugin now authors the **top-level** `reasoning_effort` with the level Jev decided. The Hermes core builds
  `api_kwargs` from the profile before the `llm_request` middleware runs and uses the middleware payload for
  the call (`agent/turn_api_request.py::build_api_request`), and the routed payload is built from the
  pre-middleware `original_request` and rewrites only `model` and `reasoning_effort` — so the plugin's level
  lands after the profile's derivation, overrides the level the profile would have sent, and leaves the
  toggle alone. The wire therefore carries `thinking: {"type": "enabled"}` **plus** `reasoning_effort: <level
  chosen by Jev>`. A probe of `POST https://api.commandcode.ai/provider/v1/chat/completions` on 2026-10-20
  returned HTTP 200 for `reasoning_effort` at `low`, `medium`, `high`, `xhigh` and `max`, so the policy
  declares `wire_parameter="reasoning_effort"`, `accepted_levels=("low","medium","high","max")`,
  `overrides={"xhigh":"max"}` and `omitted=False`, mirroring `DEEPSEEK_V4_EFFORTS`/`DEEPSEEK_V4_OVERRIDES`.
  Anything the policy does not declare is omitted, never clamped or invented. The probe proves the field is
  accepted, not that the endpoint grades the level. `opencode-console` still omits the plugin's own field,
  and its OpenCode policy is not reused here.

### Fixed

- **The plugin is a virtual uv workspace member, so it installs in more than one profile.** With a
  `[build-system]` block, `pm/workspace.py` kept the declared project name
  (`hermes-plugin-jev-effort-router`) for every profile copy. Two profiles enabling the same plugin
  then produced two workspace members with one name and `uv lock` refused the environment:
  `Two workspace members are both named hermes-plugin-jev-effort-router`. The package builds no
  modules (`py-modules = []`), so the build backend was removed: the member is now metadata-only and
  uv keys it by path (`hermes-plugin-jev-effort-router-<hash>`), which is the layout the plugin
  manager documents for virtual members. Dependencies are unchanged.
- **A profile entry for a route that already has a built-in policy no longer hides its contract.**
  `resolve_effort` resolves a provider that appears in `effort_maps` through the custom path, which
  takes precedence over the built-in policy and clamps to the nearest weaker accepted level. A
  `commandcode` entry declaring `accepted: [low, medium, high, max]` therefore answered `xhigh` with
  `high` (the declared `xhigh`→`max` override was rejected as an escalation) and `ultra` with `max`
  instead of omitting it, so the delivered configuration contradicted the policy it was meant to
  enable. The reference profile now declares only `routable_providers` and `grids` for that route — the
  built-in policy is its single authority — while operator maps of other routes keep their existing
  clamp semantics. Regression: `test_the_delivered_code_profile_config_holds_the_commandcode_contract`
  loads the profile settings through `load_settings` and asserts `low`/`medium`/`high`/`max` verbatim,
  `xhigh`→`max`, omit for `minimal`/`none`/`bogus`/`ultra`/empty, and the untouched `opencode-go`
  semantics; an opt-in companion test reads a real profile file via `JEV_PROFILE_CONFIG`.
- **Route audit records name their `provider`.** A `route` record carried the model, the effort and the
  decider but not the route the decision was applied to, while a `skip` record always named it. With
  `opencode-go` and `commandcode` both sending `reasoning_effort` and both serving
  `deepseek/deepseek-v4.1-flash` (under different ids), a route record could no longer be attributed to a
  route by inspection. The record now carries `provider` at the top level, the same key skip records use.
- **The audit record names the decider model that actually answered.** `router.py` stamped every route
  with `settings.jev_model`, the legacy OpenRouter id (`typesafe/jev-1.13`), so a `decider: jev_console`
  turn — which is answered by `console_jev_model` (`jev-1.13-free`) — was recorded as a model that was
  never consulted. The record now carries the decision's own `decider_model`, and the new
  `decider_fallback_reason` names why the Console fallback (`console_jev_fallback_model`) answered when
  the primary failed over. Provenance survives a memo replay: it is stored with the memo instead of being
  re-derived from the (unconsulted) active configuration.

## [0.4.0] - 2026-10-15

### Added

- **Pluggable Decider Backends:** Introduces pluggable decider architectures supported by `decider.py`:
  - `jev` (**default**): Preserves legacy TypeSafe Jev via OpenRouter Decisions API (`https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`).
  - `openai_compatible`: Generic OpenAI-compatible chat completions endpoint (e.g. OpenCode `https://opencode.ai/zen/go/v1` with `deepseek-v4.1-flash`), with strict JSON output parsing and multi-candidate extraction.
- **Selectable Routable Providers (`routable_providers`):** Replaces hardcoded single-provider gate with a configurable list (default `["ollama-cloud"]`).
- **Per-Provider Grids and Effort Maps:** Optional `grids` and `effort_maps` configuration allowing distinct model grids and reasoning effort translations for each routable provider.
- **Per-Decider Confidence Thresholds:** `confidence_threshold_per_decider` mapping allowing fine-tuning thresholds per backend.
- **Fail-open Effort Guarantees:** Unknown or unmapped providers omit `reasoning_effort` on the wire rather than guessing or escalating.
- **Audit Records:** Enhanced with `decider` and `latency_ms` fields for every decision.

## [0.3.0] - 2026-10-11

### Added

- **Per-surface routing policy (`platform_overrides`).** An operator can declare standing routing policies
  for individual surfaces (e.g. `cron`, `telegram`, `cli`) without affecting any other surface.
  Configured in `plugin.yaml` under `platform_overrides`, each platform entry accepts overrides in
  precedence order: `enabled`, `grid`, `confidence_threshold`, and `max_effort`.
  - `enabled: false`: Leaves requests on that surface untouched, recording a skip entry naming the surface (`surface_disabled`).
  - `grid`: Restricts routing and fallback to a subset of the global grid as a structural cap; Jev cannot choose off-grid models, and low-confidence fallback resolves within the surface grid.
  - `confidence_threshold`: Per-surface decision threshold.
  - `max_effort`: Clamps the reasoning effort level emitted by the router (`low`, `medium`, `high`) before per-family effort translation, applied whether chosen by Jev or from `default_effort`.
  - Absent, `null`, unlisted platforms, or unparseable entries reproduce global 0.2.2 behavior byte for byte.

## [0.2.2] - 2026-09-23

### Fixed

- **Replayed audit records now re-emit the two measured confidences instead of a collapsed copy.**
  The per-turn memo stored a single `confidence = max(model_confidence, effort_confidence)` and the
  replay path recopied that value into *both* confidence fields of every follow-up record in the turn.
  Verified in production: a fresh `0.34 / 0.65` decision re-reported as `0.65 / 0.65` on every replay,
  so the effort confidence was disguised as a model confidence Jev never gave. On a tool-heavy turn
  (≈1 fresh record for ≈11 replays) the tail was dominated by that invented pair, so anyone tuning the
  threshold off the tail calibrated against a number that was not the decision. The memo now keeps both
  confidences, and the replay re-emits that pair. This is an audit/observability fix — routing and
  replay behaviour are unchanged, and the `grid_coverage` report already filtered replayed records.

- **Replayed records report unmeasured fields as `null`, never as a copied value.** A replay only
  persisted the model, effort and confidences, so its record used to carry `effort_choice: ""`,
  `model_probabilities: {}`, `effort_probabilities: {}` and `alternatives: []`. Any aggregate over the
  raw tail silently read those as "this decision measured nothing". They are now `null` (and the confidences
  above are no longer the collapsed max), because an audit record must record what was measured and say
  `null` — omit — for what was not. Collapsing the confidences to a max also destroyed *which* of the two
  answers was weak; the pair is kept so a consumer can tell a model-side from an effort-side fallback.

## [0.2.1] - 2026-09-23

### Changed

- **The six grid profiles were rewritten to name task families, and the two questions are now
  English. This is a change of payload and therefore of behaviour.** `glm-5.3` and `glm-5.3-flash`
  were almost never chosen. The cause was the criterion text, not the models: `glm-5.3-flash` was
  described as *"fast and economical, excellent in real use for everyday tasks"* and `glm-5.3` as
  *"deep scientific and logical reasoning, expensive, for tasks with high analytical demands"*,
  while `deepseek-v4.1-flash` was *"generalist, excellent value for money, 1M context, the default
  choice"*. A superlative with no task attached reads as a safe pick on **every** prompt, and
  "*excellent in real use for everyday tasks*" describes nearly every request a user makes — so
  `glm-5.3-flash` competed with the generalist for ordinary work and lost, while `glm-5.3` was
  reserved for "high analytical demands" and never fired on an ordinary analysis.
  Every profile now names the task family it is for (`glm-5.3-flash` → *"best reasoning-per-cost on
  large text: drafting, summarising, translating and structured extraction over long documents"*,
  `glm-5.3` → *"strongest at rigorous reasoning: mathematics, logic, science, quantitative and
  financial analysis, where a wrong answer is costly"*), the model instructions ask Jev to compare
  every option on the same axes (task, cost, speed) instead of leaving cost adjectives to break ties,
  and the two question instructions and the three effort criteria move from French to English so the
  whole payload is stated in one language. Ids, order and the six-entry count are untouched;
  `tests/test_grid.py::test_no_profile_is_a_task_free_superlative` now fails a profile that praises
  a model without naming a task.

- Measured against the live endpoint, 5 calls × 8 task families = 40 calls per variant (the decision
  is not deterministic, so one call proves nothing). Applied model = the choice after the 0.5
  confidence threshold and the configured fallback, i.e. what actually serves the turn.

  | Task family | Before: chosen → applied | After: chosen → applied |
  |---|---|---|
  | trivial (`2+2`-class) | `glm-5.3-flash` 0.40 → **fallback** (0/5 confident) | `nemotron-3-nano:30b` 0.92 → `nemotron-3-nano:30b` (5/5) |
  | everyday ERP entry | `minimax-m3` 0.69 → `minimax-m3` | `minimax-m3` 0.62 → `minimax-m3` |
  | code refactor | `kimi-k3` 0.64 → `kimi-k3` | `kimi-k3` 0.64 → `kimi-k3` |
  | hard debugging | `deepseek-v4.1-flash` 0.42 → **fallback** (0/5) | `kimi-k3` 0.39 → **fallback** (0/5) |
  | demanding analysis | `glm-5.3` **1/5**, else `deepseek-v4.1-flash`, 0.40 → **fallback** | `glm-5.3` **5/5**, 0.95 → `glm-5.3` |
  | long-text extraction | `deepseek-v4.1-flash` 0.90 → `deepseek-v4.1-flash` | `glm-5.3-flash` **5/5**, 0.98 → `glm-5.3-flash` |
  | sequential tool calling | `minimax-m3` 0.90 → `minimax-m3` | `minimax-m3` 0.96 → `minimax-m3` |
  | web/design copy | `deepseek-v4.1-flash` 0.91 → `deepseek-v4.1-flash` | `deepseek-v4.1-flash` 0.61 → `deepseek-v4.1-flash` |

  Aggregate over the 40 calls: `glm-5.3` 5 → 5 and `glm-5.3-flash` 5 → 5 (each was 1 or 0 before, on
  a distribution that scattered across four models with mean confidence ~0.40 and 0/5 above the
  threshold on the two tasks they now win 5/5); mean model confidence 0.658 → 0.749; calls above the
  0.5 threshold 25/40 → 30/40. The generalist is untouched where it should be — it still takes
  ordinary writing and web copy — and two previously degraded-and-scattered tasks now route
  confidently. Median decision latency 311 ms in both variants.

- The English-instruction change alone was measured separately first (40 calls): it moved
  `analysis` from `glm-5.3` 1/5 to `deepseek-v4.1-flash` 5/5 at 0.50 — i.e. it removed the only GLM
  signal without adding another. It is the grid rewrite that produces the effect; the instructions
  change rides along for consistency of payload language and is not claimed as a gain.

- A control run with two differently-worded variants of the first profile (with/without "the usual
  choice", and "cheap for its size" vs "cheapest of the full-size models") confirmed the effect does
  not depend on that phrase: 40 calls each, identical distributions on 7 of 8 task families, and the
  design prompt at 0.61/0.63 (applied, routed) versus 0.45/0.47 (below threshold, degraded to the
  fallback). The phrase is kept for the generalist, deliberately, so a plain writing turn does not
  fall through to the fallback.

- Evidence quality: 120 live calls across five variants. This measures the mechanism and the
  before/after route distribution; it is not a quality benchmark of the models themselves, and the
  0.95+ confidences on the two GLM tasks are high enough to re-check after any future edit to the
  grid or the instructions.

### Added

- `jev_effort_router_status` now reports `grid_coverage` over the last 200 routed turns: what each
  model was actually **applied** to, which grid entries are **never_chosen**, and which ones Jev picks
  but the confidence threshold then throws away (`below_threshold`). This is the finding that was
  invisible before: a model can be right for the job, present in the provider's catalog and cheap,
  and still never serve a turn — and no single turn shows it. Only first-call records are counted, so
  a turn that replays one decision across a long tool loop is not weighted as if it had decided many
  times. Applied against the real audit trail of this profile, the report names `glm-5.3` under
  `never_chosen` and `glm-5.3-flash` under `below_threshold` — exactly the symptom.

- The same block is rendered on the **text** surfaces (`hermes jev-effort-router status` and the
  `/jev-effort-router status` slash command), not only in the agent tool's JSON: the shell is where an
  operator reads routing state, and a finding that only exists in the tool would stay invisible there.

### Fixed

- The **audit trail restarts** after the `jev-router` → `jev-effort-router` rename: the plugin data
  directory is namespaced by the plugin id *and* a content hash, so the new id opened a new empty
  `routes.jsonl` next to the 880-record history of the old one. `grid_coverage` therefore reports a
  window of a handful of turns until the new trail fills. The old directory is left in place and is
  the source of truth for the pre-rename history.

## [0.2.0] - 2026-09-23

### Changed

- **Renamed: `jev-router` → `jev-effort-router`** (plugin id, repository, CLI command, slash command,
  toolset, tools, Python package and pyproject distribution name).

  The old name was both **taken and less accurate**. On GitHub, `jev-router` is a 340-star project
  (`gargpratyush/jev-router`) that also picks the cheapest model, created six days before this one, and
  `jev-router in:name` returns 155 repositories — searching the old name finds someone else. More
  importantly, "router" alone under-sells what this plugin does: it decides the **model** *and* the
  **reasoning effort**, per turn. Measured across the 241-entry catalog, no other entry does that —
  every other routing entry picks a model and stops, and the only two entries that touch reasoning effort
  (`reasoning-switch`, `compact-reasoning-label`) require you to move the level by hand. The name now
  leads with the capability that has no competitor.

  Renaming the id changes what a user installs, so this is a breaking change for anyone who installed
  `jev-router` directly: remove it and install the new name. There were no catalog or released users at
  the time of the rename.

| Old | New |
|---|---|
| `hermes plugins install …/jev-router` | `hermes plugins install …/jev-effort-router` |
| `/jev-router status` | `/jev-effort-router status` |
| `hermes jev-router status` | `hermes jev-effort-router status` |
| `jev_router_status` / `jev_router_route` | `jev_effort_router_status` / `jev_effort_router_route` |
| `plugins.entries.jev-router.settings.*` | `plugins.entries.jev-effort-router.settings.*` |
| `hermes-plugin-jev-router` (pip) | `hermes-plugin-jev-effort-router` |

  The README also now leads with the model **and effort** combination instead of opening on the
  Zürich→London 7.1-second benchmark, which sold speed rather than the capability this plugin is alone in
  offering.

## [0.1.1] - 2026-09-23

### Changed

- **The grid profiles are now English, which is a change of payload and therefore of behaviour.**
  The six criterion strings in `DEFAULT_GRID` are sent to Jev verbatim and are what it weighs; they
  were French, they are now English (`grid.py`, mirrored in `README.md`, `docs/routing-grid.md`,
  `docs/jev-decisions-api.md` and `tests/test_grid.py`). The ids, the order and the six-entry count are
  untouched, and the docstrings now say the criteria are sent in English and that rewording them is a
  behaviour change rather than an editorial one.
- Measured against the live endpoint, 4 calls per task before and 4 after (16 calls per variant, the
  decision is not deterministic so one call proves nothing):

  | Task | Chosen model, French → English | Mean confidence | Calls at/above the 0.5 threshold |
  |---|---|---|---|
  | trivial (`2+2`) | `glm-5.3-flash` → `glm-5.3-flash` | 0.477 → 0.350 | 1/4 → 0/4 |
  | code refactor | `kimi-k3` → `kimi-k3` | 0.520 → 0.550 | 3/4 → 4/4 |
  | demanding analysis | `glm-5.3` → `glm-5.3` | 0.893 → 0.893 | 4/4 → 4/4 |
  | sequential tool calling | `minimax-m3` → `minimax-m3` | 0.962 → 0.980 | 4/4 → 4/4 |

  All 16 choices are identical between the two variants: discrimination between the four task
  categories is unchanged. The only material difference is confidence on the trivial task, which drops
  from ~0.48 to ~0.35 — further below the 0.5 threshold than it already was. That task was already
  degraded in 3 of 4 French calls, so the applied model (the configured default, option 1
  `deepseek-v4.1-flash`) is unchanged in practice; but the English wording makes the tie between
  `glm-5.3-flash` and `nemotron-3-nano:30b` for a trivial prompt slightly harder to break (0.46/0.36
  versus 0.56/0.31). One task × 4 calls is thin evidence: this measures the mechanism and one
  category-level regression in confidence, not a general quality verdict.

### Fixed

- `jev_router_status` and `jev_router_route` now accept the arguments dict the host's tool registry
  passes positionally (`handler(args, **context)`). Both were declared `handler(recent=5)` /
  `handler(task="", context="")`, so the arguments dict was bound to the first parameter and every
  call that carried a parameter failed with
  `TypeError: int() argument must be a string, a bytes-like object or a real number, not 'dict'`.
  Reproduced through `tools/registry.py::dispatch`: the empty-arguments call worked, which is how it
  hid. Covered by tests that dispatch through a host-shaped call.
- **`provides_middleware:` is declared again — a deliberate reversal of the previous entry.** It had
  been removed because the manifest schema has no such field and the host warned about it on every
  load. That trade was wrong: `hermes plugins validate` diffs the manifest against what
  `register(ctx)` wires and fails the plugin with `undeclared middleware registered (not in
  provides_middleware): llm_request`. That check *is* the plugin-catalog admission gate
  (`plugin-catalog-ci.yml` runs `hermes plugins validate --install-deps` at the pinned sha), so
  omitting the key silently disqualified the plugin from the catalog while looking like harmless
  cleanup — the warning it "fixed" was cosmetic and the failure it caused was not. Reproduced and
  confirmed on Hermes 0.21.4; with the key restored, `hermes plugins validate` reports
  `declared middleware :: matches registrations` and the overall verdict is `ok: true`. The one
  load-time warning is accepted as the price of admission. `test_manifest_fields_are_known_to_the_host`
  now asserts exactly `["provides_middleware"]` as the single tolerated unknown key instead of
  demanding an empty set, and the README/manifest comments and `docs/integration-surface.md` state
  the trade-off rather than denying the field exists.
- Documentation states the plugin's scope explicitly: it is for Hermes running on **Ollama:Cloud**, and
  no other provider is routed (README, manifest description, SPEC non-goals). The README's grid table
  now gives the profiles as they are actually sent to Jev, plus an English gloss beside each.
- The grid entry `nemotron-3-nano` is now `nemotron-3-nano:30b`, the id the provider's own catalog
  uses. The bare name produced `HTTP 404: model "nemotron-3-nano" not found` and failed the whole
  turn — the one outcome the fail-open design exists to prevent.
- New `catalog.py` cross-checks a decision against the provider's cached model list (read from the
  host's `ollama_cloud_models_cache.json`, no credential and no socket) before the chosen model is
  put on the wire. A model the catalog proves absent is refused: the turn keeps the configured model
  and the refusal is recorded as `model_not_in_provider_catalog` with the model Jev picked. No
  catalog evidence (absent/unreadable/unexpected) means "no verdict" and routing proceeds as before.
  `jev_effort_router_status` reports offending grid entries under `grid_unavailable`.

## [0.1.0] - 2026-09-22

First release.

### Added

- `llm_request` middleware that asks TypeSafe Jev (`typesafe/jev-1.13` over OpenRouter's Decisions API) for
  a model and a reasoning-effort level on the first provider request of every user turn, and rewrites the
  outgoing provider kwargs accordingly.
- Six-model routing grid, keyed by position, with the criteria sent to Jev as `{"1": "<model>: <profile>"}`.
- Per-family reasoning-effort translation table: never escalates, never invents a level, omits the field
  rather than risk a 400. Kimi K3's `medium` maps to `high` per its documented vocabulary.
- Confidence guard (default `0.5`): a distrusted answer degrades to the configured default model and
  effort, and the turn still goes out routed but flagged.
- One decision per user turn, memoised on `turn_id` so a turn's whole tool loop replays it without
  disturbing the prompt cache. `route_per_turn: false` routes once per session instead.
- Durable JSONL audit trail under `plugin-data/jev-router/routes.jsonl`: choice, probabilities,
  confidence, alternatives, latency, degradation reasons and turn/session identifiers. Skipped turns are
  recorded with their reason.
- Fail-open on every path: timeout, HTTP error, malformed answer, low confidence, off-grid model, unknown
  provider or API mode all leave the request byte-identical to a plugin-disabled run.
- Operator surfaces: `/jev-router` slash command, `hermes jev-router` CLI family (`status`, `route`,
  `grid`, `tail`, `reset`), and the `jev_effort_router_status` / `jev_effort_router_route` agent tools.
- 74 tests, driving the real middleware callback against a stub Decisions API with no network access.

[0.3.0]: https://github.com/AlphaPerseii3000/jev-effort-router/releases/tag/v0.3.0
[0.2.2]: https://github.com/AlphaPerseii3000/jev-effort-router/releases/tag/v0.2.2
[0.2.1]: https://github.com/AlphaPerseii3000/jev-effort-router/releases/tag/v0.2.1
[0.2.0]: https://github.com/AlphaPerseii3000/jev-effort-router/releases/tag/v0.2.0
[0.1.1]: https://github.com/AlphaPerseii3000/jev-router/releases/tag/v0.1.1
[0.1.0]: https://github.com/AlphaPerseii3000/jev-router/releases/tag/v0.1.0
