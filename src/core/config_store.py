from __future__ import annotations

import html
import re
from typing import Any


class ConfigStore:
    """One per plugin instance. Cleared on unload to avoid Plugin Reloader leaks."""

    def __init__(self):
        self._server_export_config: dict | None = None
        self._activation_config: dict | None = None
        self._telemetry_collector: Any = None

    def set_server_export_config(self, config: dict) -> None:
        self._server_export_config = config

    def get_server_export_config(self) -> dict | None:
        return self._server_export_config

    def has_server_export_config(self) -> bool:
        return self._server_export_config is not None

    def clear_server_export_config(self) -> None:
        self._server_export_config = None

    def set_activation_config(self, config: dict) -> None:
        """Publish the config a live fetch returned. An empty or non-dict answer
        is ignored, so the last good one keeps serving the session."""
        if not isinstance(config, dict) or not config:
            return
        self._activation_config = config

    def get_activation_config(self) -> dict | None:
        """The config in force, or None when nothing has been fetched yet.

        Nothing expires on age. This config carries the kill switches, the
        links and the update floor, and it is fetched ONCE per session (see
        _warm_activation_config at startup): dropping it after a while would
        silently put every switch back on and send every link back to its
        shipped constant, in the middle of a session that runs for hours. So a
        served value applies until the plugin unloads.

        Only a live fetch ever reaches this store, so a feature can only be
        switched off by the server, never by anything left behind on disk.
        """
        return self._activation_config

    def clear_activation_config(self) -> None:
        self._activation_config = None

    def set_telemetry_collector(self, collector: Any) -> None:
        self._telemetry_collector = collector

    def get_telemetry_collector(self) -> Any:
        return self._telemetry_collector

    def clear(self) -> None:
        self._server_export_config = None
        self._activation_config = None
        if self._telemetry_collector is not None:
            shutdown = getattr(self._telemetry_collector, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:  # nosec B110
                    pass
        self._telemetry_collector = None


_store: ConfigStore | None = None


def set_store(store: ConfigStore | None) -> None:
    global _store
    _store = store


def get_store() -> ConfigStore | None:
    return _store


# -- server-tunable dials ---------------------------------------------------
# Numeric tunables served inside the export config so they can change with a
# server deploy instead of a plugin release. Every read is fail-open: the
# shipped constant wins whenever the store is empty (startup, old server) or
# the server value is invalid. Lock-free reads of the already-loaded dict, so
# safe from worker threads.


def _read_export_value(path: str) -> Any:
    """Raw dotted-path lookup into the server export config, or None."""
    store = _store
    cfg = store.get_server_export_config() if store is not None else None
    if not cfg:
        return None
    val: Any = cfg
    for part in path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


# Bounds every served list read below. A config is not a trusted input: it can
# arrive with thousands of entries, or with one entry megabytes long, and these
# reads happen on the click path and on menu rebuilds. Both bounds apply BEFORE
# an entry is stripped or copied, so a huge value is never materialised.
_MAX_DIAL_ENTRIES = 200
_MAX_DIAL_ENTRY_CHARS = 200


def get_export_dial(path: str, fallback):
    """Positive numeric dial at dotted ``path``, coerced to the fallback's
    kind (int fallback -> int). Anything else returns the shipped constant."""
    try:
        val = _read_export_value(path)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
            return fallback
        return type(fallback)(val)
    except Exception:  # nosec B110
        return fallback


def get_export_dial_pair(path: str, fallback: tuple[float, float]) -> tuple[float, float]:
    """Two-element (lo, hi) dial, e.g. a clamp range. Valid only as a
    two-number list with 0 < lo <= hi; else the shipped constant."""
    try:
        val = _read_export_value(path)
        if isinstance(val, (list, tuple)) and len(val) == 2:
            lo, hi = val
            if (
                not isinstance(lo, bool)
                and not isinstance(hi, bool)
                and isinstance(lo, (int, float))
                and isinstance(hi, (int, float))
                and 0 < lo <= hi
            ):
                return (float(lo), float(hi))
    except Exception:  # nosec B110
        pass
    return fallback


def get_export_dial_list(path: str, base=(), extra_only: bool = True, normalize=None) -> frozenset:
    """Additive-union string-set dial: the server list at ``path`` holds EXTRA
    entries added to the shipped ``base``. Union-only is the safety property
    (a deploy can ADD entries but never remove one, so it can never relax a
    shipped exclusion; a wrong addition is undone by another deploy).
    Non-string/blank entries are dropped; absent or malformed = base unchanged.
    ``normalize`` (e.g. str.lower) applies to server entries only, so casing
    drift in the config can't defeat a match. ``extra_only=False`` disables
    the server merge (shipped base only); replace semantics do not exist."""
    merged = frozenset(base)
    if not extra_only:
        return merged
    try:
        val = _read_export_value(path)
        if isinstance(val, (list, tuple)):
            for item in val[:_MAX_DIAL_ENTRIES]:
                if not isinstance(item, str):
                    continue
                entry = item[:_MAX_DIAL_ENTRY_CHARS].strip()
                if entry:
                    merged |= {normalize(entry) if normalize else entry}
    except Exception:  # nosec B110
        pass
    return merged


def get_export_dial_seq(
    path: str,
    base=(),
    max_len: int = 24,
    require_base_overlap: bool = False,
) -> tuple[str, ...]:
    """Ordered additive-union string sequence: the shipped ``base`` first in
    its shipped order, then any server entry at ``path`` that the base does
    not already carry, appended in served order. Same union-only safety as
    get_export_dial_list (a deploy ADDS, never removes a shipped entry), for
    the call sites where display order matters. Non-string, blank and
    duplicate entries are dropped, and ``max_len`` bounds how long one deploy
    can make the result. Absent or malformed leaves the shipped order alone.

    ``require_base_overlap`` treats a served list that names none of the
    shipped entries as a mis-keyed config and ignores it whole, instead of
    appending entries that probably belong to another setting."""
    merged = list(base)
    seen = set(merged)
    try:
        val = _read_export_value(path)
        if not isinstance(val, (list, tuple)):
            return tuple(merged)
        cleaned = []
        for item in val[:_MAX_DIAL_ENTRIES]:
            if not isinstance(item, str):
                continue
            entry = item[:_MAX_DIAL_ENTRY_CHARS].strip()
            if entry:
                cleaned.append(entry)
        if require_base_overlap and not any(entry in seen for entry in cleaned):
            return tuple(merged)
        for entry in cleaned:
            if len(merged) >= max_len:
                break
            if entry not in seen:
                seen.add(entry)
                merged.append(entry)
    except Exception:  # nosec B110
        return tuple(base)
    return tuple(merged)


def get_export_dial_objs(path: str, max_len: int = 40) -> tuple[dict, ...]:
    """Bounded tuple of the dict entries served at dotted ``path``.

    For the few dials whose entry is a small record rather than a number or a
    string (a demo scene, with an id and an extent). Non-dict entries are
    dropped here; every FIELD inside a kept entry stays the caller's to check,
    because only the caller knows what a usable record looks like. Absent or
    malformed reads as an empty tuple, which every caller treats as "the
    shipped list, unchanged"."""
    try:
        val = _read_export_value(path)
        if not isinstance(val, (list, tuple)):
            return ()
        return tuple(item for item in val[:max_len] if isinstance(item, dict))
    except Exception:  # nosec B110
        return ()


def get_export_dial_ratio(path: str, fallback: float) -> float:
    """Dial for a value that is a FRACTION of something (0 < x <= 1).

    get_export_dial has no upper bound, and these call sites compare a measured
    ratio against the dial, so a served 5 would make the comparison always true
    and light up a warning that can never clear. Out of range reads as absent."""
    value = get_export_dial(path, fallback)
    return value if 0 < value <= 1 else fallback


def get_export_dial_str(path: str, fallback: str, allowed=None) -> str:
    """String dial: a non-empty server string wins, optionally restricted to
    the ``allowed`` set; anything else returns the shipped fallback."""
    try:
        val = _read_export_value(path)
        if isinstance(val, str):
            val = val.strip()
            if val and (allowed is None or val in allowed):
                return val
    except Exception:  # nosec B110
        pass
    return fallback


# -- served copy ------------------------------------------------------------
# Text the user reads. The server sends it already written in the language the
# plugin asked for (see request_context), so it carries no tr() and needs none.
# Three properties make it safe to render:
#   - it replaces ONE shipped string at a time, so a bad entry costs that
#     string and never the screen around it;
#   - it is capped, stripped of control characters, and stripped of the angle
#     brackets that could open a tag, here, before any widget sees it;
#   - the call sites that build rich text escape it on top of that.
# The angle brackets are removed rather than escaped because these labels are
# AutoText: Qt decides per string whether to render markup, so an escaped
# entity would show as itself ("&#x27;") in a label that decided the text was
# plain. Removing the character is the only answer that reads correctly under
# both decisions. A served string cannot contain "<" or ">".
# Absent config, absent entry, wrong type or an empty string all mean the
# shipped string, which is what a user sees today.
_MAX_COPY_CHARS = 400
# Tab and newline stay: a sentence may legitimately wrap. Everything else in
# the control ranges goes, including the C1 block.
_COPY_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f<>]")
# A pool is a rotation of interchangeable lines (the waiting messages), not a
# policy list. Bounded the same way as any other served list.
_MAX_POOL_ENTRIES = 40


def _clean_served_text(value: Any, max_chars: int) -> str | None:
    """One served string, cleaned, or None when nothing usable is left. The cap
    applies before the scrub so an over-long value is never fully copied."""
    if not isinstance(value, str):
        return None
    text = _COPY_CTRL_RE.sub("", value[:max_chars]).strip()
    return text or None


def get_export_text(container_path: str, key: str, max_chars: int = _MAX_COPY_CHARS) -> str | None:
    """One entry of a served ``{key: text}`` map at ``container_path``, or None
    when the map or the entry is missing or unusable. Validation is per entry:
    one bad entry never costs the others."""
    try:
        container = _read_export_value(container_path)
        if isinstance(container, dict):
            return _clean_served_text(container.get(key), max_chars)
    except Exception:  # nosec B110
        pass
    return None


def get_export_copy(
    string_id: str,
    fallback: str,
    max_chars: int = _MAX_COPY_CHARS,
    escape: bool = False,
) -> str:
    """Served replacement for one shipped user-facing string, or the shipped
    string. ``string_id`` is a flat key under ``copy``, so it may carry dots
    without the server having to nest anything.

    ``escape=True`` for a label the plugin builds rich text into (a link, a
    span): the only character left to deal with there is "&", and it has to
    become an entity or the parser eats it. The shipped fallback is returned
    untouched either way, so an absent entry changes nothing at all."""
    served = get_export_text("copy", string_id, max_chars)
    if served is None:
        return fallback
    return html.escape(served, quote=False) if escape else served


def get_activation_copy(
    string_id: str,
    fallback: str,
    max_chars: int = _MAX_COPY_CHARS,
    escape: bool = False,
) -> str:
    """Same contract as ``get_export_copy`` for the ``copy`` map of the
    activation config (the one fetched once per session at startup). The two
    maps are served by two routes, so a string lives in one or the other,
    never both."""
    try:
        store = get_store()
        config = store.get_activation_config() if store is not None else None
        container = config.get("copy") if isinstance(config, dict) else None
        served = _clean_served_text(container.get(string_id), max_chars) if isinstance(container, dict) else None
    except Exception:  # nosec B110
        served = None
    if served is None:
        return fallback
    return html.escape(served, quote=False) if escape else served


def get_export_copy_pool(string_id: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Served replacement for a POOL of interchangeable lines under ``copy``,
    or the shipped pool. Replacement rather than union, because a pool is copy:
    a line that reads badly has to be removable the same day. The union-only
    rule guards the sets and lists that carry policy (which codes are a server
    fault, which prompts match a rule), where dropping a shipped entry could
    relax something the plugin promises; a waiting message promises nothing.

    Entries are validated one by one and the bad ones dropped. An empty result
    keeps the shipped pool, so a truncated or garbled list changes nothing."""
    try:
        container = _read_export_value("copy")
        if not isinstance(container, dict):
            return fallback
        raw = container.get(string_id)
        if not isinstance(raw, (list, tuple)):
            return fallback
        lines = []
        for item in raw[:_MAX_POOL_ENTRIES]:
            line = _clean_served_text(item, _MAX_COPY_CHARS)
            if line is not None:
                lines.append(line)
        return tuple(lines) if lines else fallback
    except Exception:  # nosec B110
        return fallback


def get_export_block(path: str) -> dict | None:
    """The raw dict at dotted ``path``, or None when it is absent or is not a
    dict. For the few readers that must judge a whole block before trusting any
    part of it (an entitlement, where one stray key must not widen anything on
    its own). Every other reader validates one field at a time."""
    try:
        val = _read_export_value(path)
        return val if isinstance(val, dict) else None
    except Exception:  # nosec B110
        return None


class ServerDialSet(frozenset):
    """Read-through frozenset: ``in`` also matches server-added extras at
    ``cfg_key`` (additive union via get_export_dial_list, so a server deploy
    can only ADD entries, never remove a shipped one). Iteration and set
    algebra see only the shipped entries."""

    def __new__(cls, cfg_key: str, base, normalize=None):
        obj = super().__new__(cls, base)
        obj._cfg_key = cfg_key
        obj._normalize = normalize
        return obj

    def __contains__(self, item) -> bool:
        if frozenset.__contains__(self, item):
            return True
        return item in get_export_dial_list(self._cfg_key, (), normalize=self._normalize)


class ServerDialMap(dict):
    """Read-through dict: ``[]``/``get`` prefer the matching server entry
    (validated per-entry by get_export_dial), so existing dict call sites
    become tunable without changing shape. Keys stay the shipped set."""

    def __init__(self, cfg_key: str, defaults: dict):
        super().__init__(defaults)
        self._cfg_key = cfg_key

    def __getitem__(self, key):
        return get_export_dial(f"{self._cfg_key}.{key}", dict.__getitem__(self, key))

    def get(self, key, default=None):
        if key in self:
            return self[key]  # routes through the dial-aware __getitem__
        return default
