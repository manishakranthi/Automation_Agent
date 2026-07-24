"""Anomaly detection per the prompt's ANOMALY FLAGS section."""

from .models import AnomalyFlag, MatchResult

PAUSED_STATUSES = {"paused", "ended", "completed", "removed", "archived"}


def detect_anomalies(match_results: list) -> list:
    flags = []
    for result in match_results:
        if result.matched is None or result.pacing_row is None:
            continue
        row = result.pacing_row
        report = result.matched

        if row.overall_budget is not None and report.spent > row.overall_budget:
            flags.append(
                AnomalyFlag(
                    row.sheet_row,
                    row.campaign_name,
                    "spend_exceeds_budget",
                    f"Spent {report.spent:,.2f} exceeds Overall Budget {row.overall_budget:,.2f}.",
                )
            )

        if row.current_impressions is not None and report.impressions < row.current_impressions:
            flags.append(
                AnomalyFlag(
                    row.sheet_row,
                    row.campaign_name,
                    "impressions_decreased",
                    f"Impressions dropped from {row.current_impressions:,.0f} to {report.impressions:,.0f}.",
                )
            )
        if row.current_clicks is not None and report.clicks < row.current_clicks:
            flags.append(
                AnomalyFlag(
                    row.sheet_row,
                    row.campaign_name,
                    "clicks_decreased",
                    f"Clicks dropped from {row.current_clicks:,.0f} to {report.clicks:,.0f}.",
                )
            )

        if report.spent > 0 and (report.impressions == 0 or report.clicks == 0):
            zero_metric = "impressions" if report.impressions == 0 else "clicks"
            flags.append(
                AnomalyFlag(
                    row.sheet_row,
                    row.campaign_name,
                    "spend_with_zero_delivery",
                    f"Spend of {report.spent:,.2f} recorded but zero {zero_metric}.",
                )
            )

        if report.status and report.status.strip().lower() in PAUSED_STATUSES:
            prior_spent = row.current_spent or 0.0
            if report.spent > prior_spent:
                flags.append(
                    AnomalyFlag(
                        row.sheet_row,
                        row.campaign_name,
                        "spend_accumulating_while_paused",
                        f"Status is '{report.status}' but spend increased from {prior_spent:,.2f} to {report.spent:,.2f}.",
                    )
                )

    return flags
