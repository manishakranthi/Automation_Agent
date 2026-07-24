"""Persists run defaults (spreadsheet_id, pacing_tab, sheets_credentials) to a
root-level agent_config.json, so recurring runs don't need to retype them.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "agent_config.json"

_KEYS = ("spreadsheet_id", "pacing_tab", "sheets_credentials")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(**kwargs) -> None:
    updates = {k: v for k, v in kwargs.items() if k in _KEYS and v is not None}
    if not updates:
        return
    config = load_config()
    config.update(updates)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
