import re
from typing import Optional

_NUMERIC_STRIP_RE = re.compile(r"[^0-9.\-]")


def clean_numeric(value) -> float:
    """Parse a metric that may be a float, int, or a formatted string like
    '$1,247.61', '40,449', or '12.5%' into a plain float. Blank/missing -> 0.0.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if value != value:  # NaN
            return 0.0
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace(",", "").replace("$", "").replace("%", "")
    text = _NUMERIC_STRIP_RE.sub("", text)
    if not text or text in ("-", "."):
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


_ID_RE = re.compile(r"\d+")
_QUARTER_YEAR_RE = re.compile(r"FY\d{2}|20\d{2}|Q[1-4]", re.IGNORECASE)
_LEADING_TOKEN_RE = re.compile(r"^([A-Za-z0-9]+)_")


def extract_numeric_id(name: str) -> Optional[str]:
    """Returns the longest run of digits in the name -- e.g. for
    'FB_309517_4116_8746302_...' this is '8746302', the campaign-specific ID,
    not the shorter, shared account-level ID ('309517') that a leftmost/fixed-
    width match would grab instead. Ties break toward the last-occurring run,
    since campaign-specific IDs conventionally sit further right than
    account/product IDs in this naming convention.
    """
    if not name:
        return None
    candidates = _ID_RE.findall(name)
    if not candidates:
        return None
    return max(reversed(candidates), key=len)


def extract_quarter_year_tokens(name: str) -> set:
    if not name:
        return set()
    return {tok.upper() for tok in _QUARTER_YEAR_RE.findall(name)}


def leading_token(name: str) -> Optional[str]:
    if not name:
        return None
    match = _LEADING_TOKEN_RE.match(name)
    return match.group(1).upper() if match else None


def normalize_for_compare(name: str) -> str:
    return (name or "").strip().lower()
