"""Reads the Pacing sheet: header on spreadsheet row 3, data from row 4.
Columns are addressed strictly by POSITION (0-indexed), per the sheet layout
documented in the prompt, since this tab has a fixed template structure.
"""

import pandas as pd
from openpyxl.utils import get_column_letter

from .config import PACING_COLUMNS_0INDEXED, PACING_DATA_START_ROW_0INDEXED
from .models import GoalHitsGroup, PacingRow
from .utils import clean_numeric


def _to_optional_str(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def read_pacing_sheet_from_file(path: str, sheet_name: str = "Pacing sheet") -> list:
    """Reads a local reference copy (xlsx or csv) of the Paid Social Tracker."""
    if str(path).lower().endswith(".csv"):
        raw = pd.read_csv(path, header=None)
    else:
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, engine="openpyxl")

    return _rows_from_dataframe(raw)


def read_pacing_sheet_from_values(values: list) -> list:
    """Reads pacing sheet rows from a raw 2D list, as returned by the Google
    Sheets API `values.get` call (no header row / dtype inference applied).
    """
    raw = pd.DataFrame(values)
    return _rows_from_dataframe(raw)


def _rows_from_dataframe(raw: pd.DataFrame) -> list:
    cols = PACING_COLUMNS_0INDEXED
    max_col = max(cols.values())
    if raw.shape[1] <= max_col:
        # pad with empty columns so positional access never raises
        for i in range(raw.shape[1], max_col + 1):
            raw[i] = None

    rows = []
    for df_index in range(PACING_DATA_START_ROW_0INDEXED, len(raw)):
        record = raw.iloc[df_index]
        campaign_name = _to_optional_str(record[cols["campaign_name"]])
        if not campaign_name:
            continue  # blank row -- nothing to match/update
        rows.append(
            PacingRow(
                sheet_row=df_index + 1,  # convert 0-indexed df row -> 1-indexed sheet row
                campaign_name=campaign_name,
                overall_budget=_optional_numeric(record[cols["overall_budget"]]),
                current_spent=_optional_numeric(record[cols["spent"]]),
                current_impressions=_optional_numeric(record[cols["impressions"]]),
                current_clicks=_optional_numeric(record[cols["link_clicks"]]),
                start_date=_to_optional_str(record[cols["start_date"]]),
                end_date=_to_optional_str(record[cols["end_date"]]),
                goal=_to_optional_str(record[cols["goal"]]),
                guaranteed_goal=_optional_numeric(record[cols["guaranteed_goal"]]),
            )
        )
    return rows


def _optional_numeric(value):
    text = _to_optional_str(value)
    if text is None:
        return None
    return clean_numeric(text)


def find_duplicate_campaign_names(pacing_rows: list) -> dict:
    """Returns {campaign_name: [sheet_row, ...]} for names appearing on more than
    one Pacing sheet row -- these must be flagged and never auto-updated.
    """
    seen = {}
    for row in pacing_rows:
        seen.setdefault(row.campaign_name, []).append(row.sheet_row)
    return {name: rows for name, rows in seen.items() if len(rows) > 1}


def group_by_goal_hits_merge(pacing_rows: list, merge_map: dict) -> tuple:
    """Groups PacingRows by their Goal Hits merge (merge_map: {sheet_row:
    top_row}, from merge_utils). Rows not covered by merge_map are their own
    single-row group. Returns (groups: list[GoalHitsGroup], data_quality_flags).

    Two cases neither the tracker's own merge structure nor the ported
    PaceT code guard against:
      - a merge's top row has no campaign name while member rows below it
        do -- the group would otherwise silently vanish rather than surface
        as something to look at.
      - member rows within one merge disagree on Goal text (the Goal column
        isn't merged, so a data-entry slip is possible) -- flagged for review,
        but still processed using the top row's own Goal rather than being
        skipped outright (every campaign should get a value or "N/A").
    """
    goal_hits_letter = get_column_letter(PACING_COLUMNS_0INDEXED["goal_hits"] + 1)

    top_rows = {}
    for row in pacing_rows:
        top = merge_map.get(row.sheet_row, row.sheet_row)
        top_rows.setdefault(top, []).append(row)

    groups = []
    flags = []
    for top_row, members in sorted(top_rows.items()):
        members = sorted(members, key=lambda r: r.sheet_row)
        end_row = max(r.sheet_row for r in members)
        top_member = next((r for r in members if r.sheet_row == top_row), None)

        if top_member is None or not top_member.campaign_name:
            other_named = [r for r in members if r.campaign_name]
            if other_named:
                flags.append(
                    {
                        "flag_type": "blank_merge_top_row",
                        "sheet_row": top_row,
                        "detail": f"Goal Hits merge top row {top_row} has no campaign name, "
                        f"but row(s) {[r.sheet_row for r in other_named]} do.",
                    }
                )
            continue

        goals = {(r.goal or "").strip() for r in members if r.goal}
        if len(goals) > 1:
            flags.append(
                {
                    "flag_type": "ambiguous_goal",
                    "sheet_row": top_row,
                    "detail": f"Row(s) {[r.sheet_row for r in members]} disagree on Goal: {sorted(goals)}. "
                    f"Used the top row's own Goal instead of skipping -- worth fixing the sheet's data.",
                }
            )

        goal = (top_member.goal or "").strip() or (next(iter(goals)) if goals else None)
        if not goal:
            continue

        groups.append(
            GoalHitsGroup(
                top_row=top_row,
                end_row=end_row,
                member_rows=[r.sheet_row for r in members],
                campaign_name=top_member.campaign_name,
                member_campaign_names=[r.campaign_name for r in members],
                goal=goal,
                goal_hits_cell=f"{goal_hits_letter}{top_row}",
                guaranteed_goal=top_member.guaranteed_goal,
            )
        )
    return groups, flags
