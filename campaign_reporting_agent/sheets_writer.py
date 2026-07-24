"""Writes Spent / Impressions / Link Clicks back to the live Google Sheet.

Only ever touches columns E, I, J on the named tab, one cell at a time via
batchUpdate, so adjacent formula columns are never at risk.
"""

from .config import WRITABLE_COLUMNS


def build_batch_update_body(sheet_name: str, updates: list) -> dict:
    """updates: list of dicts {sheet_row, spent, impressions, clicks} (any of
    the three metrics may be None to skip that cell -- e.g. an unmatched row
    should never be included here at all).
    """
    data = []
    for update in updates:
        row = update["sheet_row"]
        for key, (_, _, letter) in WRITABLE_COLUMNS.items():
            value = update.get(key)
            if value is None:
                continue
            data.append(
                {
                    "range": f"'{sheet_name}'!{letter}{row}",
                    "values": [[value]],
                }
            )
    return {"valueInputOption": "RAW", "data": data}


def write_updates(spreadsheet_id: str, sheet_name: str, updates: list, credentials_path: str):
    """Applies `updates` to the live sheet via the Google Sheets API v4.
    Requires google-api-python-client + google-auth to be installed and a
    service-account JSON key with edit access to the spreadsheet.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not updates:
        return {"data": []}

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    service = build("sheets", "v4", credentials=creds)

    body = build_batch_update_body(sheet_name, updates)
    return (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )


def read_pacing_values(spreadsheet_id: str, sheet_name: str, credentials_path: str) -> list:
    """Fetches the full Pacing sheet tab as a raw 2D list of values, suitable
    for pacing_sheet.read_pacing_sheet_from_values().
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"'{sheet_name}'")
        .execute()
    )
    return result.get("values", [])


def get_merges(spreadsheet_id: str, sheet_name: str, credentials_path: str) -> list:
    """Fetches merge ranges (GridRange dicts, 0-indexed, half-open on the end
    index) for the given tab only. Used to build a {row: top_row} map via
    merge_utils.build_merge_map_from_sheets_api -- the Values API used
    elsewhere in this module never exposes merge metadata.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    service = build("sheets", "v4", credentials=creds)

    result = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, ranges=[f"'{sheet_name}'"], fields="sheets(merges)")
        .execute()
    )
    sheets = result.get("sheets", [])
    return sheets[0].get("merges", []) if sheets else []


def verify_updates(spreadsheet_id: str, sheet_name: str, updates: list, credentials_path: str) -> list:
    """Re-reads each cell that `updates` targeted and returns the subset that
    doesn't match what was supposed to be written -- callers should treat a
    non-empty result as "the write did not fully take" rather than assuming
    success from the batchUpdate response alone.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not updates:
        return []

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    service = build("sheets", "v4", credentials=creds)

    ranges = []
    expected = []
    for update in updates:
        row = update["sheet_row"]
        for key, (_, _, letter) in WRITABLE_COLUMNS.items():
            value = update.get(key)
            if value is None:
                continue
            ranges.append(f"'{sheet_name}'!{letter}{row}")
            expected.append((f"{letter}{row}", value))

    if not ranges:
        return []

    result = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges)
        .execute()
    )
    mismatches = []
    for (cell, expected_value), value_range in zip(expected, result.get("valueRanges", [])):
        values = value_range.get("values", [[]])
        actual = values[0][0] if values and values[0] else None
        if str(actual) != str(expected_value):
            mismatches.append({"cell": cell, "expected": expected_value, "actual": actual})
    return mismatches
