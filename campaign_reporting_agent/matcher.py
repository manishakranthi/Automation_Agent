"""Platform routing + cascading campaign-name matching, per the prompt's
CAMPAIGN MATCHING LOGIC section. Deterministic and conservative: anything
ambiguous or inconsistent is flagged rather than guessed.
"""

import pandas as pd

from .config import PREFIX_RULES
from .models import MatchResult, ReportRow
from .utils import extract_numeric_id, extract_quarter_year_tokens, leading_token, normalize_for_compare


def platform_for_campaign(campaign_name: str):
    """Returns the platform name a Pacing-sheet campaign should be matched
    against, based on its prefix, or None if no rule matches.
    """
    for pattern, platform in PREFIX_RULES:
        if pattern.match(campaign_name):
            return platform
    return None


def _row_from_series(platform: str, series: pd.Series) -> ReportRow:
    return ReportRow(
        platform=platform,
        match_key=series["match_key"],
        spent=series["spent"],
        impressions=series["impressions"],
        clicks=series["clicks"],
        status=series["status"],
        source_index=int(series["source_index"]),
        note=series["note"],
    )


def find_match(pacing_name: str, platform: str, report_df: pd.DataFrame) -> MatchResult:
    pacing_norm = normalize_for_compare(pacing_name)

    # --- Step 1: exact match --------------------------------------------------
    exact_mask = report_df["match_key"].apply(normalize_for_compare) == pacing_norm
    exact_rows = report_df[exact_mask]
    if len(exact_rows) == 1:
        return MatchResult(None, platform, "exact", matched=_row_from_series(platform, exact_rows.iloc[0]))
    if len(exact_rows) > 1:
        return MatchResult(
            None,
            platform,
            "ambiguous",
            reason="Multiple exact matches in the same platform report.",
            candidates=[_row_from_series(platform, r) for _, r in exact_rows.iterrows()],
        )

    # --- Step 2: contained match (substring, case-insensitive) ---------------
    # Guard against garbage/placeholder report rows (e.g. a stray "A" or "-")
    # trivially substring-matching almost every real campaign name.
    MIN_CONTAINED_LEN = 6

    def _contained(report_name: str) -> bool:
        rn = normalize_for_compare(report_name)
        if min(len(rn), len(pacing_norm)) < MIN_CONTAINED_LEN:
            return False
        return rn in pacing_norm or pacing_norm in rn

    contained_mask = report_df["match_key"].apply(_contained)
    contained_rows = report_df[contained_mask]
    if len(contained_rows) == 1:
        return MatchResult(None, platform, "contained", matched=_row_from_series(platform, contained_rows.iloc[0]))
    if len(contained_rows) > 1:
        return MatchResult(
            None,
            platform,
            "ambiguous",
            reason="Multiple contained-substring matches in the same platform report.",
            candidates=[_row_from_series(platform, r) for _, r in contained_rows.iterrows()],
        )

    # --- Step 3: numeric ID match, gated by consistency checks ----------------
    pacing_id = extract_numeric_id(pacing_name)
    if pacing_id:
        id_mask = report_df["match_key"].apply(lambda n: extract_numeric_id(n) == pacing_id)
        id_rows = report_df[id_mask]

        # For Meta, never let an FB_ pacing row match an IG_ report row or vice
        # versa -- explicit rule in the prompt.
        if platform == "Meta":
            pacing_prefix = leading_token(pacing_name)
            id_rows = id_rows[id_rows["match_key"].apply(leading_token) == pacing_prefix]

        # Reject candidates whose quarter/year tokens conflict with the pacing
        # name's tokens (e.g. "FY26 Q2+Q3" vs "2026 Q1" -- different campaigns
        # per the prompt's own example) when both sides carry such tokens.
        pacing_tokens = extract_quarter_year_tokens(pacing_name)

        def _consistent(report_name: str) -> bool:
            report_tokens = extract_quarter_year_tokens(report_name)
            if not pacing_tokens or not report_tokens:
                return True
            return bool(pacing_tokens & report_tokens)

        id_rows = id_rows[id_rows["match_key"].apply(_consistent)]

        if len(id_rows) == 1:
            return MatchResult(None, platform, "id_match", matched=_row_from_series(platform, id_rows.iloc[0]))
        if len(id_rows) > 1:
            return MatchResult(
                None,
                platform,
                "ambiguous",
                reason=f"Multiple rows share numeric ID {pacing_id} after prefix/quarter filtering.",
                candidates=[_row_from_series(platform, r) for _, r in id_rows.iterrows()],
            )

    # --- Step 4: no match ------------------------------------------------------
    return MatchResult(None, platform, "unmatched", reason="No exact, contained, or numeric-ID match found in the platform report.")


def match_all(pacing_rows: list, reports: dict) -> list:
    """pacing_rows: list[PacingRow]. reports: {platform: DataFrame}.
    Returns list[MatchResult] with `.pacing_row` populated.
    """
    results = []
    for row in pacing_rows:
        platform = platform_for_campaign(row.campaign_name)
        if platform is None:
            results.append(
                MatchResult(row, None, "unmatched", reason="Could not determine platform from campaign-name prefix.")
            )
            continue
        report_df = reports.get(platform)
        if report_df is None:
            results.append(
                MatchResult(row, platform, "no_report", reason=f"No {platform} report was uploaded this run.")
            )
            continue
        result = find_match(row.campaign_name, platform, report_df)
        result.pacing_row = row
        results.append(result)
    return results
