"""The webui session's own model/effort selection, read from the profile's ``state.db``.

When the operator picks a model in the webui, the choice reaches the backend twice: it is pinned
into the session's row (``sessions.model_config``) and it is supposed to be handed to the agent
being built. If the choice arrives while the pre-warmed build is already running, only the row gets
it — the live agent keeps the profile's default, and a middleware that only sees the request cannot
tell the two apart. This module answers the one question that makes the difference: *what did the
session actually choose?*

The row is written by the ``tui_gateway`` in one flat shape
(``tui_gateway/session_workdir.py``::

    {"model": ..., "provider": ..., "base_url": ..., "api_mode": ...,
     "reasoning_config": {"enabled": ..., "effort": ...}}

and that shape is the only one this module understands. It is not a public contract, so every other
shape — the ``api_server``'s ``browser_model_lock`` wrapper, a ``follow_profile_config`` flag, a
``room_plumbing`` blob, invalid JSON — answers ``None``: the caller then behaves exactly as it does
without this module, and the reason goes to ``DEBUG`` only.

Nothing here is load-bearing: the profile database is opened ``mode=ro`` with a 50 ms lock timeout,
a missing or locked database is "no evidence", and no call raises. Stdlib only — the plugin ships no
new dependency for this.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: The profile database, relative to the profile's home (the same directory that holds
#: ``config.yaml`` and ``logs/``).
DB_FILENAME = "state.db"

#: How long the read waits for the writer's lock. The row is a single indexed lookup on a database
#: the host keeps open; anything slower than this is a busy database, and a busy database is not
#: worth delaying a turn for.
DEFAULT_TIMEOUT_S = 0.05

#: Entries kept by :class:`SelectionReader`. One per turn, so a handful of turns' worth of entries
#: covers a whole tool loop many times over; the cap only bounds a long-lived process.
CACHE_LIMIT = 256

#: Keys that mean "this ``model_config`` was written by another component and is not the flat
#: session shape". The subscription ``api_server`` stores ``{"browser_model_lock": {...}}``; the
#: flags below mark rows whose model comes from somewhere other than a per-session choice. A row
#: carrying one of them says nothing this plugin may apply.
UNSUPPORTED_KEYS: Tuple[str, ...] = (
    "browser_model_lock",
    "follow_profile_config",
    "room_plumbing",
)

#: What the audit says when a fail-open turn was reconciled with the session's own row.
SELECTION_SOURCE_SESSION_ROW = "session_row"


@dataclass(frozen=True)
class Selection:
    """The session's choice, as the row records it.

    ``effort`` is ``None`` when the row carries no level (nothing was pinned, or the shape predates
    ``reasoning_config``), and ``enabled`` is ``None`` when the row does not say whether reasoning
    was on. Both are *absence of information*, never a default: a caller that cannot tell leaves the
    request's own field alone.
    """

    model: str
    provider: str
    effort: Optional[str] = None
    enabled: Optional[bool] = None


def parse_model_config(raw: Any) -> Optional[Selection]:
    """Turn one ``sessions.model_config`` value into a :class:`Selection`, or ``None``.

    ``None`` covers every "this is not a shape I understand" answer, and none of them is logged
    above ``DEBUG``: an unexpected row is a fact about another component's storage, not an error in
    this turn.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:  # noqa: BLE001 - an undecodable blob is simply not a selection
            logger.debug("jev-effort-router: session model_config is not utf-8; ignoring it")
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.debug("jev-effort-router: session model_config is not JSON (%s); ignoring it", exc)
        return None
    if not isinstance(payload, dict):
        logger.debug("jev-effort-router: session model_config is not an object; ignoring it")
        return None

    foreign = [key for key in UNSUPPORTED_KEYS if key in payload]
    if foreign:
        logger.debug(
            "jev-effort-router: session model_config uses an unsupported shape (%s); ignoring it",
            ", ".join(foreign),
        )
        return None

    model = payload.get("model")
    provider = payload.get("provider")
    if not isinstance(model, str) or not model.strip():
        return None
    if not isinstance(provider, str):
        return None

    effort: Optional[str] = None
    enabled: Optional[bool] = None
    reasoning = payload.get("reasoning_config")
    if isinstance(reasoning, dict):
        level = reasoning.get("effort")
        if isinstance(level, str) and level.strip():
            effort = level.strip().lower()
        flag = reasoning.get("enabled")
        if isinstance(flag, bool):
            enabled = flag

    return Selection(
        model=model.strip(),
        provider=provider.strip().lower(),
        effort=effort,
        enabled=enabled,
    )


def read_selection(
    profile_home: Any,
    session_id: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> Optional[Selection]:
    """The session's own selection, or ``None`` when there is nothing usable to read.

    The database is opened read-only through a URI (``mode=ro``), so this module can never write to
    the host's state — including on a profile whose ``-wal`` is being appended to by a running
    dashboard. Every failure (absent file, absent row, locked database, unreadable schema) is
    ``None`` plus a ``DEBUG`` line.
    """
    home = str(profile_home or "").strip()
    wanted = str(session_id or "").strip()
    if not home or not wanted:
        return None

    db_path = Path(home) / DB_FILENAME
    if not db_path.is_file():
        logger.debug("jev-effort-router: no %s under %s; no session selection", DB_FILENAME, home)
        return None

    uri = "file:" + urllib.parse.quote(str(db_path), safe="/") + "?mode=ro"
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=max(0.0, float(timeout_s)))
        row = connection.execute(
            "SELECT model_config FROM sessions WHERE id = ?", (wanted,)
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 - the session row is optional evidence
        logger.debug(
            "jev-effort-router: session selection unreadable (%s: %s)", type(exc).__name__, exc
        )
        return None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:  # noqa: BLE001 - a read-only handle cannot matter now
                logger.debug("jev-effort-router: closing the read-only session db failed (%s)", exc)

    if not row:
        logger.debug("jev-effort-router: no session row %r; no session selection", wanted)
        return None
    return parse_model_config(row[0])


def _context_home() -> Optional[Path]:
    """The active profile's home as the host reports it, or ``None`` when it cannot be asked."""
    try:  # the plugin runs in-process with Hermes, so this is the precise answer
        from hermes_constants import get_hermes_home  # type: ignore

        value = get_hermes_home()
    except Exception:  # noqa: BLE001 - an absent host is not an error, it is no evidence
        return None
    text = str(value or "").strip()
    return Path(text) if text else None


def _data_dir_home(data_dir: Any) -> Optional[Path]:
    """The profile home implied by a plugin data directory (``<home>/plugin-data/<plugin>``)."""
    text = str(data_dir or "").strip()
    if not text:
        return None
    path = Path(text)
    if not path.is_absolute():
        return None
    home = path.parent.parent
    return home if str(home) not in ("", ".") else None


def profile_home(data_dir: Any = None) -> Optional[Path]:
    """The profile home whose ``state.db`` holds the session rows.

    The host is asked first, because a profile can be relocated and ``get_hermes_home()`` follows
    it. The plugin data directory is the fallback: the host hands a plugin
    ``<profile home>/plugin-data/<plugin slug>``, so two levels up is the directory that holds the
    database the session rows were written to.
    """
    return _context_home() or _data_dir_home(data_dir)


class SelectionReader:
    """Reads session selections with a per-turn cache, so a tool loop reads the database once.

    The row is written before the turn's first request and a turn is one conversation step, so the
    answer cannot change inside one ``turn_id``. A ``None`` is cached too: a locked or malformed
    database must not be retried on every provider request of the same turn.

    The cache is keyed by ``(profile home, session id, turn id)`` so two profiles or two sessions
    of one process can never see each other's row. It is bounded: overflow drops everything, which
    costs one extra read and keeps the process from growing with the day's sessions.
    """

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        reader: Callable[..., Optional[Selection]] = read_selection,
        cache_limit: int = CACHE_LIMIT,
    ) -> None:
        self._timeout_s = timeout_s
        self._reader = reader
        self._cache_limit = max(1, int(cache_limit))
        self._cache: Dict[Tuple[str, str, str], Optional[Selection]] = {}

    def read(self, profile_home: Any, session_id: str, turn_id: str = "") -> Optional[Selection]:
        """The session's selection for ``turn_id``, read at most once per turn."""
        key = (
            str(profile_home or "").strip(),
            str(session_id or "").strip(),
            str(turn_id or "").strip(),
        )
        if not key[0] or not (key[1] or key[2]):
            return None
        if key in self._cache:
            return self._cache[key]
        try:
            value = self._reader(key[0], key[1], timeout_s=self._timeout_s)
        except Exception as exc:  # noqa: BLE001 - a reader bug must not break a turn
            logger.debug(
                "jev-effort-router: session selection read failed (%s: %s)", type(exc).__name__, exc
            )
            value = None
        if len(self._cache) >= self._cache_limit:
            self._cache.clear()
        self._cache[key] = value
        return value

    def clear(self) -> None:
        self._cache.clear()
