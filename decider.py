"""Pluggable decider backends for jev-effort-router.

Supported deciders:
- 'jev': OpenRouter Decisions API (typesafe/jev-1.13 by default).
- 'openai_compatible': OpenAI-compatible chat completions (e.g. OpenCode zen/go/v1).

Every method returns a :class:`Decision` or ``None``. It never raises: a timeout, an HTTP error,
a malformed body, an unknown choice or a low-confidence answer all fail-open to ``None`` plus a
reason string, so the caller's request goes out byte-identical to a plugin-disabled run.
"""

from __future__ import annotations

import json
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .config import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_CONSOLE_JEV_FALLBACK_MODEL,
    DEFAULT_CONSOLE_JEV_MODEL,
    DEFAULT_CONSOLE_KEY_ENV,
    EFFORT_LEVELS,
    OPENCODE_CONSOLE_ALLOWLIST,
    OPENCODE_CONSOLE_MODELS,
    OPENCODE_CONSOLE_PROVIDER,
    OPENCODE_CONSOLE_PROVIDER_ALIASES,
    EffectiveSettings,
    Settings,
    _as_float,
    _as_optional_float,
    api_key,
    redact,
)
from .effort import resolve_effort
from .grid import Entry, resolve
from .state import (
    EFFORT_CRITERIA,
    EFFORT_QUESTION_ID,
    MODEL_QUESTION_ID,
    build_payload,
    last_user_message,
    normalise_effort_choice,
    recent_context,
)

logger = logging.getLogger(__name__)

#: Reasons a turn was left unrouted. Stable strings — they are what the audit file records.
REASON_DISABLED = "disabled"
REASON_SURFACE_DISABLED = "surface_disabled"
REASON_NO_API_KEY = "no_api_key"
REASON_SKIPPED_PROVIDER = "provider_not_routed"
REASON_SKIPPED_API_MODE = "api_mode_not_routed"
REASON_SKIPPED_MODEL = "model_not_in_grid"
REASON_SKIPPED_UNKNOWN_MODEL = "model_not_in_provider_catalog"
REASON_SKIPPED_NO_MESSAGE = "no_user_message"
REASON_SKIPPED_REPLAYED = "turn_already_routed"
REASON_UPSTREAM_ERROR = "upstream_error"
REASON_TIMEOUT = "timeout"
REASON_MALFORMED = "malformed_answer"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_UNKNOWN_CHOICE = "unknown_choice"
REASON_USER_DATA = "user_data"
REASON_EXCEPTION = "exception"


@dataclass(frozen=True)
class Decision:
    """A routed turn: the model and the effort that will be applied, plus the audit trail."""

    model: str
    effort: Optional[str]
    effort_requested: Optional[str]
    model_confidence: float
    effort_confidence: float
    model_choice: str
    effort_choice: str
    model_probabilities: Dict[str, float] = field(default_factory=dict)
    effort_probabilities: Dict[str, float] = field(default_factory=dict)
    alternatives: Tuple[str, ...] = ()
    latency_ms: int = 0
    fallback_reasons: Tuple[str, ...] = ()
    decider: str = "jev"

    @property
    def degraded(self) -> bool:
        return bool(self.fallback_reasons)


_EFFORT_ORDER = {level: index for index, level in enumerate(EFFORT_LEVELS)}


def _cap_effort(level: Optional[str], cap: Optional[str]) -> Optional[str]:
    """Lower ``level`` to ``cap`` when it is strictly stronger; never raise it."""
    if not level or not cap:
        return level
    if level not in _EFFORT_ORDER or cap not in _EFFORT_ORDER:
        return level
    return level if _EFFORT_ORDER[level] <= _EFFORT_ORDER[cap] else cap


def _fallback_model(effective: EffectiveSettings) -> str:
    """Resolve the fallback model when the decider's answer was absent, off-grid or low-confidence."""
    chosen = effective.entry_for(effective.default_model)
    if chosen is not None:
        return chosen.model_id
    if effective.grid_from_policy and effective.grid:
        return effective.grid[0].model_id
    return effective.default_model


def _as_probabilities(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): _as_float(value) for key, value in raw.items()}


def _answer(answers: Any, question_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(answers, dict):
        return None
    value = answers.get(question_id)
    return value if isinstance(value, dict) else None


def _choice_text(answer: Dict[str, Any]) -> str:
    if answer_type(answer) != "choice":
        return ""
    return str(answer.get("choice") or "")


def answer_type(answer: Dict[str, Any]) -> str:
    return str(answer.get("type") or "choice").strip().lower() or "choice"


def _criteria_map(model_answer: Dict[str, Any]) -> Dict[str, str]:
    echoed = model_answer.get("criteria")
    if isinstance(echoed, dict):
        return {str(key): str(value) for key, value in echoed.items()}
    return {}


class _SimpleResponse:
    """Minimal response wrapper for stdlib urllib calls."""

    def __init__(self, status_code: int, text: str, data: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self._data = data

    def json(self) -> Any:
        if self._data is not None:
            return self._data
        return json.loads(self.text)


class BaseDecider(ABC):
    """Abstract base decider."""

    def __init__(self, settings: Settings, transport: Any = None) -> None:
        self._settings = settings
        self._transport = transport

    @abstractmethod
    def decide(
        self,
        messages: Sequence[Dict[str, Any]],
        effective: EffectiveSettings,
        *,
        platform: str = "",
        provider: str = "",
    ) -> Tuple[Optional[Decision], Optional[str]]:
        pass


class JevDecider(BaseDecider):
    """Decider backend for TypeSafe Jev via OpenRouter Decisions API."""

    def _post(self, payload: Dict[str, Any]) -> Tuple[Optional[Any], Optional[str]]:
        key_env = self._settings.api_key_env_for("jev")
        headers = {
            "Authorization": f"Bearer {api_key(key_env)}",
            "Content-Type": "application/json",
            "X-Title": "hermes-jev-effort-router",
        }
        if self._transport is not None:
            response = self._transport.post(
                self._settings.endpoint,
                json=payload,
                headers=headers,
                timeout=self._settings.timeout_s,
            )
            return response, None

        import httpx

        with httpx.Client(timeout=self._settings.timeout_s) as client:
            return (
                client.post(
                    self._settings.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self._settings.timeout_s,
                ),
                None,
            )

    def decide(
        self,
        messages: Sequence[Dict[str, Any]],
        effective: EffectiveSettings,
        *,
        platform: str = "",
        provider: str = "",
    ) -> Tuple[Optional[Decision], Optional[str]]:
        key_env = self._settings.api_key_env_for("jev")
        if not api_key(key_env):
            return None, REASON_NO_API_KEY

        payload = build_payload(
            messages,
            effective.grid,
            jev_model=self._settings.jev_model,
            context_turns=self._settings.context_turns,
            platform=platform,
            provider=provider,
        )

        started = time.monotonic()
        try:
            response, _ = self._post(payload)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            name = type(exc).__name__
            timeout_types = ("Timeout", "ReadTimeout", "ConnectTimeout", "PoolTimeout", "TimeoutException")
            if any(token in name for token in timeout_types):
                logger.warning("jev-effort-router: Jev timed out after %d ms (%s)", latency_ms, name)
                return None, REASON_TIMEOUT
            logger.warning("jev-effort-router: Jev call failed: %s: %s", name, redact(exc, 300, env_name=key_env))
            return None, REASON_UPSTREAM_ERROR
        latency_ms = int((time.monotonic() - started) * 1000)

        status = getattr(response, "status_code", None)
        if status is None or not (200 <= int(status) < 300):
            body = redact(getattr(response, "text", ""), 300, env_name=key_env)
            logger.warning("jev-effort-router: Jev returned HTTP %s: %s", status, body)
            return None, REASON_UPSTREAM_ERROR

        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            logger.warning(
                "jev-effort-router: Jev returned a non-JSON body: %s",
                redact(getattr(response, "text", ""), 300, env_name=key_env),
            )
            return None, REASON_MALFORMED

        return self._interpret(data, effective, latency_ms=latency_ms, provider=provider)

    def _interpret(
        self, data: Any, effective: EffectiveSettings, *, latency_ms: int, provider: str = ""
    ) -> Tuple[Optional[Decision], Optional[str]]:
        if not isinstance(data, dict):
            return None, REASON_MALFORMED
        answers = data.get("answers")
        model_answer = _answer(answers, MODEL_QUESTION_ID)
        effort_answer = _answer(answers, EFFORT_QUESTION_ID)
        if model_answer is None and effort_answer is None:
            return None, REASON_MALFORMED

        reasons: List[str] = []

        # -- model -----------------------------------------------------------------
        chosen: Optional[Entry] = None
        model_confidence = 0.0
        model_choice_text = ""
        model_probabilities: Dict[str, float] = {}
        if model_answer is None:
            reasons.append(REASON_MALFORMED)
        else:
            model_choice_text = _choice_text(model_answer)
            model_confidence = _as_float(model_answer.get("confidence"))
            model_probabilities = _as_probabilities(model_answer.get("probabilities"))
            chosen = resolve(_criteria_map(model_answer), model_choice_text, effective.grid)
            if chosen is None:
                reasons.append(REASON_UNKNOWN_CHOICE)
            elif model_confidence < effective.confidence_threshold:
                reasons.append(REASON_LOW_CONFIDENCE)
                chosen = None

        model = chosen.model_id if chosen is not None else _fallback_model(effective)

        # -- effort ----------------------------------------------------------------
        requested: Optional[str] = None
        effort_confidence = 0.0
        effort_choice_text = ""
        effort_probabilities: Dict[str, float] = {}
        answered = False
        if effort_answer is None:
            reasons.append(REASON_MALFORMED)
        elif answer_type(effort_answer) != "choice":
            reasons.append(REASON_MALFORMED)
        else:
            answered = True
            effort_choice_text = _choice_text(effort_answer)
            effort_confidence = _as_float(effort_answer.get("confidence"))
            effort_probabilities = _as_probabilities(effort_answer.get("probabilities"))
            candidate = normalise_effort_choice(effort_choice_text)
            if candidate is None:
                reasons.append(REASON_UNKNOWN_CHOICE)
            elif effort_confidence < effective.confidence_threshold:
                reasons.append(REASON_LOW_CONFIDENCE)
            else:
                requested = candidate

        if requested is None and answered:
            requested = effective.default_effort

        if requested is None and effective.max_effort:
            requested = effective.max_effort
        requested = _cap_effort(requested, effective.max_effort)

        effort = resolve_effort(
            model,
            requested,
            provider=provider,
            effort_maps=self._settings.effort_maps,
        )

        alternatives = tuple(
            entry.model_id for entry in effective.grid if entry.model_id != model
        )
        return (
            Decision(
                model=model,
                effort=effort,
                effort_requested=requested,
                model_confidence=model_confidence,
                effort_confidence=effort_confidence,
                model_choice=model_choice_text,
                effort_choice=effort_choice_text,
                model_probabilities=model_probabilities,
                effort_probabilities=effort_probabilities,
                alternatives=alternatives,
                latency_ms=latency_ms,
                fallback_reasons=tuple(reasons),
                decider="jev",
            ),
            None,
        )


class OpenAICompatibleDecider(BaseDecider):
    """Decider backend for OpenAI-compatible chat completions (e.g. OpenCode)."""

    def _post(self, url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Tuple[Optional[Any], Optional[str]]:
        if self._transport is not None:
            response = self._transport.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._settings.timeout_s,
            )
            return response, None

        import socket
        import urllib.error
        import urllib.request

        data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._settings.timeout_s) as resp:
                status_code = resp.status
                body_bytes = resp.read()
                text = body_bytes.decode("utf-8", errors="replace")
                try:
                    json_data = json.loads(text)
                except Exception:
                    json_data = None
                return _SimpleResponse(status_code, text, json_data), None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            return _SimpleResponse(exc.code, body, None), None
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
                raise TimeoutError(str(exc)) from exc
            raise exc

    def decide(
        self,
        messages: Sequence[Dict[str, Any]],
        effective: EffectiveSettings,
        *,
        platform: str = "",
        provider: str = "",
    ) -> Tuple[Optional[Decision], Optional[str]]:
        key_env = self._settings.api_key_env_for("openai_compatible")
        key = api_key(key_env)
        if not key:
            return None, REASON_NO_API_KEY

        base_url = self._settings.base_url_for()
        if not base_url or not isinstance(base_url, str) or not base_url.strip():
            return None, REASON_UPSTREAM_ERROR

        url = f"{base_url.rstrip('/')}/chat/completions"
        decider_model = self._settings.decider_model_for()

        user_msg = last_user_message(messages)
        ctx = recent_context(messages, self._settings.context_turns)

        grid_lines = [f"- {entry.model_id}: {entry.description}" for entry in effective.grid]
        effort_lines = [f"- {lvl}: {desc}" for lvl, desc in EFFORT_CRITERIA.items()]

        system_prompt = (
            "You are an expert model and effort router for an AI coding assistant.\n"
            "Given the user request, recent context, available models and reasoning effort levels, "
            "select the single best model from the available grid and the appropriate reasoning effort level.\n"
            "Return valid JSON ONLY matching exactly this schema:\n"
            "{\n"
            '  "model": "<model_id>",\n'
            '  "effort": "<low|medium|high>",\n'
            '  "confidence": <float between 0.0 and 1.0>\n'
            "}\n"
            "Rules:\n"
            "- The model MUST be one of the IDs from the Available Models list.\n"
            "- The effort MUST be low, medium, or high.\n"
            "- Return JSON only, with no commentary, markdown or extra text."
        )

        user_prompt_parts = []
        if user_msg:
            user_prompt_parts.append(f"User Request:\n{user_msg}")
        if ctx:
            user_prompt_parts.append(f"Recent Context:\n{ctx}")
        if platform:
            user_prompt_parts.append(f"Platform: {platform}")
        if provider:
            user_prompt_parts.append(f"Provider: {provider}")
        user_prompt_parts.append("Available Models:\n" + "\n".join(grid_lines))
        user_prompt_parts.append("Reasoning Effort Levels:\n" + "\n".join(effort_lines))
        user_prompt_parts.append("Pick the model and effort. Output JSON only.")
        user_prompt = "\n\n".join(user_prompt_parts)

        payload = {
            "model": decider_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Hermes-Agent/0.21.4 (jev-effort-router)",
        }
        if "opencode" in url.lower():
            headers["x-opencode-session"] = f"jev-{int(time.time())}"

        started = time.monotonic()
        try:
            response, _ = self._post(url, payload, headers)
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.monotonic() - started) * 1000)
            name = type(exc).__name__
            timeout_types = ("Timeout", "ReadTimeout", "ConnectTimeout", "PoolTimeout", "TimeoutException")
            if any(token in name for token in timeout_types):
                logger.warning(
                    "jev-effort-router: openai_compatible decider timed out after %d ms (%s)",
                    latency_ms,
                    name,
                )
                return None, REASON_TIMEOUT
            logger.warning(
                "jev-effort-router: openai_compatible decider call failed: %s: %s",
                name,
                redact(exc, 300, env_name=key_env),
            )
            return None, REASON_UPSTREAM_ERROR
        latency_ms = int((time.monotonic() - started) * 1000)

        status = getattr(response, "status_code", None)
        if status is None or not (200 <= int(status) < 300):
            body = redact(getattr(response, "text", ""), 300, env_name=key_env)
            logger.warning("jev-effort-router: openai_compatible decider returned HTTP %s: %s", status, body)
            return None, REASON_UPSTREAM_ERROR

        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            logger.warning(
                "jev-effort-router: openai_compatible decider returned non-JSON body: %s",
                redact(getattr(response, "text", ""), 300, env_name=key_env),
            )
            return None, REASON_MALFORMED

        if not isinstance(data, dict):
            return None, REASON_MALFORMED

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, REASON_MALFORMED
        msg_obj = choices[0].get("message")
        if not isinstance(msg_obj, dict):
            return None, REASON_MALFORMED
        raw_content = msg_obj.get("content")
        if isinstance(raw_content, dict):
            parsed = raw_content
        elif isinstance(raw_content, str):
            content_text = raw_content.strip()
            if content_text.startswith("```"):
                lines = content_text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content_text = "\n".join(lines).strip()
            try:
                parsed = json.loads(content_text)
            except Exception:
                logger.warning(
                    "jev-effort-router: openai_compatible decider content not valid JSON: %s",
                    redact(content_text, 300, env_name=key_env),
                )
                return None, REASON_MALFORMED
        else:
            return None, REASON_MALFORMED

        if not isinstance(parsed, dict):
            return None, REASON_MALFORMED

        return self._interpret(parsed, effective, latency_ms=latency_ms, provider=provider)

    def _interpret(
        self,
        parsed: Dict[str, Any],
        effective: EffectiveSettings,
        *,
        latency_ms: int,
        provider: str = "",
    ) -> Tuple[Optional[Decision], Optional[str]]:
        if not isinstance(parsed, dict):
            return None, REASON_MALFORMED

        # Model validation: strict schema and grid membership
        model_raw = parsed.get("model") or parsed.get("model_id") or parsed.get("choice")
        if model_raw is None or not isinstance(model_raw, str):
            return None, REASON_MALFORMED
        model_choice_text = model_raw.strip()
        if not model_choice_text:
            return None, REASON_MALFORMED

        grid_models = {entry.model_id: entry for entry in effective.grid}
        chosen = grid_models.get(model_choice_text)
        if chosen is None:
            return None, REASON_UNKNOWN_CHOICE

        # Confidence validation: must be finite float in [0.0, 1.0]
        conf_raw = parsed.get("confidence")
        if conf_raw is None or isinstance(conf_raw, bool) or not isinstance(conf_raw, (int, float)):
            return None, REASON_MALFORMED
        try:
            conf_float = float(conf_raw)
        except (TypeError, ValueError):
            return None, REASON_MALFORMED
        if not math.isfinite(conf_float) or conf_float < 0.0 or conf_float > 1.0:
            return None, REASON_MALFORMED

        if conf_float < effective.confidence_threshold:
            return None, REASON_LOW_CONFIDENCE

        # Effort validation
        effort_raw = parsed.get("effort") or parsed.get("reasoning_effort")
        if effort_raw is None or not isinstance(effort_raw, str):
            return None, REASON_MALFORMED
        effort_choice_text = effort_raw.strip()
        candidate = normalise_effort_choice(effort_choice_text)
        if candidate is None:
            return None, REASON_UNKNOWN_CHOICE

        requested: Optional[str] = candidate
        if effective.max_effort:
            requested = _cap_effort(requested, effective.max_effort)

        effort = resolve_effort(
            chosen.model_id,
            requested,
            provider=provider,
            effort_maps=self._settings.effort_maps,
        )

        alternatives = tuple(
            entry.model_id for entry in effective.grid if entry.model_id != chosen.model_id
        )

        return (
            Decision(
                model=chosen.model_id,
                effort=effort,
                effort_requested=requested,
                model_confidence=conf_float,
                effort_confidence=conf_float,
                model_choice=model_choice_text,
                effort_choice=effort_choice_text,
                model_probabilities={chosen.model_id: conf_float},
                effort_probabilities={effort_choice_text: conf_float},
                alternatives=alternatives,
                latency_ms=latency_ms,
                fallback_reasons=(),
                decider="openai_compatible",
            ),
            None,
        )


class JevConsoleDecider(BaseDecider):
    """Decider backend for Jev on OpenCode Console SystemOne."""

    def _post_one(
        self, url: str, payload: Dict[str, Any], headers: Dict[str, str]
    ) -> Tuple[Optional[Any], Optional[str]]:
        if self._transport is not None:
            response = self._transport.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._settings.timeout_s,
            )
            return response, None

        import httpx

        with httpx.Client(timeout=self._settings.timeout_s) as client:
            return (
                client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._settings.timeout_s,
                ),
                None,
            )

    def decide(
        self,
        messages: Sequence[Dict[str, Any]],
        effective: EffectiveSettings,
        *,
        platform: str = "",
        provider: str = "",
    ) -> Tuple[Optional[Decision], Optional[str]]:
        key_env = self._settings.api_key_env_for("jev_console")
        key = api_key(key_env)
        if not key:
            return None, REASON_NO_API_KEY

        url = self._settings.base_url_for("jev_console")
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-Title": "hermes-jev-effort-router",
        }

        # Models to try: primary (free) first, fallback (paid) second
        primary_model = self._settings.decider_model_for("jev_console")
        fallback_model = (self._settings.console_jev_fallback_model or "").strip()
        models_to_try = [primary_model]
        if fallback_model and fallback_model != primary_model:
            models_to_try.append(fallback_model)

        last_reason = REASON_UPSTREAM_ERROR
        started = time.monotonic()

        for model_name in models_to_try:
            payload = build_payload(
                messages,
                effective.grid,
                jev_model=model_name,
                context_turns=self._settings.context_turns,
                platform=platform,
                provider=provider,
            )
            try:
                response, _ = self._post_one(url, payload, headers)
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.monotonic() - started) * 1000)
                name = type(exc).__name__
                timeout_types = ("Timeout", "ReadTimeout", "ConnectTimeout", "PoolTimeout", "TimeoutException")
                if any(token in name for token in timeout_types):
                    logger.warning("jev-effort-router: Jev Console timed out on %s after %d ms (%s)", model_name, latency_ms, name)
                    last_reason = REASON_TIMEOUT
                else:
                    logger.warning(
                        "jev-effort-router: Jev Console call on %s failed: %s: %s",
                        model_name,
                        name,
                        redact(exc, 300, env_name=key_env),
                    )
                    last_reason = REASON_UPSTREAM_ERROR
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            if response is None:
                continue
            status = getattr(response, "status_code", None)
            if status is None or not (200 <= int(status) < 300):
                body = redact(getattr(response, "text", ""), 300, env_name=key_env)
                logger.warning("jev-effort-router: Jev Console returned HTTP %s on %s: %s", status, model_name, body)
                last_reason = REASON_UPSTREAM_ERROR
                continue

            try:
                data = response.json()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "jev-effort-router: Jev Console returned a non-JSON body on %s: %s",
                    model_name,
                    redact(getattr(response, "text", ""), 300, env_name=key_env),
                )
                last_reason = REASON_MALFORMED
                continue

            dec, reason = self._interpret(data, effective, latency_ms=latency_ms, provider=provider)
            if dec is not None:
                return dec, None
            if reason:
                last_reason = reason
                continue

        return None, last_reason

    def _interpret(
        self, data: Any, effective: EffectiveSettings, *, latency_ms: int, provider: str = ""
    ) -> Tuple[Optional[Decision], Optional[str]]:
        if not isinstance(data, dict):
            return None, REASON_MALFORMED
        answers = data.get("answers")
        if not isinstance(answers, dict):
            return None, REASON_MALFORMED

        if MODEL_QUESTION_ID not in answers:
            return None, REASON_MALFORMED
        model_answer = answers[MODEL_QUESTION_ID]
        if not isinstance(model_answer, dict):
            return None, REASON_MALFORMED

        # Specific wire validation: type must be explicitly present, string, and 'choice'
        if "type" not in model_answer or not isinstance(model_answer["type"], str):
            return None, REASON_MALFORMED
        if model_answer["type"].strip().lower() != "choice":
            return None, REASON_MALFORMED

        conf_raw = model_answer.get("confidence")
        if conf_raw is None or isinstance(conf_raw, bool) or not isinstance(conf_raw, (int, float)):
            return None, REASON_MALFORMED
        try:
            model_confidence = float(conf_raw)
        except (TypeError, ValueError):
            return None, REASON_MALFORMED
        if not math.isfinite(model_confidence) or model_confidence < 0.0 or model_confidence > 1.0:
            return None, REASON_MALFORMED

        model_probabilities: Dict[str, float] = {}
        probs_raw = model_answer.get("probabilities")
        if probs_raw is not None:
            if not isinstance(probs_raw, dict) or not probs_raw:
                return None, REASON_MALFORMED
            grid_positions = {str(i) for i in range(1, len(effective.grid) + 1)}
            grid_model_ids = {entry.model_id for entry in effective.grid}
            allowed_prob_keys = grid_positions | grid_model_ids | set(_criteria_map(model_answer).keys())
            prob_sum = 0.0
            for k, v in probs_raw.items():
                k_str = str(k).strip()
                if k_str not in allowed_prob_keys:
                    return None, REASON_MALFORMED
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return None, REASON_MALFORMED
                vf = float(v)
                if not math.isfinite(vf) or vf < 0.0 or vf > 1.0:
                    return None, REASON_MALFORMED
                model_probabilities[k_str] = vf
                prob_sum += vf
            if abs(prob_sum - 1.0) > 0.02:
                return None, REASON_MALFORMED

        if "choice" not in model_answer or model_answer["choice"] is None:
            return None, REASON_MALFORMED
        model_choice_text = str(model_answer["choice"]).strip()
        if not model_choice_text:
            return None, REASON_MALFORMED

        chosen = resolve(_criteria_map(model_answer), model_choice_text, effective.grid)
        if chosen is None:
            return None, REASON_UNKNOWN_CHOICE
        if model_confidence < effective.confidence_threshold:
            return None, REASON_LOW_CONFIDENCE

        model = chosen.model_id
        prov_key = (provider or "").strip().lower()
        if prov_key in (OPENCODE_CONSOLE_PROVIDER, *OPENCODE_CONSOLE_PROVIDER_ALIASES):
            if model not in OPENCODE_CONSOLE_ALLOWLIST:
                return None, REASON_UNKNOWN_CHOICE

        # -- effort ----------------------------------------------------------------
        requested: Optional[str] = None
        effort_confidence = 0.0
        effort_choice_text = ""
        effort_probabilities: Dict[str, float] = {}

        if EFFORT_QUESTION_ID in answers:
            effort_answer = answers[EFFORT_QUESTION_ID]
            if not isinstance(effort_answer, dict):
                return None, REASON_MALFORMED
            if "type" not in effort_answer or not isinstance(effort_answer["type"], str):
                return None, REASON_MALFORMED
            if effort_answer["type"].strip().lower() != "choice":
                return None, REASON_MALFORMED

            e_conf_raw = effort_answer.get("confidence")
            if e_conf_raw is None or isinstance(e_conf_raw, bool) or not isinstance(e_conf_raw, (int, float)):
                return None, REASON_MALFORMED
            try:
                effort_confidence = float(e_conf_raw)
            except (TypeError, ValueError):
                return None, REASON_MALFORMED
            if not math.isfinite(effort_confidence) or effort_confidence < 0.0 or effort_confidence > 1.0:
                return None, REASON_MALFORMED

            e_probs_raw = effort_answer.get("probabilities")
            if e_probs_raw is not None:
                if not isinstance(e_probs_raw, dict) or not e_probs_raw:
                    return None, REASON_MALFORMED
                allowed_effort_keys = set(EFFORT_LEVELS) | {"1", "2", "3"}
                e_prob_sum = 0.0
                for k, v in e_probs_raw.items():
                    k_str = str(k).strip().lower()
                    if k_str not in allowed_effort_keys:
                        return None, REASON_MALFORMED
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        return None, REASON_MALFORMED
                    vf = float(v)
                    if not math.isfinite(vf) or vf < 0.0 or vf > 1.0:
                        return None, REASON_MALFORMED
                    effort_probabilities[k_str] = vf
                    e_prob_sum += vf
                if abs(e_prob_sum - 1.0) > 0.02:
                    return None, REASON_MALFORMED

            if "choice" not in effort_answer or effort_answer["choice"] is None:
                return None, REASON_MALFORMED
            effort_choice_text = str(effort_answer["choice"]).strip()
            candidate = normalise_effort_choice(effort_choice_text)
            if candidate is None:
                return None, REASON_UNKNOWN_CHOICE
            if effort_confidence < effective.confidence_threshold:
                return None, REASON_LOW_CONFIDENCE
            requested = candidate

        if requested is not None:
            requested = _cap_effort(requested, effective.max_effort)

        effort = resolve_effort(
            model,
            requested,
            provider=provider,
            effort_maps=self._settings.effort_maps,
        )

        alternatives = tuple(
            entry.model_id for entry in effective.grid if entry.model_id != model
        )
        return (
            Decision(
                model=model,
                effort=effort,
                effort_requested=requested,
                model_confidence=model_confidence,
                effort_confidence=effort_confidence,
                model_choice=model_choice_text,
                effort_choice=effort_choice_text,
                model_probabilities=model_probabilities,
                effort_probabilities=effort_probabilities,
                alternatives=alternatives,
                latency_ms=latency_ms,
                fallback_reasons=(),
                decider="jev_console",
            ),
            None,
        )


def get_decider(settings: Settings, transport: Any = None) -> BaseDecider:
    """Factory for decider backends."""
    if settings.decider == "openai_compatible":
        return OpenAICompatibleDecider(settings, transport=transport)
    if settings.decider == "jev_console":
        return JevConsoleDecider(settings, transport=transport)
    return JevDecider(settings, transport=transport)
