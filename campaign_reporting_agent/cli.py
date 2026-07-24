"""Campaign Reporting Agent CLI.

Reads platform export files (Meta/LinkedIn/Google Ads/Taboola/StackAdapt),
matches them against a Pacing sheet, and produces:
  - updated_tracker_data.json
  - match_summary.json
  - anomaly_flags.json
  - new_campaigns.json     (report rows with no matching Pacing row)
  - duplicate_campaigns.json
  - read_errors.json       (platform files that failed to parse)

Optionally writes the matched Spent/Impressions/Clicks back to a live Google
Sheet (--write-to-sheet), updates Goal Hits from Pressboard/StudioStack
(--update-goal-hits), and/or pulls Pressboard Goal Hits via the legacy
Bearer-token API for reference only (--pressboard-api-key); none of these are
destructive without their explicit flag.

This is a thin argparse adapter -- the actual pipeline (shared with the Flask
UI in webui.py) lives in pipeline.py.
"""

import argparse

from .pipeline import PipelineOptions, run_pipeline


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Campaign Reporting Agent: reconcile platform reports into the Paid Social Tracker's Pacing sheet."
    )
    parser.add_argument("--meta", help="Path to Meta export (.xlsx)")
    parser.add_argument("--linkedin", help="Path to LinkedIn export (.csv, UTF-16, tab-separated)")
    parser.add_argument("--google-ads", dest="google_ads", help="Path to Google Ads export (.csv, UTF-16, tab-separated)")
    parser.add_argument("--taboola", help="Path to Taboola export (.csv, UTF-8-BOM)")
    parser.add_argument("--stackadapt", help="Path to StackAdapt export (.xlsx)")

    parser.add_argument("--pacing-sheet", dest="pacing_sheet", help="Path to a local reference copy of the tracker (.xlsx/.csv)")
    parser.add_argument("--pacing-tab", dest="pacing_tab", default="Pacing sheet", help="Tab name within the pacing-sheet workbook")

    parser.add_argument("--spreadsheet-id", dest="spreadsheet_id", help="Google Sheet ID of the live Paid Social Tracker")
    parser.add_argument("--sheets-credentials", dest="sheets_credentials", help="Path to a Google service-account JSON key")
    parser.add_argument(
        "--write-to-sheet",
        action="store_true",
        help="Actually write matched Spent/Impressions/Clicks back to the live Google Sheet (requires --spreadsheet-id and --sheets-credentials). Without this flag, nothing is written -- output is local files only.",
    )

    parser.add_argument(
        "--update-goal-hits",
        dest="update_goal_hits",
        action="store_true",
        help="Log into Pressboard/StudioStack (via .env credentials) and update the Goal Hits column for every eligible campaign, respecting merged cells.",
    )
    parser.add_argument("--goal-hits-headless", dest="goal_hits_headless", action="store_true", default=True, help="Run the Pressboard browser session headless (default).")
    parser.add_argument("--goal-hits-headful", dest="goal_hits_headless", action="store_false", help="Run the Pressboard browser session visibly, for debugging.")
    parser.add_argument("--goal-hits-limit", dest="goal_hits_limit", type=int, default=0, help="Optional limit on the number of Goal Hits groups processed, for test runs.")
    parser.add_argument("--goal-hits-no-data-marker", dest="goal_hits_no_data_marker", default="N/A", help="Written to a Goal Hits cell when Pressboard has no usable value for that campaign, so nothing is left blank. Pass an empty string to leave such cells untouched instead.")
    parser.add_argument("--dotenv-path", dest="dotenv_path", default=".env", help="Path to the .env file holding PRESSBOARD_LOGIN_URL/USERNAME/PASSWORD")

    parser.add_argument("--pressboard-api-key", dest="pressboard_api_key", help="Legacy Pressboard Bearer-token API key; if supplied, Goal Hits are fetched for reference and written to goal_hits_report.json only (never to the live sheet). Superseded by --update-goal-hits.")

    parser.add_argument("--output-dir", dest="output_dir", default="./output", help="Directory to write report files to")
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    options = PipelineOptions(**vars(args))

    run_pipeline(options, on_progress=print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
