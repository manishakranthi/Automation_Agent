"""Orchestrates a Goal Hits update pass: given pre-grouped GoalHitsGroups
(see pacing_sheet.group_by_goal_hits_merge), logs into Pressboard/StudioStack
once and fetches a value per group, returning sheet updates + a log -- the
caller (pipeline.py) decides where those updates get written (local file vs.
live sheet).

Ported from PaceT's run_goal_hits_update, split so this module doesn't own
the write target.
"""

import csv
from pathlib import Path

from . import studiostack


def run_goal_hits_update(
    groups: list,
    headless: bool = True,
    limit: int = 0,
    dotenv_path: str = ".env",
    no_data_marker="N/A",
    on_progress=None,
) -> tuple:
    """Returns (updates, log_rows).

    no_data_marker: written to the cell when Pressboard genuinely has no
    usable value (no campaign/goal match, or the Cube query itself errors) --
    every processed campaign gets something rather than being left blank.
    Set to None or "" to leave the existing cell content untouched instead.

    on_progress: optional callable(str), invoked before/after each group so a
    caller (CLI or the Flask UI) can surface live status during what can be a
    multi-minute run.
    """
    notify = on_progress or (lambda _msg: None)
    studiostack.load_dotenv(Path(dotenv_path))

    # Impressions-goal rows are deliberately skipped -- Impressions come
    # from the ad platform reports (Meta/LinkedIn/etc.), not Pressboard, so
    # there's nothing for a Pressboard lookup to add for these specifically.
    groups = [g for g in groups if "impression" not in g.goal.lower()]

    if limit:
        groups = groups[:limit]

    notify("Logging into Pressboard...")
    updates = []
    log_rows = []
    claimed_targets = {}  # (campaign_id, goal_title) -> group description that already got this value
    with studiostack.PressboardClient(headless=headless) as client:
        notify(f"Logged in. Looking up {len(groups)} Goal Hits group(s)...")
        for index, group in enumerate(groups, start=1):
            notify(f"[{index}/{len(groups)}] {group.campaign_name} ({group.goal})")
            try:
                value, meta = client.goal_hits_for_group(group)
            except Exception as exc:  # noqa: BLE001
                value = None
                meta = {"status": "error", "error": str(exc)}

            if value is not None:
                # Two DIFFERENT tracker groups (different merge cells -- not
                # platform siblings under the same cell, which is expected
                # and fine) can independently resolve to the same Pressboard
                # campaign+goal, e.g. two quarters of one campaign when
                # Pressboard only tracks one aggregate delivery number for
                # the whole thing. That's flagged in the log for review, but
                # every campaign still gets its value written -- none are
                # left blank just because another row shares the same target.
                target = (meta.get("pressboard_campaign_id"), meta.get("pressboard_goal_title"))
                prior = claimed_targets.get(target) if target[0] is not None else None
                if prior is not None:
                    meta["status"] = "duplicate_pressboard_target"
                    meta["duplicate_of_row"] = prior
                else:
                    if target[0] is not None:
                        claimed_targets[target] = group.top_row
                updates.append({"sheet_row": group.top_row, "goal_hits": value})
            elif no_data_marker:
                updates.append({"sheet_row": group.top_row, "goal_hits": no_data_marker})

            log_rows.append(
                {
                    "row": group.top_row,
                    "end_row": group.end_row,
                    "goal_hits_cell": group.goal_hits_cell,
                    "campaign_by_platform": group.campaign_name,
                    "tracker_goal": group.goal,
                    "value_written": value if value is not None else (no_data_marker or ""),
                    **meta,
                }
            )
            if value is not None:
                notify(f"  -> {value:,}")
            else:
                notify(f"  -> {meta.get('status', 'no value')}")

    notify(f"Goal Hits lookups done: {len(groups)} group(s) processed.")
    return updates, log_rows


def write_log(log_rows: list, log_path: str) -> None:
    fieldnames = sorted({key for row in log_rows for key in row.keys()})
    with open(log_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(log_rows)
