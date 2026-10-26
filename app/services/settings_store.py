"""Typed access to the key/value settings table."""

from __future__ import annotations

import json
import secrets
from typing import Any

from sqlmodel import Session

from ..config import config
from ..models import Setting

DEFAULT_MODEL = "claude-opus-5-5"

DEFAULTS: dict[str, Any] = {
    "llm": {"api_key": "", "model": DEFAULT_MODEL, "effort": "medium", "enabled": True},
    "sources": {
        # Company board tokens, e.g. "stripe" for boards.greenhouse.io/stripe.
        "greenhouse": [],
        "lever": [],
        "ashby": [],
        "remotive": {"enabled": False, "queries": []},
        "adzuna": {
            "enabled": False,
            "app_id": "",
            "app_key": "",
            "country": "in",
            "queries": [],
            "where": "",
            "max_pages": 2,
        },
    },
    "ingest": {"interval_hours": 6, "max_age_days": 45},
    "matching": {"min_score_to_show": 0},
    "extension_token": None,  # generated lazily
    "last_ingest": None,
}

SECRET_PATHS = (("llm", "api_key"), ("sources", "adzuna", "app_key"))


def get(session: Session, key: str) -> Any:
    row = session.get(Setting, key)
    default = DEFAULTS.get(key)
    if row is None:
        return json.loads(json.dumps(default))  # deep copy
    value = json.loads(row.value)
    if isinstance(default, dict) and isinstance(value, dict):
        return _merge(default, value)
    return value


def put(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    payload = json.dumps(value)
    if row is None:
        session.add(Setting(key=key, value=payload))
    else:
        row.value = payload
        session.add(row)
    session.commit()


def extension_token(session: Session, rotate: bool = False) -> str:
    token = get(session, "extension_token")
    if rotate or not token:
        token = secrets.token_urlsafe(24)
        put(session, "extension_token", token)
    return token


def llm_settings(session: Session) -> dict[str, Any]:
    s = get(session, "llm")
    # The environment variable wins so keys need not be stored in the DB.
    if config.anthropic_api_key:
        s["api_key"] = config.anthropic_api_key
        s["api_key_source"] = "env"
    else:
        s["api_key_source"] = "settings" if s.get("api_key") else "none"
    return s


def public_settings(session: Session) -> dict[str, Any]:
    """All user-editable settings with secrets masked."""
    out = {k: get(session, k) for k in ("llm", "sources", "ingest", "matching")}
    out["llm"]["api_key_source"] = llm_settings(session)["api_key_source"]
    for path in SECRET_PATHS:
        node = out
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = mask(node.get(path[-1], ""))
    return out


def update_settings(session: Session, incoming: dict[str, Any]) -> None:
    for key in ("llm", "sources", "ingest", "matching"):
        if key not in incoming:
            continue
        current = get(session, key)
        merged = _merge(current, incoming[key])
        # A masked secret coming back from the UI means "unchanged".
        for path in SECRET_PATHS:
            if path[0] != key:
                continue
            new_node, old_node = merged, current
            for part in path[1:-1]:
                new_node, old_node = new_node[part], old_node[part]
            if is_masked(new_node.get(path[-1], "")):
                new_node[path[-1]] = old_node.get(path[-1], "")
        put(session, key, merged)


def mask(value: str) -> str:
    if not value:
        return ""
    return "••••" + value[-4:]


def is_masked(value: str) -> bool:
    return isinstance(value, str) and value.startswith("••••")


def _merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _merge(base.get(k), v) if k in base else v
        return out
    return override
