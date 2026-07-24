"""Merge-range helpers for the Pacing sheet's Goal Hits column -- shared by
the local-file (openpyxl) and live-sheet (Google Sheets API metadata) read
paths. Neither this project's original `sheets_writer.py` (Values API only)
nor the ported PaceT code (which only ever read a freshly-rebuilt, unmerged
workbook) had a live-sheet merge lookup -- this is net-new.
"""

from openpyxl.utils import column_index_from_string


def build_merge_map_from_openpyxl(ws, col_letter: str) -> dict:
    """Returns {row: top_row} for every row inside a merge spanning
    `col_letter` on worksheet `ws` (1-indexed rows, inclusive ranges, matching
    openpyxl's own convention). Rows outside any merge simply have no entry --
    callers should treat a missing key as "this row is its own top row".
    """
    col = column_index_from_string(col_letter)
    merge_map = {}
    for merged in ws.merged_cells.ranges:
        if merged.min_col <= col <= merged.max_col:
            for row in range(merged.min_row, merged.max_row + 1):
                merge_map[row] = merged.min_row
    return merge_map


def build_merge_map_from_sheets_api(merges: list, col_0indexed: int) -> dict:
    """merges: the raw GridRange list from sheets_writer.get_merges (0-indexed,
    half-open on the end index per the Sheets API convention). Returns
    {sheet_row: top_sheet_row}, 1-indexed to match PacingRow.sheet_row.
    """
    merge_map = {}
    for merge in merges:
        start_col = merge.get("startColumnIndex", 0)
        end_col = merge.get("endColumnIndex", start_col + 1)
        if not (start_col <= col_0indexed < end_col):
            continue
        start_row = merge.get("startRowIndex", 0)
        end_row = merge.get("endRowIndex", start_row + 1)
        top_sheet_row = start_row + 1
        for r in range(start_row, end_row):
            merge_map[r + 1] = top_sheet_row
    return merge_map


def resolve_top_row(merge_map: dict, row: int) -> int:
    return merge_map.get(row, row)
