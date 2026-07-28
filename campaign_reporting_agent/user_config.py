"""Persists run defaults (spreadsheet_id, pacing_tab, sheets_credentials) to a
root-level agent_config.json, so recurring runs don't need to retype them.
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "agent_config.json"

_KEYS = ("spreadsheet_id", "pacing_tab", "sheets_credentials")

# Falls back to these env vars when agent_config.json is absent or incomplete --
# needed on hosts like Render where the filesystem is ephemeral and there's no
# shell to run the CLI once to write the file.
_ENV_FALLBACKS = {
    "spreadsheet_id": "SPREADSHEET_ID",
    "pacing_tab": "PACING_TAB",
    "sheets_credentials": "SHEETS_CREDENTIALS_PATH",
}


def load_config() -> dict:
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            config = json.load(f)
    for key, env_name in _ENV_FALLBACKS.items():
        if not config.get(key) and os.environ.get(env_name):
            config[key] = os.environ[env_name]
    return config


def save_config(**kwargs) -> None:
    updates = {k: v for k, v in kwargs.items() if k in _KEYS and v is not None}
    if not updates:
        return
    config = load_config()
    config.update(updates)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
