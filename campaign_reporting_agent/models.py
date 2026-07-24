from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ReportRow:
    """One normalized row from a platform report."""

    platform: str
    match_key: str
    spent: float
    impressions: float
    clicks: float
    status: Optional[str]
    source_index: int  # index within that platform's DataFrame
    note: Optional[str] = None  # e.g. "Delivery Status: REJECTED"


@dataclass
class PacingRow:
    """One row from the Pacing sheet."""

    sheet_row: int  # 1-indexed spreadsheet row number
    campaign_name: str
    overall_budget: Optional[float]
    current_spent: Optional[float]
    current_impressions: Optional[float]
    current_clicks: Optional[float]
    start_date: Optional[str]
    end_date: Optional[str]
    goal: Optional[str] = None
    guaranteed_goal: Optional[float] = None


@dataclass
class GoalHitsGroup:
    """One Goal-Hits merge group -- one or more PacingRows sharing a single
    merged Goal Hits cell (the tracker groups multiple platform executions of
    the same editorial campaign under one cell)."""

    top_row: int  # merge's top-left sheet row -- the only row it's valid to write to
    end_row: int
    member_rows: list
    campaign_name: str  # representative name (top row's), used for Pressboard matching
    member_campaign_names: list
    goal: str
    goal_hits_cell: str  # e.g. "O13"
    guaranteed_goal: Optional[float] = None  # Pacing sheet's own guarantee number, if any


@dataclass
class MatchResult:
    pacing_row: PacingRow
    platform: Optional[str]
    match_type: str  # "exact" | "contained" | "id_match" | "unmatched" | "ambiguous" | "no_report"
    matched: Optional[ReportRow] = None
    reason: Optional[str] = None
    candidates: list = field(default_factory=list)  # populated when ambiguous


@dataclass
class AnomalyFlag:
    sheet_row: int
    campaign_name: str
    flag_type: str
    detail: str
