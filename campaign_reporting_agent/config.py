"""Static mapping tables driven directly by Campaign_Reporting_Agent_Prompt.md."""

import re

# --- Column mapping per platform -------------------------------------------------
# match_key: the column used to identify/match a campaign against the Pacing sheet
# spent / impressions / clicks: normalized metric columns
# status: column used for exclusion rules / anomaly detection (may be None)
COLUMN_MAP = {
    "Meta": {
        "match_key": "Campaign name",
        "spent": "Amount spent (USD)",
        "impressions": "Impressions",
        "clicks": "Link clicks",
        "status": "Delivery status",
    },
    "LinkedIn": {
        "match_key": "Ad Set Name",
        "spent": "Total Spent",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "status": "Ad Set Status",
    },
    "Google Ads": {
        "match_key": "Campaign",
        "spent": "Cost",
        "impressions": "Impr.",
        "clicks": "Clicks",
        "status": "Campaign status",
    },
    "Taboola": {
        "match_key": "Campaign Name",
        "spent": "Spent",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "status": "Delivery Status",
    },
    "StackAdapt": {
        # StackAdapt exports two templates: a per-campaign report ("Campaign
        # Name") and an advertiser/campaign-group rollup ("Campaign Group").
        # Try the per-campaign name first, falling back to the rollup column.
        "match_key": ("Campaign Name", "Campaign Group"),
        "spent": "Media Cost",
        "impressions": "Impressions",
        "clicks": "Clicks",
        "status": "Status",
    },
}

# --- File read settings per platform ----------------------------------------------
READ_SETTINGS = {
    "Meta": {"kind": "excel"},
    "LinkedIn": {"kind": "csv", "encoding": "utf-16", "sep": "\t", "skiprows": 5},
    "Google Ads": {"kind": "csv", "encoding": "utf-16", "sep": "\t", "skiprows": 2},
    "Taboola": {"kind": "csv", "encoding": "utf-8-sig", "sep": ","},
    "StackAdapt": {"kind": "excel"},
}

# --- Pacing-sheet prefix -> platform routing --------------------------------------
# Evaluated top-to-bottom; first regex that matches wins. More specific patterns
# are listed before the generic prefixes they would otherwise collide with.
PREFIX_RULES = [
    (re.compile(r"^SA_DOOH_", re.IGNORECASE), "StackAdapt"),
    (re.compile(r"^S2Ep.*?_YT_", re.IGNORECASE), "Google Ads"),
    (re.compile(r"^S2Ep.*?_Podcast_FB_", re.IGNORECASE), "Meta"),
    (re.compile(r"^S2Ep.*?_Podcast_IG_", re.IGNORECASE), "Meta"),
    (re.compile(r"^S2Ep.*?_Podcast_LI_", re.IGNORECASE), "LinkedIn"),
    (re.compile(r"^Engagement_Audio_S\d+_FB_", re.IGNORECASE), "Meta"),
    (re.compile(r"^Audio_S\d+_E\d+_FB_", re.IGNORECASE), "Meta"),
    (re.compile(r"^Audio_S\d+_E\d+_(?!FB_|IG_|LI_)", re.IGNORECASE), "LinkedIn"),
    (re.compile(r"^Image_S\d+_E\d+_FB_", re.IGNORECASE), "Meta"),
    (re.compile(r"^Image_S\d+_E\d+_(?!FB_|IG_)", re.IGNORECASE), "Meta"),
    (re.compile(r"^FB_", re.IGNORECASE), "Meta"),
    (re.compile(r"^IG_", re.IGNORECASE), "Meta"),
    (re.compile(r"^LI_", re.IGNORECASE), "LinkedIn"),
    (re.compile(r"^(TB_|Tb_)", re.IGNORECASE), "Taboola"),
    (re.compile(r"^SA_", re.IGNORECASE), "StackAdapt"),
    (re.compile(r"^CTV_", re.IGNORECASE), "StackAdapt"),
    (re.compile(r"^GA_", re.IGNORECASE), "Google Ads"),
    (re.compile(r"^YT_", re.IGNORECASE), "Google Ads"),
]

# --- Per-platform row exclusion rules (applied while parsing) --------------------
# Returns True if a row should be DROPPED from the normalized report.
def google_ads_exclude(row, resolved):
    status = str(row.get("Campaign status", "")).strip().lower()
    # Google Ads exports append summary/rollup rows (e.g. "Total: Campaigns",
    # "Total: Account", "Total: Video", "Total: Search") -- these aren't real
    # campaigns and must never be treated as match candidates.
    return status == "removed" or status.startswith("total:")


def stackadapt_exclude(row, resolved):
    status = str(row.get("Status", "")).strip().lower()
    name = str(row.get(resolved["match_key"], "")).strip().lower()
    return status == "paused" or name == "test"


# Taboola rows with REJECTED / PENDING_APPROVAL are NOT excluded -- they are kept
# but annotated, per the prompt ("may still carry zeroes -- include them but note
# the status"). Real exports render REJECTED as a free-text string, e.g.
# "REJECTED (Description does not relate to the landing page.)", so this is
# matched by prefix, not equality.
TABOOLA_NOTE_STATUS_PREFIXES = ("REJECTED", "PENDING_APPROVAL")

EXCLUSION_RULES = {
    "Google Ads": google_ads_exclude,
    "StackAdapt": stackadapt_exclude,
}

# --- Pacing sheet layout -----------------------------------------------------------
PACING_HEADER_ROW_0INDEXED = 2  # row 3 (1-indexed)
PACING_DATA_START_ROW_0INDEXED = 3  # row 4 (1-indexed)

PACING_COLUMNS_0INDEXED = {
    "campaign_name": 2,
    "overall_budget": 3,
    "spent": 4,
    "goal": 5,
    "guaranteed_goal": 6,
    "total_spent": 7,
    "impressions": 8,
    "link_clicks": 9,
    "ctr": 10,
    "start_date": 11,
    "end_date": 12,
    "pct_pacing": 13,
    "goal_hits": 14,
    "safety_net": 15,
    "outstanding_goal": 16,
    "notes": 17,
    "cpm": 18,
    "cpc": 19,
}

# Columns this agent is permitted to write to (0-indexed) and their A1 letters.
WRITABLE_COLUMNS = {
    "spent": ("spent", 4, "E"),
    "impressions": ("impressions", 8, "I"),
    "clicks": ("link_clicks", 9, "J"),
    "goal_hits": ("goal_hits", 14, "O"),
}

PRESSBOARD_GOAL_TYPE_METRIC = {
    "PV": "page_views",
    "VV": "video_views",
    "Imp": "impressions",
}
