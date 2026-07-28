"""Pressboard/StudioStack client -- ported from the proven, previously-run
implementation in PaceT/PaceT/Pace-T/actions/MyActions/pace_t/update_goal_hits.py
(real Playwright login + real StudioStack/Cube.js endpoints, validated against
production data). Kept close to the original; the matching weights/threshold
are calibrated against real campaign names, not redesigned here.

Differences from the original, deliberate:
  - The numeric-ID overlap bonus in `score_campaign` used `\\b\\d{5,}\\b`, which
    never matches because `\\b` does not create a boundary next to `_` -- the
    same class of bug just fixed in `utils.extract_numeric_id`. Fixed here to
    a plain `\\d{5,}` so the bonus actually fires.
  - `login()` is wrapped in the same 3-attempt retry already used by
    `api_get`/`cube_get`, so a transient blip during login doesn't abort the
    whole run.
  - `ORG_ID` is read from the `STUDIOSTACK_ORG_ID` env var (falling back to
    the same real value, 1455207) instead of being a hardcoded constant.
  - Operates on this project's `GoalHitsGroup` (campaign_name/goal/etc.)
    instead of the original's `TrackerGroup` -- same shape, different source.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ORG_ID = os.environ.get("STUDIOSTACK_ORG_ID", "1455207")

PLATFORM_PREFIXES = {"fb", "ig", "li", "yt", "tb", "sa", "tw", "x", "tt", "ctv"}

# Below: categorized "skip while hunting for the advertiser name" sets, used
# by advertiser_keyword(). Kept as separate categories (rather than one flat
# list) so a new naming variant can be slotted into the right bucket instead
# of growing an undifferentiated pile of exceptions.

# Episode/series scaffolding words.
EPISODE_NOISE_PARTS = {"episode", "podcast", "sponsored"}

# Creative-format/placement/media-type descriptors that can appear as their
# own token, either instead of or alongside a platform code (e.g.
# "Image_S4_E28_FB_B_297755_..._PwC_...", "SA_DOOH_...", "Carousel Topic
# Takeover_301775_Cadillac...").
FORMAT_NOISE_PARTS = {
    "dooh", "image", "engagement", "audio", "video", "carousel", "landing",
    "static", "native", "retarget", "topic", "takeover", "digital", "copy",
}

# Goal/metric words that occasionally appear as a LEADING token (before the
# campaign ID) in some naming variants, e.g. "IG_Impressions_293406_...",
# "FB_Clicks_293406_...", rather than only as a trailing suffix.
GOAL_WORD_NOISE_PARTS = {
    "impressions", "impression", "clicks", "click", "views", "view", "vv", "pv", "imp",
}

# Generic English function/institutional words that are never themselves a
# useful search keyword -- skipping these lets a compound lead like
# "University of Michigan" or "Custom Content" reduce to the real brand word
# ("Michigan") without a dedicated multi-word rule.
GENERIC_STOPWORDS = {
    "the", "of", "and", "via", "for", "a", "an",
    "university", "college", "institute", "custom", "content", "article", "articles",
    # Generic ad-industry/marketing-process terms that recur across many
    # unrelated Pressboard campaigns -- confirmed real false-positive cases:
    # "campaign" alone matched BASF to "Range Rover ... Brand Campaign"
    # (score 0.664, zero real connection), "sponsorship" alone matched
    # Cadillac to "Eli Lilly ... TAF Sponsorship", "rfi" alone matched
    # Contentful to a Merck campaign. None of these identify WHO the
    # advertiser is, so they must never be the sole basis for a match.
    "campaign", "campaigns", "sponsorship", "sponsor", "sponsored", "brand",
    "branding", "rfi", "rfp", "media", "event", "events",
}

LEADING_NOISE_PARTS = EPISODE_NOISE_PARTS | FORMAT_NOISE_PARTS | GOAL_WORD_NOISE_PARTS | GENERIC_STOPWORDS

# Every word that can appear as real signal in a GOAL comparison ("Video
# Views" vs a Pressboard goal title) -- protected from goal-text
# normalization even though some of these same words (e.g. "video") are
# deliberately treated as noise for campaign-NAME/advertiser-keyword
# purposes above. Without this, e.g. "video" being format-noise for
# advertiser_keyword() would also silently strip it from goal-title
# comparisons, collapsing "Video Views" down to just "views" -- which then
# false-matches any title containing "views", including "Page Views".
GOAL_SIGNAL_WORDS = {
    "page", "video", "link", "click", "clicks", "view", "views",
    "impression", "impressions", "engagement", "engagements", "ctr",
}

# Used by normalize()/score_campaign() for general text similarity (a
# superset of the platform/goal-word noise above, since normalize() operates
# on the whole name/goal text, not just the leading token).
NOISE_TOKENS = PLATFORM_PREFIXES | FORMAT_NOISE_PARTS | GOAL_WORD_NOISE_PARTS | GENERIC_STOPWORDS | {
    "q1", "q2", "q3", "q4",
}

MATCH_SCORE_THRESHOLD = 0.34


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def normalize(text) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[_\-/|]+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b20\d{2}\b|\b\d{2}\b|\b\d+\b", " ", text)
    tokens = [t for t in text.split() if t and t not in NOISE_TOKENS]
    return " ".join(tokens)


# Several of these words ("impressions", "views", "video", "clicks", ...)
# are noise for campaign-NAME matching (some campaign names literally end in
# "_Impressions"/"_VV", or use "Video"/"Engagement" as a leading creative-
# format word) but they're the exact signal needed when comparing GOAL TEXT
# ("Impressions"/"Video Views" vs a Pressboard goal title) -- so goal-title
# comparisons use this separate normalizer, protecting the full
# GOAL_SIGNAL_WORDS set rather than stripping them like `normalize` does.
_GOAL_NOISE_TOKENS = NOISE_TOKENS - GOAL_SIGNAL_WORDS


def _normalize_goal_text(text) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[_\-/|]+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b20\d{2}\b|\b\d{2}\b|\b\d+\b", " ", text)
    tokens = [t for t in text.split() if t and t not in _GOAL_NOISE_TOKENS]
    return " ".join(tokens)


# Creative-format words ("carousel", "video", "audio", "static", ...) are
# noise for campaign-NAME/advertiser-keyword purposes but they're often the
# ONLY thing that distinguishes two stories within one Pressboard campaign
# (e.g. "Social Carousel" vs "Article Promotion on Social" -- confirmed real
# case: both had a goal literally titled "Social Impressions" with the same
# targetGoal, so nothing else discriminates them). Stripping "carousel" here
# made story_match_score blind to the one signal that would correctly pick
# the Carousel-specific story instead of silently landing on a different,
# unrelated one.
_STORY_NOISE_TOKENS = NOISE_TOKENS - FORMAT_NOISE_PARTS


def _normalize_story_text(text) -> str:
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[_\-/|]+", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\b20\d{2}\b|\b\d{2}\b|\b\d+\b", " ", text)
    tokens = [t for t in text.split() if t and t not in _STORY_NOISE_TOKENS]
    return " ".join(tokens)


def advertiser_keyword(campaign_name: str) -> str:
    parts = [p.strip() for p in re.split(r"[_\s]+", campaign_name) if p.strip()]
    for part in parts:
        clean = re.sub(r"[^A-Za-z0-9&-]", "", part)
        low = clean.lower()
        if (
            not clean
            or low in PLATFORM_PREFIXES
            or low in LEADING_NOISE_PARTS
            or re.fullmatch(r"s\d+ep\d+|ep\d+|episode\d*|s\d+|e\d+|\d+s|[a-z]", low)
            or low.isdigit()
        ):
            continue
        if re.fullmatch(r"20\d{2}", low):
            continue
        if low in {"j&j", "jj"}:
            return "Johnson"
        return clean
    return parts[0] if parts else campaign_name


def score_campaign(tracker_name: str, candidate_name: str) -> float:
    tracker_norm = normalize(tracker_name)
    candidate_norm = normalize(candidate_name)
    tracker_tokens = set(tracker_norm.split())
    candidate_tokens = set(candidate_norm.split())
    shared_tokens = tracker_tokens & candidate_tokens

    # Hard gate: two campaigns must share at least one real (non-noise) word
    # to be considered related at all. Without this, an advertiser that
    # simply isn't in Pressboard yet (e.g. "Hermes") can still score above
    # threshold against a totally unrelated campaign purely from the
    # date/quarter bonuses below (confirmed real case: 0 shared words, but
    # both happened to be dated 2026 and both contained "Q3" as a substring
    # -- scored 0.48, comfortably above the 0.34 threshold). Every genuine
    # match observed in production shares at least one real word; this gate
    # costs nothing for those and blocks pure date-coincidence false matches.
    if not shared_tokens:
        return 0.0

    overlap = len(shared_tokens) / max(1, len(tracker_tokens | candidate_tokens))
    ratio = SequenceMatcher(None, tracker_norm, candidate_norm).ratio()

    score = (0.65 * ratio) + (0.35 * overlap)
    row_start = re.search(r"q[1-4]\s*20?(\d{2})|20(\d{2})", tracker_name.lower())
    if row_start:
        year = next((g for g in row_start.groups() if g), "")
        if year and year in candidate_name:
            score += 0.05
    if advertiser_keyword(tracker_name).lower() in candidate_name.lower():
        score += 0.1
    tracker_numbers = {n for n in re.findall(r"\d{5,}", tracker_name)}
    candidate_numbers = {n for n in re.findall(r"\d{5,}", candidate_name)}
    if tracker_numbers & candidate_numbers:
        score += 0.25
    tracker_low = tracker_name.lower()
    candidate_low = candidate_name.lower()
    for quarter in ("q1", "q2", "q3", "q4"):
        if quarter in tracker_low and quarter in candidate_low:
            score += 0.12
            break
    for year in re.findall(r"20(\d{2})", tracker_low):
        if f"20{year}" in candidate_low or f"'{year}" in candidate_low or f" {year}" in candidate_low:
            score += 0.1
            break
    tracker_words = [t for t in tracker_norm.split() if len(t) >= 5]
    phrase_bonus = 0.0
    for left, right in zip(tracker_words, tracker_words[1:]):
        if f"{left} {right}" in candidate_norm:
            phrase_bonus += 0.08
    score += min(0.2, phrase_bonus)
    return score


def _guaranteed_goal_matches(goal: dict, guaranteed_goal) -> bool:
    target = goal.get("targetGoal")
    if target is None or guaranteed_goal in (None, 0):
        return False
    try:
        target = float(target)
        guaranteed_goal = float(guaranteed_goal)
    except (TypeError, ValueError):
        return False
    if guaranteed_goal == 0:
        return False
    return abs(target - guaranteed_goal) / guaranteed_goal < 0.01


def goal_matches(tracker_goal: str, pressboard_goal_title: str) -> bool:
    tracker = _normalize_goal_text(tracker_goal)
    title = _normalize_goal_text(pressboard_goal_title)
    if not tracker or not title:
        return False
    if tracker in title:
        return True
    goal_tokens = set(tracker.split())
    title_tokens = set(title.split())
    if goal_tokens and goal_tokens <= title_tokens:
        return True
    aliases = {
        # Bare "views" was deliberately dropped from this list -- it's a
        # substring of "Video Views" titles too, causing Page Views tracker
        # rows to false-match Video Views goals (and vice versa).
        "page views": ["ga4 views", "google analytics views"],
        "video views": ["video views"],
        "clicks": ["clicks", "link clicks"],
        "link clicks": ["clicks", "link clicks"],
        "ctr": ["ctr"],
        "engagements": ["engagements", "engagement"],
        "impressions": ["impressions", "impression"],
    }
    for key, values in aliases.items():
        if _normalize_goal_text(tracker_goal) == _normalize_goal_text(key):
            return any(_normalize_goal_text(v) in title for v in values)
    return False


def story_match_score(tracker_name: str, story_request: dict) -> float:
    story_text = " ".join(
        str(v or "")
        for v in (
            story_request.get("topic"),
            story_request.get("storyUrl"),
            story_request.get("description"),
        )
    )
    tracker_norm = _normalize_story_text(tracker_name)
    story_norm = _normalize_story_text(story_text)
    if not story_norm:
        return 0.0
    tracker_tokens = set(tracker_norm.split())
    story_tokens = set(story_norm.split())
    overlap = len(tracker_tokens & story_tokens) / max(1, len(tracker_tokens | story_tokens))
    ratio = SequenceMatcher(None, tracker_norm, story_norm).ratio()
    score = (0.45 * ratio) + (0.55 * overlap)

    tracker_low = tracker_name.lower()
    story_low = story_text.lower()
    for pattern in (r"(?:q&a|article|video|vid|custom video)\s*#?\s*(\d+)", r"#\s*(\d+)"):
        tracker_nums = set(re.findall(pattern, tracker_low))
        story_nums = set(re.findall(pattern, story_low))
        if tracker_nums and story_nums and tracker_nums & story_nums:
            score += 0.5
    for number in re.findall(r"\b\d{4}\b", tracker_name):
        if number in story_text:
            score += 0.2
    return score


_PERMANENT_CUBE_ERROR_RE = re.compile(r"not found for path", re.IGNORECASE)


def _is_permanent_cube_error(exc) -> bool:
    """Cube schema errors ("Cube 'CustomMetric' not found for path ...") are
    a missing/broken metric definition on Pressboard's side -- reproducible
    every time, not a transient network blip. Retrying 3x with a 2s sleep
    for these wastes several seconds per occurrence and they're common
    (many campaigns reference these broken custom metrics)."""
    return bool(_PERMANENT_CUBE_ERROR_RE.search(str(exc)))


def strip_nulls(value):
    if isinstance(value, dict):
        return {k: strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_nulls(v) for v in value]
    return value


class PressboardClient:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None
        self.api_token = ""
        self.cube_token = ""
        self.summary_cache: dict = {}
        self.goal_cache: dict = {}
        self.cube_cache: dict = {}
        self.all_campaigns_cache = None
        # Guards the caches above -- goal_hits.py fans lookups out across
        # threads, so two groups can race to fill the same cache entry.
        self._cache_lock = threading.Lock()
        # Plain HTTP session for the actual data calls (api_get/cube_get):
        # these are REST calls that only need the bearer token from login,
        # not a browser context, and going through requests instead of
        # page.evaluate() drops the per-call browser IPC round-trip and lets
        # concurrent lookups actually run in parallel (page.evaluate() serializes
        # on the single Playwright page). Pool sized for the thread-pool
        # concurrency goal_hits.py uses.
        self.http = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.http.mount("https://", adapter)

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self._launch_browser()
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self._login_with_retry()
        return self

    def _launch_browser(self):
        launch_errors = []
        launch_attempts = [
            ("bundled chromium", lambda: self.playwright.chromium.launch(headless=self.headless)),
            ("Microsoft Edge", lambda: self.playwright.chromium.launch(channel="msedge", headless=self.headless)),
            ("Google Chrome", lambda: self.playwright.chromium.launch(channel="chrome", headless=self.headless)),
        ]
        for label, launcher in launch_attempts:
            try:
                return launcher()
            except PlaywrightError as exc:
                launch_errors.append(f"{label}: {exc}")

        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
                text=True,
                capture_output=True,
                timeout=300,
            )
            return self.playwright.chromium.launch(headless=self.headless)
        except Exception as exc:  # noqa: BLE001
            launch_errors.append(f"playwright install chromium: {exc}")

        raise RuntimeError(
            "Playwright could not launch a browser. Tried bundled Chromium, Microsoft Edge, "
            "Google Chrome, and automatic `python -m playwright install chromium`. "
            + " | ".join(launch_errors)
        )

    def __exit__(self, exc_type, exc, tb):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def _login_with_retry(self) -> None:
        last_exc = None
        for _ in range(3):
            try:
                self.login()
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(2)
        raise last_exc

    def login(self) -> None:
        required = ["PRESSBOARD_LOGIN_URL", "PRESSBOARD_USERNAME", "PRESSBOARD_PASSWORD"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")
        self.page.goto(os.environ["PRESSBOARD_LOGIN_URL"], wait_until="domcontentloaded", timeout=60_000)
        self.page.locator("#email").fill(os.environ["PRESSBOARD_USERNAME"])
        self.page.locator("#password").fill(os.environ["PRESSBOARD_PASSWORD"])
        self.page.locator('button[type="submit"], input[type="submit"]').first.click()
        self.page.wait_for_load_state("networkidle", timeout=60_000)
        self.page.wait_for_timeout(1_500)
        user = self.page.evaluate("() => JSON.parse(localStorage.getItem('pressboarduser') || '{}')")
        self.api_token = user.get("access_token", "")
        if not self.api_token:
            raise RuntimeError("Pressboard login succeeded visually, but no API token was found.")

    def api_get(self, url: str) -> Any:
        last_exc = None
        for attempt in range(3):
            try:
                resp = self.http.get(url, headers={"Authorization": "Bearer " + self.api_token}, timeout=30)
                if not resp.ok:
                    raise RuntimeError(f"{resp.status_code} {url} {resp.text[:500]}")
                return resp.json() if resp.text else None
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                # A 4xx (bad/missing ID, auth) won't fix itself on retry;
                # only 5xx/network errors are worth the 2s-sleep retry.
                if re.match(r"^4\d\d\b", str(exc)) or attempt == 2:
                    break
                time.sleep(2)
        raise last_exc

    def cube_get(self, query: dict) -> Any:
        if not self.cube_token:
            self.cube_token = self.api_get(f"https://api.studiostack.com/{ORG_ID}/CubeJsAuth")["token"]
        clean_query = strip_nulls(query)
        cache_key = json.dumps(clean_query, sort_keys=True)
        with self._cache_lock:
            cached = self.cube_cache.get(cache_key)
        if cached is not None:
            if isinstance(cached, Exception):
                raise cached
            return cached

        last_exc = None
        for attempt in range(3):
            try:
                resp = self.http.get(
                    "https://analytics.studiostack.com/cubejs-api/v1/load",
                    params={"query": json.dumps(clean_query), "queryType": "multi"},
                    headers={"Authorization": self.cube_token},
                    timeout=30,
                )
                if not resp.ok:
                    raise RuntimeError(f"{resp.status_code} cube {resp.text[:500]}")
                result = resp.json()
                with self._cache_lock:
                    self.cube_cache[cache_key] = result
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_permanent_cube_error(exc) or attempt == 2:
                    break
                time.sleep(2)
        with self._cache_lock:
            self.cube_cache[cache_key] = last_exc
        raise last_exc

    def all_campaigns(self) -> list:
        """Every campaign in the org, fetched once and cached for the whole
        run -- confirmed to return the complete list (not paginated/capped),
        so every tracker row can be scored locally against the full set
        instead of depending on Search/Suggestions' fragile requirement that
        the query align with the start of Pressboard's own campaign name."""
        with self._cache_lock:
            cached = self.all_campaigns_cache
        if cached is None:
            cached = self.api_get(f"https://api.studiostack.com/{ORG_ID}/Campaigns")
            with self._cache_lock:
                self.all_campaigns_cache = cached
        return cached

    def campaign_summary(self, campaign_id: int) -> dict:
        with self._cache_lock:
            cached = self.summary_cache.get(campaign_id)
        if cached is None:
            cached = self.api_get(f"https://api.studiostack.com/{ORG_ID}/CampaignSummaries/{campaign_id}")
            with self._cache_lock:
                self.summary_cache[campaign_id] = cached
        return cached

    def story_goals(self, campaign_id: int, story_id: int) -> list:
        key = (campaign_id, story_id)
        with self._cache_lock:
            cached = self.goal_cache.get(key)
        if cached is None:
            cached = self.api_get(
                f"https://api.studiostack.com/{ORG_ID}/Campaigns/{campaign_id}/StoryRequests/{story_id}/Goals"
            )
            with self._cache_lock:
                self.goal_cache[key] = cached
        return cached

    def best_campaign(self, group) -> tuple:
        keyword = advertiser_keyword(group.campaign_name)

        # Score against every campaign in the org directly (fetched once,
        # cached for the whole run) instead of depending on Search/Suggestions
        # -- that endpoint only matches a query that aligns with the literal
        # start of Pressboard's own campaign name, which our tracker's naming
        # convention frequently diverges from after just the advertiser word
        # (confirmed: e.g. "TeamViewer 2026 Express..." vs the real name
        # "TeamViewer Q2 2026 (Part 2) 298542"). Scoring locally against the
        # full list sidesteps that entirely, and it's one bulk call for the
        # whole run instead of 2+ search calls per group.
        campaigns = self.all_campaigns()
        if not campaigns:
            return None, 0.0, keyword
        scored = [(score_campaign(group.campaign_name, c.get("name", "")), c) for c in campaigns]
        scored.sort(key=lambda item: item[0], reverse=True)
        score, campaign = scored[0]
        if score < MATCH_SCORE_THRESHOLD:
            return None, score, keyword
        return campaign, score, keyword

    def goal_hits_for_group(self, group) -> tuple:
        campaign, score, keyword = self.best_campaign(group)
        meta = {"keyword": keyword, "match_score": round(score, 3)}
        if not campaign:
            meta["status"] = "no_campaign_match"
            return None, meta

        campaign_id = int(campaign["entityId"])
        meta["pressboard_campaign_id"] = campaign_id
        meta["pressboard_campaign_name"] = campaign.get("name", "")
        summary = self.campaign_summary(campaign_id)
        stories = summary.get("stories") or []

        # Group matched goals BY STORY, then commit to the single best-scoring
        # story and only try goals within it. A campaign with multiple
        # creatives (e.g. one story per talent/feature) can have several
        # stories that all carry a same-titled goal ("Video Views - Delivery
        # vs. Guarantee") -- if the best story's metric is broken on
        # Pressboard's side, silently falling back to a DIFFERENT story's
        # (unrelated) number is worse than reporting no data: it reports a
        # real-looking but wrong figure for the wrong creative.
        matches_by_story = {}
        for story in stories:
            story_request = story.get("storyRequest") or {}
            story_id = story_request.get("entityId")
            if not story_id:
                continue
            story_score = story_match_score(group.campaign_name, story_request)
            goals = [g for g in self.story_goals(campaign_id, int(story_id)) if goal_matches(group.goal, g.get("title", ""))]
            if goals:
                matches_by_story[story_id] = (story_score, goals)

        if not matches_by_story:
            meta["status"] = "no_goal_match"
            return None, meta

        # Pressboard's own guarantee number for a goal ("targetGoal", the
        # denominator in the UI's "delivered / guarantee" display) is a much
        # stronger disambiguator than text similarity when one campaign has
        # several near-identically-named stories (e.g. one per creative/
        # talent) with DIFFERENT guarantees -- if the tracker's own
        # Guaranteed Goal figure uniquely matches one story's targetGoal,
        # trust that over story_match_score. But standardized products (e.g.
        # "Q&A Article" slots) often share the SAME guarantee across several
        # stories -- if more than one story matches, that's not actually
        # disambiguating anything, so fall through to story_match_score
        # (which already has an explicit "#1"/"#2"/"#3" numbered-match bonus
        # built in for exactly this case).
        guaranteed_story_ids = [
            story_id
            for story_id, (_, goals) in matches_by_story.items()
            if group.guaranteed_goal and any(_guaranteed_goal_matches(g, group.guaranteed_goal) for g in goals)
        ]

        if len(guaranteed_story_ids) == 1:
            story_score, candidate_goals = matches_by_story[guaranteed_story_ids[0]]
            meta["matched_via"] = "guaranteed_goal"
        else:
            _, (story_score, candidate_goals) = max(matches_by_story.items(), key=lambda item: item[1][0])

        cube_errors = []
        for goal in sorted(candidate_goals, key=lambda g: g.get("order", 999)):
            meta["pressboard_goal_title"] = goal.get("title", "")
            meta["story_match_score"] = round(story_score, 3)
            time_dimensions = goal.get("timeDimensions") or []
            filters = (goal.get("filters") or []) + (goal.get("otherFilters") or [])
            try:
                value = self._resolve_goal_value(goal, time_dimensions, filters)
            except Exception as exc:  # noqa: BLE001
                cube_errors.append(f"{goal.get('measure')}: {exc}")
                continue
            if value is None:
                continue
            meta["status"] = "updated"
            meta["pressboard_goal_measure"] = goal.get("measure", "")
            if cube_errors:
                meta["cube_fallback_errors"] = " | ".join(cube_errors)
            return int(value) if float(value).is_integer() else value, meta
        meta["status"] = "no_cube_value"
        if cube_errors:
            meta["cube_errors"] = " | ".join(cube_errors)
        return None, meta

    def _query_measure(self, measure: str, time_dimensions, filters):
        """A single real Cube measure -- returns its numeric value, or None
        if there's simply no data for it (not an error)."""
        query = {"measures": [measure], "timeDimensions": time_dimensions, "filters": filters}
        data = self.cube_get(query)
        rows = (((data.get("results") or [{}])[0]).get("data") or [])
        if not rows:
            return None
        raw_value = rows[0].get(measure)
        if raw_value in (None, ""):
            return None
        return float(raw_value)

    def _resolve_goal_value(self, goal: dict, time_dimensions, filters):
        """Resolves a goal's delivered value. "CustomMetric.NN" is never a
        real Cube measure (querying it directly is exactly what produces the
        "Cube 'CustomMetric' not found for path" errors) -- it's Pressboard's
        own composite metric, defined by a `customMetric` numerator (list of
        real measures to sum) and an optional denominator (a second sum, for
        a ratio metric like a CTR). The underlying measures often live in
        different, unrelated Cubes (Google/Facebook/LinkedIn/TikTok/...) that
        can't be joined in a single multi-measure query, so each is queried
        separately and combined here.
        """
        measure = goal["measure"]
        custom_metric = goal.get("customMetric")
        if not (measure.startswith("CustomMetric.") and custom_metric):
            return self._query_measure(measure, time_dimensions, filters)

        def _sum(measures):
            measures = measures or []
            if not measures:
                return None
            # Each measure is its own several-second Cube.js query (confirmed:
            # some customMetrics sum up to 9 of these), and they're independent
            # of each other -- querying them one at a time made a single goal's
            # lookup dominate the whole run. Run them concurrently instead.
            with ThreadPoolExecutor(max_workers=min(8, len(measures))) as pool:
                values = list(pool.map(lambda m: self._query_measure(m, time_dimensions, filters), measures))
            found_values = [v for v in values if v is not None]
            return sum(found_values) if found_values else None

        numerator_total = _sum(custom_metric.get("numerator"))
        if numerator_total is None:
            return None
        denominator = custom_metric.get("denominator")
        if not denominator:
            return numerator_total
        denominator_total = _sum(denominator)
        if not denominator_total:
            return None
        return numerator_total / denominator_total
