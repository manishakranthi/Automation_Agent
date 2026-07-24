"""Reads each platform's raw export and normalizes it to a common schema:
match_key, spent, impressions, clicks, status, note.

Column lookups are always by name (never position) per the prompt's constraint
that report column order changes between exports.
"""

import warnings

import pandas as pd

from .config import COLUMN_MAP, EXCLUSION_RULES, READ_SETTINGS, TABOOLA_NOTE_STATUS_PREFIXES
from .utils import clean_numeric


class PlatformReadError(Exception):
    def __init__(self, platform: str, path: str, original: Exception):
        self.platform = platform
        self.path = path
        self.original = original
        super().__init__(f"Failed to read {platform} report at {path}: {original}")


def _field_candidates(value):
    """A COLUMN_MAP field value is either a single column name or a tuple of
    candidate names to try in order (for platforms with multiple export
    templates, e.g. StackAdapt's "Campaign Name" vs "Campaign Group")."""
    return (value,) if isinstance(value, str) else tuple(value)


def _columns_satisfied(columns, cols_map):
    return all(
        any(name in columns for name in _field_candidates(cols_map[field]))
        for field in ("match_key", "spent", "impressions", "clicks")
    )


def _resolve_column(columns, value):
    for name in _field_candidates(value):
        if name in columns:
            return name
    return _field_candidates(value)[0]


def _read_excel(path: str) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*[Ww]orkbook contains no default style.*",
        )
        return pd.read_excel(path, sheet_name=0, engine="openpyxl")


def _detect_encoding(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        with open(path, "r", encoding="utf-8") as f:
            f.read()
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _detect_sep(path: str, encoding: str, skiprows: int) -> str:
    try:
        with open(path, "r", encoding=encoding) as f:
            line = ""
            for _ in range(skiprows + 1):
                line = f.readline()
                if not line:
                    break
    except (UnicodeDecodeError, OSError):
        return ","
    return "\t" if line.count("\t") > line.count(",") else ","


def _read_csv_with(path: str, encoding: str, sep: str, skiprows: int) -> pd.DataFrame:
    df = pd.read_csv(path, encoding=encoding, sep=sep, skiprows=skiprows)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def parse_platform_report(platform: str, path: str) -> pd.DataFrame:
    """Returns a normalized DataFrame with columns:
    match_key, spent, impressions, clicks, status, note, source_index

    CSV platforms are read using the documented encoding/delimiter first; if
    that fails outright or simply doesn't yield the expected columns (export
    templates vary -- e.g. some Google Ads exports are UTF-16 tab-separated,
    others are plain UTF-8 comma-separated), the reader falls back to
    auto-detecting encoding (via BOM sniffing) and delimiter before giving up.
    """
    if platform not in COLUMN_MAP:
        raise ValueError(f"Unknown platform: {platform}")

    settings = READ_SETTINGS[platform]
    cols = COLUMN_MAP[platform]

    raw = None
    last_error = None

    if settings["kind"] == "excel":
        try:
            raw = _read_excel(path)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    else:
        skiprows = settings.get("skiprows", 0)
        try:
            raw = _read_csv_with(path, settings["encoding"], settings["sep"], skiprows)
        except Exception as exc:  # noqa: BLE001
            last_error = exc

        if raw is None or not _columns_satisfied(raw.columns, cols):
            try:
                detected_encoding = _detect_encoding(path)
                detected_sep = _detect_sep(path, detected_encoding, skiprows)
                fallback = _read_csv_with(path, detected_encoding, detected_sep, skiprows)
                if _columns_satisfied(fallback.columns, cols):
                    raw = fallback
            except Exception as exc:  # noqa: BLE001
                if last_error is None:
                    last_error = exc

    if raw is None:
        raise PlatformReadError(platform, path, last_error or ValueError("Could not read file."))

    raw.columns = [str(c).strip() for c in raw.columns]
    resolved = {
        field: _resolve_column(raw.columns, cols[field])
        for field in ("match_key", "spent", "impressions", "clicks")
    }
    missing = [name for name in resolved.values() if name not in raw.columns]
    if missing:
        raise PlatformReadError(
            platform, path, ValueError(f"Expected column(s) not found: {missing}. Found: {list(raw.columns)}")
        )

    exclude_fn = EXCLUSION_RULES.get(platform)
    if exclude_fn is not None:
        keep_mask = ~raw.apply(lambda row: exclude_fn(row, resolved), axis=1)
        raw = raw[keep_mask]

    raw = raw.reset_index(drop=True)

    out = pd.DataFrame(
        {
            "match_key": raw[resolved["match_key"]].astype(str).str.strip(),
            "spent": raw[resolved["spent"]].apply(clean_numeric),
            "impressions": raw[resolved["impressions"]].apply(clean_numeric),
            "clicks": raw[resolved["clicks"]].apply(clean_numeric),
        }
    )
    if cols["status"] and cols["status"] in raw.columns:
        out["status"] = raw[cols["status"]].astype(str).str.strip()
    else:
        out["status"] = None

    # Drop blank/garbage rows (empty match key, or single stray characters
    # from malformed exports) -- these are never real campaigns and would
    # otherwise pollute substring/ID matching downstream.
    out = out[out["match_key"].str.len() >= 2]
    out = out[~out["match_key"].isin(["", "nan", "None"])]
    out = out.reset_index(drop=True)

    if platform == "Taboola":
        out["note"] = out["status"].apply(
            lambda s: f"Delivery Status: {s}"
            if isinstance(s, str) and s.upper().startswith(TABOOLA_NOTE_STATUS_PREFIXES)
            else None
        )
    else:
        out["note"] = None

    out["source_index"] = out.index
    out["platform"] = platform
    return out


def parse_all_reports(report_paths: dict) -> dict:
    """report_paths: {platform_name: file_path}. Returns {platform_name: DataFrame}
    for platforms that were provided. Read errors are collected, not raised, so
    other platforms can still be processed (per the prompt's error-handling rule).
    """
    results = {}
    errors = {}
    for platform, path in report_paths.items():
        if not path:
            continue
        try:
            results[platform] = parse_platform_report(platform, path)
        except PlatformReadError as exc:
            errors[platform] = str(exc)
    return results, errors
