"""Optional Pressboard API pull for Goal Hits. Never called unless the user
explicitly supplies an API key and confirms they want it pulled -- this module
is inert until invoked by the CLI with --pressboard-api-key.
"""

import re

import requests

from .config import PRESSBOARD_GOAL_TYPE_METRIC

BASE_URL = "https://api.pressboard.ca/v2/campaigns/{campaign_id}/metrics"

_GOAL_SUFFIX_RE = re.compile(r"_(PV|VV|Imp)\b", re.IGNORECASE)
_CAMPAIGN_ID_RE = re.compile(r"_(\d{5,})_")


def extract_goal_type(campaign_name: str):
    match = _GOAL_SUFFIX_RE.search(campaign_name)
    if not match:
        return None
    return match.group(1).upper() if match.group(1).upper() != "IMP" else "Imp"


def extract_campaign_id(campaign_name: str):
    """Pressboard's campaign_id is a longer numeric ID than the 6-digit
    matching ID (e.g. 8680137 from FB_300327_4098_8680137_TIAA_...). Take the
    longest run of digits in the name.
    """
    candidates = re.findall(r"\d+", campaign_name)
    if not candidates:
        return None
    return max(candidates, key=len)


def fetch_goal_hits(campaign_name: str, api_key: str, timeout: int = 15):
    """Returns the numeric goal-hit value for one campaign, or None if the
    goal type / campaign id can't be determined from the name, or the API
    call fails.
    """
    goal_type = extract_goal_type(campaign_name)
    campaign_id = extract_campaign_id(campaign_name)
    if not goal_type or not campaign_id:
        return None

    metric_key = PRESSBOARD_GOAL_TYPE_METRIC.get(goal_type)
    if not metric_key:
        return None

    url = BASE_URL.format(campaign_id=campaign_id)
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload.get(metric_key)
