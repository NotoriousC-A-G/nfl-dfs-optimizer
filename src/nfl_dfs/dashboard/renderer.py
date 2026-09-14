"""Weekly dashboard renderer (PRD Section 8's deliverables, presentation-only round).

Modeled directly on the MLB DFS optimizer's dashboard approach (`mlb_dfs/dashboard_v2/
renderer.py`): no frontend framework, no build step, no external CDN -- one Python module
produces a single self-contained static HTML file (inline CSS, vanilla JS for tab-switching and
a client-side search filter only) that gets written to disk and opened directly in a browser.
Unlike the MLB reference (which ships a JSON data blob and builds table rows with JS at load
time), this module renders every table server-side in Python -- the NFL pipeline's per-run
payload (3 lineups, one exposure report, a player pool) is small and entirely known at render
time, so there is no reuse-across-snapshots need driving the MLB build's JS-rendering choice, and
server-side rendering is more directly testable (assert real values appear in the returned HTML
string) than asserting on an embedded JSON blob a browser would need to execute JS to consume.

**This round is presentation only** -- every value rendered here is read as-is from `Lineup`,
`WeeklyOutput` (`output/exposure.py`, `output/rationale.py`), and `PlayerDetailRecord`
(`composition/player_detail.py`). No new computation, no new data source, no touching any
upstream module.

## Structure -- four tabs

1. **Lineups** -- each of the up-to-3 generated lineups as a roster table (slot, player, team,
   salary, projection), total salary/projected points, and the real `LineupRationale.text`
   (`output/rationale.py`'s `pivot_to`-derived text, or its documented fallback prose) displayed
   directly under the roster table, not buried in a drawer or tooltip -- the task brief's explicit
   ask ("displayed prominently... not buried").
2. **Exposure** -- `ExposureReport.players`/`.stacks` (`output/exposure.py`) as plain, already
   server-sorted HTML tables. No JS sorting is added -- the report is already sorted descending by
   count (its own `render_text`'s ordering), and the task brief only asks for "genuinely readable,"
   not interactive re-sorting.
3. **Player Detail** -- one row per `PlayerDetailRecord`, with a client-side name/team search
   filter (vanilla JS, no data blob) since the caller-supplied player list can be much larger than
   3 lineups' worth of players (task brief: "a browsable table").
4. **Slate Overview** -- one row per GAME (not per team), modeled on the MLB sister project's
   "Slate Overview" tab: matchup + kickoff, the Vegas spread/total and each team's implied total
   (`ingestion/odds_api.py`), weather conditions (`ingestion/weather.py`), and both teams'
   `GameEnvironmentScore` composite (`game_environment/score.py`) plus any injury-uncertainty
   flag -- Chris's own framing: "a summary of the game environments - line, weather, which games
   have the highest conviction." Server-sorted by a "conviction" heuristic (see
   `_conviction_sort_key`'s docstring for the full reasoning), no JS re-sorting, same precedent as
   the Exposure tab above. Consumes a new, small, purely-typed `SlateGameRow` per game -- assembled
   by the caller (see `scripts/live_integration_check_dashboard.py`) the same way
   `composition/player_detail.py` assembles `PlayerDetailRecord` for the Player Detail tab; this
   module adds no new computation, only formatting and a display-ordering heuristic over
   already-computed values.

## The decision-relevance filter -- what's shown, what's deliberately left out

Applying the MLB reference's own test verbatim ("state the lineup decision it changes and the
condition that flips it -- if you can't, the signal doesn't earn screen space"), per
`PlayerDetailRecord` section:

- **Role share** -- shown (`role_share_blended`, raw `trailing_share`/`trailing_volume`/
  `weeks_played`, `role_tier`, `is_team_identified_leader`, `prior_used`). Decision: which
  pass-catcher/back is the real "vehicle" for a stack thesis (directly the question
  `StackProfile.primary_stack_candidates`/`RoleShare` exists to answer, PRD Section 6/ADR-0019).
  Condition: a low blended share or "committee" tier should discount confidence in that player's
  specific volume thesis relative to a gate-identified bell-cow/plurality leader.
- **Snap share** -- shown, but narrowed to `offense_pct_last_week`/`offense_pct_trailing` only.
  `defense_pct`/`st_pct` are read by `PlayerSnapShare` for every skill-position player (it's a
  generic all-positions ingestion) but are structurally near-zero for a rostered offensive skill
  player and change no lineup decision this dashboard supports -- left out, not because the data
  doesn't exist, but because it fails the decision-relevance test for this player population.
- **Red zone usage** -- shown (`carries_trailing`/`carry_share_trailing`,
  `targets_trailing`/`target_share_trailing`). Decision: TD-equity concentration independent of
  overall volume share -- a real, distinct signal from role share (a committee back can still be
  the red-zone back). Condition: a red-zone share meaningfully above the player's overall role
  share flips a "he's just a committee piece" read.
- **Own scheme splits (man/zone)** -- shown, narrowed to the YPRR differential
  (`man_yprr`/`zone_yprr`) and the PFF route grade differential (`man_grades_pass_route`/
  `zone_grades_pass_route`) -- the two fields that most directly answer "does this player earn
  more against man or zone." `OwnSchemeSplits.grades` is deliberately not narrowed at the
  composition layer (its own docstring says so), but a dashboard row showing every raw field in
  that dict (targets, yards, contested-target rate, etc.) would bury the one comparison that
  actually matters for "should I expect this matchup to help or hurt this player" -- left out at
  the render layer, not the data layer.
- **Opponent coverage tendency faced** -- shown (`man_rate`/`zone_rate`, snap/defender counts).
  Decision: combined with the row above, "this receiver is a zone-buster facing a team that plays
  70% zone" is a real, statable matchup lean -- exactly the level `MatchupContext`'s coverage row
  is scoped to (PRD Section 6), even though the identified-defender-level grade itself isn't
  computed yet (see below).
- **Game environment** -- shown, narrowed to `composite_score` plus two confidence flags (the
  weather-impact badge and `injury_uncertainty_flag`), not the full four-component breakdown
  (`implied_total`/`pace`/`proe`/`weather` `ComponentScore`s). Decision: a low composite score or
  an active injury-uncertainty flag should discount confidence in every player on that team this
  week. The sub-component breakdown is architecture/QA-shaped data (which of four inputs drove the
  number), not a per-player-row lineup decision -- left out of this table, not off the dashboard's
  design space entirely (a future "Game Environment" tab, one row per team/game, is the more
  natural home for it).
- **`matchup_this_week.own_unit_grade`/`opponent_unit_grade` -- excluded entirely, not just
  hidden behind a reason string.** `MatchupContext` (ADR-0022 Round B, `matchup/context.py`) now
  computes these -- team-level run-block/run-defense, pass-block/pass-rush, or opponent coverage
  aggregates, position-dependent -- when a caller passes `matchup_facets` into
  `build_player_detail_record`. This dashboard's own render pipeline does not yet thread those
  already-fetched PFF grade facets through to that call, so the field still reads `None` here
  today -- a real, scoped follow-up (thread `matchup_facets` through this module's data-assembly
  step, then add the column), not evidence the formula itself is missing. Left off this table for
  now rather than shown as a column of `None`s indistinguishable from genuinely missing data --
  see `composition/player_detail.py`'s own `_MATCHUP_GRADE_NOTE`.
- **`PlayerDetailRecord.notes`, `pff_native_id`** -- excluded from the visible table (the `reason`
  strings surfaced per-section already carry the substance of `notes`; `pff_native_id` is
  traceability/QA metadata, not a lineup-decision input) -- kept in the record itself for anyone
  inspecting the underlying data, just not given dashboard screen space.

## Visually distinguishing validated vs. unvalidated signals

Mirrors the MLB build's Tier-badge convention (`.badge-unvalidated`/`t1val` in
`mlb_dfs/dashboard_v2/renderer.py`): validated/structural facts (a gate-identified role-share
leader, a computed coverage-tendency percentage) render as plain text or a solid informational
badge; explicitly-flagged, not-yet-backtested figures render as an outline/low-saturation badge
with a superscript marker and an explanatory tooltip, never with the same visual weight as a
validated number. Applied here to:

- `RoleShare.role_tier` (bell-cow/mid-tier/committee) -- PRD Section 6/ADR-0019's own words:
  "empirically cut, not backtested."
- `GameEnvironmentScore`'s weather contribution -- PRD Section 6/ADR-0007's own words: "external,
  unvalidated... sourced from public research, not this project's backtested data," now further
  damped to 60% of the cited (already-external) magnitude.
- `GameEnvironmentScore.injury_uncertainty_flag` -- not itself a magnitude-calibration concern
  (it's a rollup of RotoGrinders' own severity score, ADR-0017), but still a confidence/urgency
  signal that should visually stand apart from the plain composite-score number it sits beside, so
  it gets a colored (not outlined) status badge instead of the outline "unvalidated" treatment.
- `PlayerRoleShare.prior_used == "uncontested"` -- a real, live-validated finding (ADR-0020: n=16,
  Welch's t=4.25, cross-validated against an independent n=15 segmentation) but still an
  unbacktested *constant* per the PRD's own closing caveat -- rendered as a distinct informational
  badge (not the same outline-unvalidated style as `role_tier`, since it carries materially more
  empirical support), so a reader can tell "this player's prior swap is a validated finding" apart
  from "this tier boundary is just an empirical cut point."

## Nullability discipline

Every cell that can be `None` is rendered through `_na()`, which always shows the real `reason`
string a source module already computed (e.g. "no nflverse gsis_id resolved for this player...",
"no opponent identified for this week (a bye week...)") rather than a blank cell or the literal
text "None" -- the same "distinguishable nullability" discipline `composition/player_detail.py`
was built with, carried through to the render layer per the task brief's explicit requirement.
"""

from __future__ import annotations

import html as _html
from datetime import datetime, timezone

from dataclasses import dataclass

from nfl_dfs.composition.player_detail import (
    MatchupThisWeek,
    OwnSchemeSplits,
    PlayerDetailRecord,
    RedZoneUsage,
    RoleShareUsage,
    SnapShareUsage,
)
from nfl_dfs.game_environment.score import GameEnvironmentScore
from nfl_dfs.ingestion.weather import WeatherReading
from nfl_dfs.optimizer.lineup import Lineup
from nfl_dfs.output.rationale import LineupRationale
from nfl_dfs.output.weekly_output import WeeklyOutput

# Matches optimizer/lineup.py's `_assign_slots`/csv_export.py's `_SLOT_KEYS_IN_DK_ORDER` -- every
# legally-solved `Lineup.slots` has exactly these 9 keys (see that module's docstring). Duplicated
# here (a 9-item tuple literal) rather than importing a private name from csv_export -- this is
# the same small, stable, DK-roster-derived constant, not shared logic worth coupling modules over.
_DK_SLOT_ORDER: tuple[str, ...] = ("QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST")

_ROLE_TIER_LABELS: dict[str, str] = {
    "bell_cow": "Bell-cow",
    "mid_tier": "Mid-tier",
    "committee": "Committee",
}

# Canonical skill-position sort order for the Player Detail tab's default row ordering -- DST and
# anything unrecognized sort last, alphabetically among themselves.
_POSITION_SORT_ORDER: dict[str, int] = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "DST": 4}


@dataclass(frozen=True)
class SlateGameRow:
    """One game's worth of already-computed input for the Slate Overview tab -- the small, typed
    slice of `ingestion/odds_api.py`/`ingestion/weather.py`/`game_environment/score.py` output this
    tab needs, assembled by the caller (see `scripts/live_integration_check_dashboard.py` for the
    real assembly). This plays the same role for this tab that `composition/player_detail.py`'s
    `PlayerDetailRecord` plays for the Player Detail tab -- a purely-typed grouping of
    already-computed values, no new computation of its own. `home_team`/`away_team` are canonical
    DK abbreviations (`normalization/team_aliases.py`).

    Every nullable field is paired with a `*_reason` string (or, for the two `GameEnvironmentScore`
    fields, the score's own `.notes`/`is_available`), per this module's nullability discipline --
    see `_na()`. `None` must mean "this source module genuinely had no value for this game," never
    a stand-in for zero or league-average.
    """

    away_team: str
    home_team: str

    kickoff_utc: str | None  # raw ISO8601 UTC, as returned by the source (DK schedule preferred
    # over the Odds API's commence_time -- see weather.py's module docstring for why)
    kickoff_reason: str | None

    home_spread: float | None  # DK's signed spread outcome for the home team (negative = favorite)
    away_spread: float | None
    total: float | None
    odds_reason: str | None  # e.g. odds_api.py's own documented gap: the feed drops a game from
    # its listing entirely once it has kicked off, not present-with-stale-data

    home_implied_total: float | None  # odds_api.py's implied_team_totals()
    away_implied_total: float | None

    weather: WeatherReading | None  # None only when no reading was fetched at all for this game
    weather_reason: str | None

    home_environment: GameEnvironmentScore | None
    home_environment_reason: str | None
    away_environment: GameEnvironmentScore | None
    away_environment_reason: str | None


# ------------------------------------------------------------------------------------------
# Small formatting/escaping helpers
# ------------------------------------------------------------------------------------------


def _esc(value: object) -> str:
    """HTML-escape anything that will be interpolated as element content or an attribute value.
    `None` becomes `""`, never the literal text "None"."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def _fmt_pct(value: float | None, decimals: int = 1) -> str | None:
    if value is None:
        return None
    return f"{value * 100:.{decimals}f}%"


def _fmt_num(value: float | None, decimals: int = 1) -> str | None:
    if value is None:
        return None
    return f"{value:.{decimals}f}"


def _fmt_money(value: int | None) -> str:
    if value is None:
        return "--"
    return f"${value:,}"


def _na(reason: str | None, *, fallback: str = "no data supplied") -> str:
    """Render a missing value as its real, already-computed reason string -- never a blank cell
    or the literal text "None" (the task brief's explicit nullability requirement)."""
    text = reason if reason else fallback
    return f'<span class="na">N/A &mdash; {_esc(text)}</span>'


def _badge(label: str, css_class: str, title: str | None = None) -> str:
    title_attr = f' title="{_esc(title)}"' if title else ""
    return f'<span class="badge {css_class}"{title_attr}>{_esc(label)}</span>'


def _tier_badge(role_tier: str) -> str:
    """PRD Section 6/ADR-0019: RB role tiers are "empirically cut, not backtested" -- rendered as
    an outline/low-saturation "unvalidated" badge with a superscript marker, mirroring the MLB
    reference's Tier-1 convention (see module docstring)."""
    label = _ROLE_TIER_LABELS.get(role_tier, role_tier)
    return _badge(
        f"{label}¹",
        "badge-unvalidated",
        "Empirically-cut RB role-tier boundary (bell-cow >= 0.60, mid-tier 0.45-0.60, committee "
        "< 0.45, ADR-0019) -- not yet validated against backtested data. Not computed for WR "
        "(no WR tier cut points exist).",
    )


def _leader_badge() -> str:
    return _badge(
        "Identified leader",
        "badge-leader",
        "This player cleared the gate (margin + volume floor, ADR-0006/ADR-0012 pattern) that "
        "identifies the team's plurality role leader this week -- not just a candidate with "
        "some trailing volume.",
    )


def _uncontested_badge() -> str:
    return _badge(
        "Uncontested prior²",
        "badge-validated",
        "Blended toward the live-validated UNCONTESTED_RB_PRIOR=0.56 instead of the generic "
        "league-average prior (ADR-0020: n=16, Welch's t=4.25, cross-validated against an "
        "independent n=15 segmentation) -- more empirical support than the tier cut points above, "
        "but still an unbacktested constant per the PRD's own caveat.",
    )


def _weather_badge() -> str:
    return _badge(
        "Weather: external, unvalidated¹",
        "badge-unvalidated",
        "Weather curve shape and magnitude are sourced from external public research, damped to "
        "60% of the cited effect size for v1 -- not yet validated against this project's own "
        "backtested data (ADR-0002/ADR-0007/ADR-0015).",
    )


def _injury_badge(flag: str) -> str:
    if flag == "high_uncertainty":
        return _badge(
            "High injury uncertainty",
            "badge-danger",
            "Worst unresolved player on this team has a RotoGrinders IMPACTRTG >= 5 (ADR-0017).",
        )
    return _badge(
        "Moderate injury uncertainty",
        "badge-warning",
        "Worst unresolved player on this team has a RotoGrinders IMPACTRTG in [2, 5) (ADR-0017).",
    )


def _chalk_badge() -> str:
    return _badge(
        "Chalk",
        "badge-chalk",
        "Top decile of this position's projected ownership on this slate (ADR-0026) -- real chalk "
        "to build around or pointedly fade.",
    )


def _leverage_badge() -> str:
    return _badge(
        "Leverage",
        "badge-leverage",
        "Priced in the top half of this position's salaries this slate but projected well below "
        "the historical field-ownership baseline for that price tier (ADR-0025/ADR-0026) -- among "
        "the slate's most underowned relative to price.",
    )


def _in_lineup_badges(canonical_id: str, lineup_membership: dict[str, list[int]]) -> str:
    indices = lineup_membership.get(canonical_id)
    if not indices:
        return ""
    label = ", ".join(f"L{i}" for i in indices)
    return _badge(f"In {label}", "badge-lineup", "Appears in this generated lineup set.")


# ------------------------------------------------------------------------------------------
# Lineups tab
# ------------------------------------------------------------------------------------------


def _render_roster_table(lineup: Lineup) -> str:
    rows = []
    for slot in _DK_SLOT_ORDER:
        player = lineup.slots.get(slot)
        if player is None:
            continue  # defensive only -- a legally-solved Lineup always has all 9 keys
        rows.append(
            "<tr>"
            f"<td class=\"slot\">{_esc(slot)}</td>"
            f"<td>{_esc(player.display_name)}</td>"
            f"<td>{_esc(player.position)}</td>"
            f"<td>{_esc(player.team)}</td>"
            f"<td class=\"num\">{_fmt_money(player.salary)}</td>"
            f"<td class=\"num\">{_fmt_num(player.blended_projection, 1) or '--'}</td>"
            "</tr>"
        )
    return (
        '<table class="roster-table"><thead><tr>'
        "<th>Slot</th><th>Player</th><th>Pos</th><th>Team</th>"
        '<th class="num">Salary</th><th class="num">Proj Pts</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _render_lineups_tab(weekly_output: WeeklyOutput) -> str:
    lineups = weekly_output.lineups
    rationales_by_position: list[LineupRationale | None] = list(weekly_output.rationales)
    # Defensive only -- build_weekly_output always produces one rationale per lineup in the same
    # order (output/weekly_output.py), so this pads/truncates rather than crashing on a caller
    # that hands this renderer a mismatched pair.
    while len(rationales_by_position) < len(lineups):
        rationales_by_position.append(None)

    if not lineups:
        return '<div class="empty-state">No lineups in this weekly output.</div>'

    cards = []
    for i, lineup in enumerate(lineups):
        rationale = rationales_by_position[i]
        label = rationale.lineup_index if rationale is not None else i + 1
        notes_html = ""
        if lineup.notes:
            notes_html = (
                '<div class="lineup-notes">'
                + "".join(f"<div>{_esc(n)}</div>" for n in lineup.notes)
                + "</div>"
            )
        rationale_html = (
            f'<div class="rationale"><span class="rationale-label">Rationale</span>'
            f"<p>{_esc(rationale.text)}</p></div>"
            if rationale is not None
            else '<div class="rationale"><span class="rationale-label">Rationale</span>'
            "<p><em>No LineupRationale was supplied for this lineup.</em></p></div>"
        )
        cards.append(
            f'<div class="lineup-card">'
            f'<div class="lineup-head">Lineup {label} '
            f'<span class="meta">{_fmt_money(lineup.total_salary)} salary &middot; '
            f"{_fmt_num(lineup.total_projected_points, 1)} projected pts &middot; "
            f"core stack: {_esc(lineup.core_stack_team)}</span></div>"
            f"{_render_roster_table(lineup)}"
            f"{rationale_html}"
            f"{notes_html}"
            "</div>"
        )

    return '<div class="lineups-wrap">' + "".join(cards) + "</div>"


# ------------------------------------------------------------------------------------------
# Exposure tab
# ------------------------------------------------------------------------------------------


def _render_exposure_tab(weekly_output: WeeklyOutput) -> str:
    report = weekly_output.exposure_report
    header = f'<p class="sub">{report.lineup_count} lineup(s) in this set.</p>'

    if not report.players:
        players_table = '<div class="empty-state">No players to report.</div>'
    else:
        rows = []
        for pe in report.players:
            rows.append(
                "<tr>"
                f"<td>{_esc(pe.display_name)}</td>"
                f"<td>{_esc(pe.position)}</td>"
                f"<td>{_esc(pe.team)}</td>"
                f'<td class="num">{pe.count}/{pe.lineup_count}</td>'
                f'<td class="num">{pe.exposure_pct * 100:.0f}%</td>'
                "</tr>"
            )
        players_table = (
            '<table class="exposure-table"><thead><tr>'
            "<th>Player</th><th>Pos</th><th>Team</th>"
            '<th class="num">Count</th><th class="num">Exposure</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    if not report.stacks:
        stacks_table = '<div class="empty-state">No stacks to report.</div>'
    else:
        rows = []
        for se in report.stacks:
            rows.append(
                "<tr>"
                f"<td>{_esc(se.display)}</td>"
                f"<td>{_esc(se.core_stack_team)}</td>"
                f'<td class="num">{se.count}/{se.lineup_count}</td>'
                f'<td class="num">{se.exposure_pct * 100:.0f}%</td>'
                "</tr>"
            )
        stacks_table = (
            '<table class="exposure-table"><thead><tr>'
            "<th>Stack (QB + WR/TE)</th><th>Team</th>"
            '<th class="num">Count</th><th class="num">Exposure</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )

    return (
        header
        + "<h3>Players</h3>"
        + players_table
        + "<h3>Stacks</h3>"
        + stacks_table
    )


# ------------------------------------------------------------------------------------------
# Slate Overview tab -- one row per game
# ------------------------------------------------------------------------------------------

_TOP_CONVICTION_COUNT = 3

# GameEnvironmentScore.injury_uncertainty_flag's severity order, worst first -- reused here to
# pick the worse of the two teams' flags for a game-level rollup (a game is two teams; the more
# uncertain of the two is the one that should discount the reader's confidence in the game as a
# whole, the same "grade off the worst, not the average" pattern ADR-0017 already uses inside the
# flag's own per-team computation).
_INJURY_SEVERITY_RANK: dict[str | None, int] = {None: 0, "moderate": 1, "high_uncertainty": 2}


def _game_composite_scores(row: SlateGameRow) -> list[float]:
    """The available composite score(s) for this game -- 0, 1, or 2 of them, since either team's
    `GameEnvironmentScore` can independently be `is_available=False` (ADR-0016 tier 3)."""
    scores = []
    for ges in (row.home_environment, row.away_environment):
        if ges is not None and ges.is_available and ges.composite_score is not None:
            scores.append(ges.composite_score)
    return scores


def _worst_injury_flag(row: SlateGameRow) -> str | None:
    flags = [
        row.home_environment.injury_uncertainty_flag if row.home_environment else None,
        row.away_environment.injury_uncertainty_flag if row.away_environment else None,
    ]
    return max(flags, key=lambda f: _INJURY_SEVERITY_RANK.get(f, 0))


def _conviction_sort_key(row: SlateGameRow) -> tuple[int, float, str]:
    """Display-ordering heuristic for "which games have the highest conviction" (Chris's own
    framing). This is a presentation-layer judgment call, not a new scoring formula -- it changes
    no stored value, only the order/emphasis this tab renders games in. Real reasoning, not an
    arbitrary sort, follows; flagged at the end for whether it needs Architect confirmation.

    **Why not just sort by raw composite score:** a high `GameEnvironmentScore` can be undermined
    two different ways, and they are not the same kind of undermining:

    1. **Caveats already priced into the number itself.** The weather sub-component is real,
       damped Vegas-adjacent signal (ADR-0007: 60% of the cited external-research magnitude) that
       is already summed into `composite_score` -- a windy/wet game's number is already lower
       *because* of that. Its "external, unvalidated" tag (this module's `_weather_badge`) is a
       methodological caveat about the *source*, not evidence the number is wrong or stale. This
       doesn't need to change the sort order -- the effect is already reflected in the magnitude
       being sorted on. It's still shown (the weather badge renders on every outdoor game's row,
       same as the Player Detail tab), just not used to re-rank.
    2. **Caveats the composite number does NOT reflect at all.** Two cases, both explicit in
       `game_environment/score.py`'s own docstring: `is_available=False` means there is no number
       (ADR-0016 tier 3 -- ranking it by "0" or by the other team's score alone would fabricate a
       comparison that doesn't exist), and `injury_uncertainty_flag` is PRD Section 6's own words
       "a flag, not a score... doesn't blend into the composite numerically" -- a team can carry a
       528-point QB-questionable flag and an untouched, still-strong composite number, because the
       flag was designed to never touch that number. Sorting by raw magnitude alone would let a
       game with an unresolved star injury look identically confident to one with a clean injury
       report, which is exactly the "pretending they're equally strong reads" the task brief warns
       against.

    So: (a) games missing a composite score for either team sort to the bottom (bucket 1) --
    there is no "how high" to compare, an unknown is not a low number, so they're sorted by
    whatever partial score exists (or team name, if neither exists) purely for a stable/legible
    order, not to imply a ranking; (b) games with both scores available (bucket 0) sort by
    descending average composite magnitude -- the literal "highest conviction" reading, per the
    task brief's own suggested approach ("sort by score"); (c) within bucket 0, the "Top
    conviction" badge (`_conviction_badge_for_row`) is additionally withheld from any game
    carrying an injury-uncertainty flag on either team, since that's the caveat axis the number
    itself can't see -- weather's caveat does not withhold the badge, since it's already priced in
    per (1) above.

    **Flagged for Architect confirmation, same pattern as `_phi`/the missing-implied-total branch/
    the injury-rollup policy already flagged in `game_environment/score.py`'s own docstring:** the
    two-axis classification above (priced-in vs. not-priced-in caveats) is a real interpretive
    call about what "conviction" means, made here at the render layer because no upstream module
    defines a conviction/confidence score today. It is a defensible, stated judgment call, not
    settled spec -- fine as a dashboard sorting convenience, but worth Architect sign-off before
    this ranking (or the "Top conviction" label) is treated as an actual recommendation a user
    acts on with money.
    """
    scores = _game_composite_scores(row)
    availability_bucket = 0 if len(scores) == 2 else 1
    magnitude = sum(scores) / len(scores) if scores else float("-inf")
    return (availability_bucket, -magnitude, f"{row.away_team}@{row.home_team}")


def _conviction_badge_for_row(row: SlateGameRow, rank_in_bucket0: int | None) -> str:
    """The Conviction column's cell -- reuses `_na`/`_injury_badge` verbatim (no parallel styling
    system) plus exactly one new label ("Top conviction", the existing green `badge-leader` style
    already used for `_leader_badge`/`_in_lineup_badges`).

    When only one team carries an injury-uncertainty flag (the common case), that single badge
    renders exactly as before, unlabeled. When *both* teams carry a flag, both are shown -- each
    on its own line, prefixed with which team it belongs to -- instead of collapsing to just the
    worse of the two: silently dropping the milder team's flag loses real information (and, if
    both happen to be the same severity, two identical unlabeled badges would be genuinely
    ambiguous about which team -- or whether both -- are flagged)."""
    scores = _game_composite_scores(row)
    if len(scores) < 2:
        missing = [
            team
            for team, ges in ((row.away_team, row.away_environment), (row.home_team, row.home_environment))
            if ges is None or not ges.is_available
        ]
        return _na(
            f"GameEnvironmentScore unavailable for {', '.join(missing)} -- not ranked against "
            "games with two known scores (an unknown number is not a low number)",
            fallback="GameEnvironmentScore unavailable for at least one team this week",
        )
    away_flag = row.away_environment.injury_uncertainty_flag if row.away_environment else None
    home_flag = row.home_environment.injury_uncertainty_flag if row.home_environment else None
    if away_flag and home_flag:
        return (
            f'<div class="cell-injury">{_esc(row.away_team)}: {_injury_badge(away_flag)}</div>'
            f'<div class="cell-injury">{_esc(row.home_team)}: {_injury_badge(home_flag)}</div>'
            '<div class="cell-sub">Conviction capped: the composite score above does not itself '
            "reflect either team's flag (PRD Section 6).</div>"
        )
    worst_flag = _worst_injury_flag(row)
    if worst_flag is not None:
        return _injury_badge(worst_flag) + (
            '<div class="cell-sub">Conviction capped: the composite score above does not itself '
            "reflect this flag (PRD Section 6).</div>"
        )
    if rank_in_bucket0 is not None and rank_in_bucket0 <= _TOP_CONVICTION_COUNT:
        return _badge(
            "Top conviction",
            "badge-leader",
            "Among this week's highest-magnitude game environments with no unresolved caveat on "
            "either team (both composite scores available, no injury-uncertainty flag).",
        )
    return '<span class="muted">Clean read, not top-ranked</span>'


def _render_line_cell(row: SlateGameRow) -> str:
    if row.home_spread is None or row.total is None:
        return _na(row.odds_reason, fallback="no DraftKings odds line for this game")
    return (
        f'<div class="cell-main">{_esc(row.home_team)} {row.home_spread:+.1f}</div>'
        f'<div class="cell-sub">O/U {_fmt_num(row.total, 1)}</div>'
    )


def _render_implied_totals_cell(row: SlateGameRow) -> str:
    if row.away_implied_total is None or row.home_implied_total is None:
        return _na(row.odds_reason, fallback="implied totals unavailable for this game")
    return (
        f'<div class="cell-main">{_esc(row.away_team)} {_fmt_num(row.away_implied_total, 1)} '
        f"&ndash; {_esc(row.home_team)} {_fmt_num(row.home_implied_total, 1)}</div>"
    )


def _render_weather_cell(row: SlateGameRow) -> str:
    weather = row.weather
    if weather is None:
        return _na(row.weather_reason, fallback="no weather reading fetched for this game")
    if weather.is_indoor:
        return (
            '<div class="cell-main">Dome / closed roof</div>'
            f'<div class="cell-sub">{_esc(weather.source_notes)}</div>'
        )
    temp = _fmt_num(weather.temperature_f, 0)
    wind = _fmt_num(weather.wind_mph, 0)
    if weather.precipitation_band and weather.precipitation_band != "none":
        precip_text = weather.precipitation_band.replace("_", " ")
    elif weather.has_precipitation is True:
        precip_text = f"precipitation ({_fmt_num(weather.precipitation_in, 2) or '--'}in)"
    elif weather.has_precipitation is False:
        precip_text = "no precipitation"
    else:
        precip_text = "precipitation data unavailable"
    badge = _weather_badge()
    return (
        f'<div class="cell-main">{temp or "--"}&deg;F, {wind or "--"}mph wind</div>'
        f'<div class="cell-sub">{_esc(precip_text)}</div>'
        f'<div class="badges">{badge}</div>'
    )


def _render_team_environment_block(team: str, ges: GameEnvironmentScore | None, reason: str | None) -> str:
    """One team's half of the Game Environment cell. Always wrapped in a single `.ge-team`
    container -- `_render_slate_game_environment_cell` concatenates this function's output for
    both teams inside a `display: flex` `.ge-pair`, and `.ge-pair > div` puts flex sizing on each
    *direct child div*. Without this wrapper, this function's own sub/main/badge divs (2-3 of
    them) would each be a direct child of `.ge-pair` in their own right, so two flagged teams
    produce six same-level flex items instead of two -- the away team's badge no longer sits
    under the away team's own score, and the row overflows its column, spilling the last badge
    into the adjacent Conviction cell with no gap (this was the actual cause of the two
    "High injury uncertainty" badges rendering glued together -- one team's badge escaping this
    cell, not two badges genuinely sharing the Conviction cell). One `.ge-team` wrapper per team
    keeps each team's own content stacked in its own flex column no matter how many badges it
    carries."""
    if ges is None:
        return f'<div class="ge-team"><div class="cell-sub">{_esc(team)}</div>{_na(reason)}</div>'
    if not ges.is_available:
        note = "; ".join(ges.notes) if ges.notes else None
        return (
            '<div class="ge-team">'
            f'<div class="cell-sub">{_esc(team)}</div>'
            + _na(note, fallback="GameEnvironmentScore unavailable this week")
            + "</div>"
        )
    badges = [_injury_badge(ges.injury_uncertainty_flag)] if ges.injury_uncertainty_flag else []
    badges_html = f'<div class="badges">{"".join(badges)}</div>' if badges else ""
    score = _fmt_num(ges.composite_score, 1) or "--"
    return (
        '<div class="ge-team">'
        f'<div class="cell-sub">{_esc(team)}</div><div class="cell-main">{score} / 100</div>{badges_html}'
        "</div>"
    )


def _render_slate_game_environment_cell(row: SlateGameRow) -> str:
    return (
        '<div class="ge-pair">'
        + _render_team_environment_block(row.away_team, row.away_environment, row.away_environment_reason)
        + _render_team_environment_block(row.home_team, row.home_environment, row.home_environment_reason)
        + "</div>"
    )


def _fmt_kickoff(kickoff_utc: str) -> str:
    """Best-effort human display of a raw ISO8601 UTC kickoff string. Small, local parse (handles
    both the Odds API's plain form and DK's 7-digit-fraction form) rather than importing
    `weather.py`'s private `_parse_iso8601_utc` -- same "small, stable, not shared logic worth
    coupling modules over" rationale as this module's own `_DK_SLOT_ORDER` duplication (see module
    docstring). Falls back to the raw string (never a blank cell) if parsing fails."""
    ts = kickoff_utc.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    if "." in ts:
        date_part, rest = ts.split(".", 1)
        for i, ch in enumerate(rest):
            if ch in "+-":
                frac, tz = rest[:i], rest[i:]
                break
        else:
            frac, tz = rest, ""
        ts = f"{date_part}.{(frac + '000000')[:6]}{tz}"
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return _esc(kickoff_utc)
    return _esc(dt.strftime("%a %b %d, %I:%M %p UTC").replace(" 0", " "))


def _render_slate_overview_tab(games: list[SlateGameRow]) -> str:
    if not games:
        return '<div class="empty-state">No games supplied for this slate.</div>'

    legend = (
        "<details class=\"legend\"><summary>What&rsquo;s shown here, and what&rsquo;s left out"
        "</summary><div class=\"legend-body\">"
        "<p><strong>One row per game</strong> (not per team) -- matchup and kickoff, the "
        "DraftKings spread/total and each team's implied total (<code>ingestion/odds_api.py</code>"
        "), weather conditions (<code>ingestion/weather.py</code>), and both teams' "
        "<code>GameEnvironmentScore</code> composite plus any injury-uncertainty flag "
        "(<code>game_environment/score.py</code>). The four-component breakdown behind each "
        "composite (implied total/pace/PROE/weather individually) is not repeated here -- that's "
        "the Player Detail tab's job, narrowed to the composite score there too.</p>"
        "<p><strong>Sort order -- &ldquo;highest conviction&rdquo; (Chris's own framing):</strong> "
        "games with both teams' composite scores available are sorted by descending average "
        "magnitude; games missing a score for either team sort below them (an unknown number is "
        "not a low number, so it isn't compared against real ones). The green &ldquo;Top "
        "conviction&rdquo; badge additionally requires no injury-uncertainty flag on either "
        "team -- that flag is PRD Section 6's own words a signal that &ldquo;doesn't blend into "
        "the composite numerically,&rdquo; so a strong number next to an unresolved star injury "
        "is a materially less certain read than the same number with a clean injury report, even "
        "though the two numbers look identical. Weather's own &ldquo;external, unvalidated&rdquo; "
        "tag is shown but does not change the sort -- its damped effect (ADR-0007) is already "
        "summed into the composite number being sorted on, unlike the injury flag. See "
        "<code>_conviction_sort_key</code>'s docstring for the full reasoning, including why this "
        "is flagged for Architect confirmation before being treated as an actual recommendation.</p>"
        "</div></details>"
    )

    ordered = sorted(games, key=_conviction_sort_key)

    rows = []
    bucket0_rank = 0
    for row in ordered:
        in_bucket0 = len(_game_composite_scores(row)) == 2
        if in_bucket0:
            bucket0_rank += 1
        kickoff_html = _fmt_kickoff(row.kickoff_utc) if row.kickoff_utc else _na(row.kickoff_reason)
        rows.append(
            "<tr>"
            f'<td><div class="cell-main">{_esc(row.away_team)} @ {_esc(row.home_team)}</div>'
            f'<div class="cell-sub">{kickoff_html}</div></td>'
            f"<td>{_render_line_cell(row)}</td>"
            f"<td>{_render_implied_totals_cell(row)}</td>"
            f"<td>{_render_weather_cell(row)}</td>"
            f"<td>{_render_slate_game_environment_cell(row)}</td>"
            f"<td>{_conviction_badge_for_row(row, bucket0_rank if in_bucket0 else None)}</td>"
            "</tr>"
        )

    table = (
        '<div class="table-scroll"><table class="slate-overview-table"><thead><tr>'
        "<th>Matchup</th><th>Line</th><th>Implied Totals</th><th>Weather</th>"
        "<th>Game Environment (Away / Home)</th><th>Conviction</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )

    return legend + table


# ------------------------------------------------------------------------------------------
# Player Detail tab -- per-section cell renderers
# ------------------------------------------------------------------------------------------


def _render_salary_cell(record: PlayerDetailRecord) -> str:
    if record.salary is None:
        return _na(record.salary_reason, fallback="no DK salary found for this player")
    return f'<span class="num">{_fmt_money(record.salary)}</span>'


def _render_role_share_cell(usage: RoleShareUsage) -> str:
    if usage.role_share is None:
        return _na(usage.reason)
    rs = usage.role_share
    badges = []
    if rs.role_tier:
        badges.append(_tier_badge(rs.role_tier))
    if usage.is_team_identified_leader:
        badges.append(_leader_badge())
    if rs.prior_used == "uncontested":
        badges.append(_uncontested_badge())
    badges_html = f'<div class="badges">{"".join(badges)}</div>' if badges else ""
    return (
        f'<div class="cell-main">{_fmt_pct(rs.role_share_blended)}</div>'
        f'<div class="cell-sub">{_fmt_pct(rs.trailing_share)} trailing '
        f"({rs.trailing_volume} vol, {rs.weeks_played}wk)</div>"
        f"{badges_html}"
    )


def _render_snap_share_cell(usage: SnapShareUsage) -> str:
    if usage.snap_share is None:
        return _na(usage.reason)
    s = usage.snap_share
    last = _fmt_pct(s.offense_pct_last_week)
    trailing = _fmt_pct(s.offense_pct_trailing)
    trend = ""
    # A real role-consolidating-or-fading signal, not just the blended trailing figure (Fantasy
    # Football Expert's dashboard review) -- flagged only past a 10pt gap so normal week-to-week
    # noise doesn't light up as a false trend.
    if s.offense_pct_last_week is not None and s.offense_pct_trailing is not None:
        delta = s.offense_pct_last_week - s.offense_pct_trailing
        if abs(delta) >= 0.10:
            css = "trend-up" if delta > 0 else "trend-down"
            arrow = "&#9650;" if delta > 0 else "&#9660;"
            trend = f' <span class="{css}">{arrow} {abs(delta) * 100:.0f}pt</span>'
    return (
        f'<div class="cell-main">{last or "--"} last wk{trend}</div>'
        f'<div class="cell-sub">{trailing or "--"} trailing ({s.weeks_played}wk)</div>'
    )


def _fmt_red_zone_trend(pairs: list[tuple[int, float]]) -> str | None:
    """Real, ordered per-week share sequence behind a single trailing-share number (ADR-0029
    addendum) -- shows consistency vs. spikiness directly rather than collapsing it, same
    descriptive posture as the Receiving Opportunity block."""
    if not pairs:
        return None
    return " &middot; ".join(f"W{week} {_fmt_pct(share)}" for week, share in pairs)


def _render_red_zone_cell(record: PlayerDetailRecord) -> str:
    usage = record.usage.red_zone
    if usage.reason is not None:
        return _na(usage.reason)
    lines = []
    if usage.carries_trailing is not None:
        lines.append(
            f"RB: {usage.carries_trailing} carries ({_fmt_pct(usage.carry_share_trailing)} share)"
        )
        carry_trend = _fmt_red_zone_trend(usage.carry_share_by_week)
        if carry_trend:
            lines.append(f'<div class="cell-sub">Trend: {carry_trend}</div>')
    if usage.targets_trailing is not None:
        lines.append(
            f"WR/TE: {usage.targets_trailing} targets "
            f"({_fmt_pct(usage.target_share_trailing)} share)"
        )
        target_trend = _fmt_red_zone_trend(usage.target_share_by_week)
        if target_trend:
            lines.append(f'<div class="cell-sub">Trend: {target_trend}</div>')
    if not lines:
        # A real, distinguishable "not applicable role" state, not missing data -- e.g. a pure
        # WR/TE has no RB-role red-zone row at all. Muted, but not styled as an error/na state.
        return '<span class="muted">No red-zone role recorded (RB carries or WR/TE targets)</span>'
    # A raw red-zone share means little without the team's own red-zone-trip frequency to weigh it
    # against (Fantasy Football Expert's dashboard review) -- the closest already-computed proxy is
    # this team's live implied point total, shown alongside rather than the share floating alone.
    context = f'<div class="cell-sub">Team implied total: {record.implied_total:.1f}</div>' if record.implied_total is not None else ""
    return "<br>".join(lines) + context


def _render_receiving_profile_cell(record: PlayerDetailRecord) -> str:
    """Real, descriptive trailing opportunity numbers (ADR-0029) -- no backtested claim, no
    z-scoring. Shown for a person to judge what KIND of opportunity a player is getting (real
    depth, real YAC upside, real volume) when choosing between two similarly-priced players, not
    to feed an automated score -- Component C's own aDOT backtest came back a clean null
    (ADR-0028), so this is deliberately presented as raw facts, not a ranked/scored signal.
    """
    profile = record.receiving_profile
    if profile is None:
        return _na(record.receiving_profile_reason, fallback="no receiving-opportunity data for this player")
    adot = f"{profile.trailing_adot:.1f}" if profile.trailing_adot is not None else "--"
    yac = f"{profile.trailing_yac_per_reception:.1f}" if profile.trailing_yac_per_reception is not None else "--"
    return (
        f'<div class="cell-main">{profile.trailing_targets} targets, {profile.trailing_receptions} rec</div>'
        f'<div class="cell-sub">{profile.trailing_air_yards} air yds &middot; {adot} aDOT &middot; {yac} YAC/rec</div>'
    )


def _render_own_scheme_cell(splits: OwnSchemeSplits) -> str:
    if not splits.applicable:
        return _na(splits.reason)
    if splits.reason is not None:
        return _na(splits.reason)
    grades = splits.grades
    man_yprr, zone_yprr = grades.get("man_yprr"), grades.get("zone_yprr")
    man_route, zone_route = grades.get("man_grades_pass_route"), grades.get("zone_grades_pass_route")

    lines = []
    if man_yprr is not None or zone_yprr is not None:
        diff = (
            f' <span class="diff">(&Delta; {man_yprr - zone_yprr:+.2f})</span>'
            if man_yprr is not None and zone_yprr is not None
            else ""
        )
        lines.append(
            f"YPRR: man {_fmt_num(man_yprr, 2) or '--'} / zone {_fmt_num(zone_yprr, 2) or '--'}{diff}"
        )
    if man_route is not None or zone_route is not None:
        lines.append(
            f"Route grade: man {_fmt_num(man_route, 1) or '--'} / zone {_fmt_num(zone_route, 1) or '--'}"
        )
    if not lines:
        return _na(
            "no man/zone YPRR or route-grade fields present in this pull for this player "
            f"(population={splits.population!r})",
            fallback="no scheme-split data",
        )
    return "<br>".join(lines)


def _render_coverage_tendency_cell(matchup: MatchupThisWeek) -> str:
    if matchup.opponent_team is None:
        return _na(matchup.coverage_tendency_reason)
    if matchup.coverage_tendency_faced is None:
        return _na(matchup.coverage_tendency_reason)
    t = matchup.coverage_tendency_faced
    if t.man_rate is None or t.zone_rate is None:
        return _na(
            f"{t.team} has zero combined man+zone coverage snaps recorded in this pull.",
            fallback="no man/zone snap data",
        )
    return (
        f'<div class="cell-main">{_esc(matchup.opponent_team)}: '
        f"{_fmt_pct(t.man_rate)} man / {_fmt_pct(t.zone_rate)} zone</div>"
        f'<div class="cell-sub">{t.defender_count} defenders, {t.man_snaps + t.zone_snaps} snaps</div>'
    )


def _render_game_environment_cell(record: PlayerDetailRecord) -> str:
    ge = record.game_environment
    if ge is None:
        return _na(record.game_environment_reason)
    if not ge.is_available:
        reason = "; ".join(ge.notes) if ge.notes else None
        return _na(
            reason,
            fallback="GameEnvironmentScore unavailable this week (missing implied-total line, "
            "ADR-0016/ADR-0017 exclusion policy)",
        )
    badges = []
    if ge.weather.points is not None and ge.weather.weight_pct > 0:
        badges.append(_weather_badge())
    if ge.injury_uncertainty_flag:
        badges.append(_injury_badge(ge.injury_uncertainty_flag))
    badges_html = f'<div class="badges">{"".join(badges)}</div>' if badges else ""
    score = _fmt_num(ge.composite_score, 1) or "--"
    # The 0-100 composite hides direction -- a 3pt road dog in a 51pt game and a 10pt home favorite
    # in a 44pt game can land on the same composite score but imply opposite player-selection
    # theses (Fantasy Football Expert's dashboard review). Show the raw numbers alongside, not just
    # the folded score.
    extra = []
    if record.implied_total is not None:
        extra.append(f"implied {record.implied_total:.1f}")
    if record.stack_context is not None:
        extra.append(f"{record.stack_context.home_team} {record.stack_context.home_spread:+.1f}")
    extra_html = f'<div class="cell-sub">{_esc(" · ".join(extra))}</div>' if extra else ""
    return f'<div class="cell-main">{score} / 100</div>{extra_html}{badges_html}'


def _render_ownership_cell(record: PlayerDetailRecord) -> str:
    ownership = record.ownership
    if ownership is None:
        return _na(record.ownership_reason, fallback="no ownership/leverage data for this player")
    badges = []
    if ownership.is_chalk:
        badges.append(_chalk_badge())
    if ownership.is_leverage:
        badges.append(_leverage_badge())
    badges_html = f'<div class="badges">{"".join(badges)}</div>' if badges else ""
    # projected_ownership/baseline_ownership are already on a 0-100 scale (rotogrinders.py's
    # _parse_percent), unlike this module's own _fmt_pct helper (which expects a 0-1 fraction) --
    # formatted directly here rather than misapplying that helper.
    baseline_sub = ""
    if ownership.baseline_ownership is not None and ownership.ownership_vs_baseline is not None:
        baseline_sub = (
            f'<div class="cell-sub">vs {ownership.baseline_ownership:.1f}% baseline '
            f"({ownership.ownership_vs_baseline:+.1f}pt)</div>"
        )
    return (
        f'<div class="cell-main">{ownership.projected_ownership:.1f}% proj</div>'
        f"{baseline_sub}{badges_html}"
    )


def _render_projection_cell(record: PlayerDetailRecord) -> str:
    if record.projection is None:
        return _na(record.projection_reason, fallback="no blended projection for this player")
    return f'<span class="num">{record.projection:.1f}</span>'


def _render_ceiling_cell(record: PlayerDetailRecord) -> str:
    if record.ceiling_multiplier is None:
        return _na(record.ceiling_multiplier_reason, fallback="no ceiling read for this player")
    main = f'<span class="num">{record.ceiling_projection:.1f}</span>' if record.ceiling_projection is not None else "&mdash;"
    sub = f'<div class="cell-sub">{record.ceiling_multiplier:.2f}x</div>'
    return main + sub


def _render_value_cell(record: PlayerDetailRecord) -> str:
    value = record.value
    if value is None:
        return "&mdash;"
    return f'<span class="num">{value:.2f}</span>'


_BRING_BACK_STATUS_NOTES: dict[str, str] = {
    "no_confident_candidate": "no confident bring-back candidate",
    "environment_unavailable": "game environment unavailable",
    "game_stack_not_viable": "game stack not viable",
}


def _render_stack_cell(record: PlayerDetailRecord) -> str:
    ctx = record.stack_context
    if ctx is None:
        return _na(record.stack_context_reason, fallback="no StackProfile for this player's game")
    badges = []
    if ctx.is_primary_stack_candidate:
        badges.append(
            _badge(
                f"Primary #{ctx.primary_stack_rank}",
                "badge-stack-primary",
                "Ranked primary-stack candidate for this game's anchor team, by target share "
                "(StackProfile, correlation/stack_profile.py).",
            )
        )
    if ctx.is_bring_back_candidate:
        badges.append(
            _badge(
                "Bring-back",
                "badge-stack-bringback",
                "Live bring-back candidate for this game's stack thesis (StackProfile).",
            )
        )
    badges_html = f'<div class="badges">{"".join(badges)}</div>' if badges else ""
    sub_parts = [f"{ctx.home_team} {ctx.home_spread:+.1f}"]
    if ctx.game_stack_viability is not None:
        sub_parts.append(f"game stack {ctx.game_stack_viability:.0f}")
    status_note = _BRING_BACK_STATUS_NOTES.get(ctx.bring_back_status)
    if status_note and not ctx.is_bring_back_candidate:
        sub_parts.append(status_note)
    main = f'<div class="cell-main">{_fmt_num(ctx.single_team_viability, 0) or "--"} viability</div>'
    sub = f'<div class="cell-sub">{_esc(" · ".join(sub_parts))}</div>'
    return main + sub + badges_html


_SLATE_WINDOW_LABELS: dict[str, str] = {
    "early": "Early (1pm ET)",
    "late": "Late (4pm ET)",
    "snf": "SNF",
    "mnf": "MNF",
    "tnf": "TNF",
    "other": "Other",
}


def _render_slate_window_cell(record: PlayerDetailRecord) -> str:
    if record.slate_window is None:
        return _na(record.slate_window_reason, fallback="no kickoff time known for this player's game")
    return _esc(_SLATE_WINDOW_LABELS.get(record.slate_window, record.slate_window))


def _render_injury_cell(record: PlayerDetailRecord) -> str:
    if record.injury is None:
        # "Not on the injury report" is real, positive information (presumed healthy) -- a
        # different case from "no injury data was supplied at all" (composition/player_detail.py's
        # `_injury` composer, ADR-0027), distinguished here by substring rather than importing that
        # module's private reason constant.
        if record.injury_reason and "presumed healthy" in record.injury_reason:
            return '<span class="muted">Healthy</span>'
        return _na(record.injury_reason, fallback="no injury data for this player")
    injury = record.injury
    return (
        f'<div class="cell-main">{_esc(injury.status)} &middot; {_esc(injury.body_part)}</div>'
        f'<div class="cell-sub">Impact {injury.impact_rating}/10</div>'
    )


def _expand_block(label: str, content_html: str) -> str:
    return (
        f'<div class="expand-block"><div class="expand-label">{_esc(label)}</div>'
        f'<div class="expand-value">{content_html}</div></div>'
    )


_SKILL_POSITIONS: frozenset[str] = frozenset({"RB", "WR", "TE"})


def _render_player_expand_content(record: PlayerDetailRecord) -> str:
    """The deep-detail fields that don't earn a default-visible column (UI/UX's "wrong altitude
    for a scannable default view" call, ADR-0027) -- shown per row on click, not hidden entirely.
    Position-appropriate by construction: the skill-only blocks (role/snap/red-zone) are simply
    omitted for QB/DST, and `own_scheme_splits.applicable` already gates itself, rather than this
    function hardcoding a second, parallel position check.
    """
    blocks = []
    if record.position in _SKILL_POSITIONS:
        blocks.append(_expand_block("Role Share", _render_role_share_cell(record.usage.role_share)))
        blocks.append(_expand_block("Snap Share", _render_snap_share_cell(record.usage.snap_share)))
        blocks.append(_expand_block("Red Zone", _render_red_zone_cell(record)))
        blocks.append(_expand_block("Receiving Opportunity", _render_receiving_profile_cell(record)))
    if record.own_scheme_splits.applicable:
        blocks.append(_expand_block("Own Scheme Split", _render_own_scheme_cell(record.own_scheme_splits)))
    blocks.append(_expand_block("Opp Coverage Faced", _render_coverage_tendency_cell(record.matchup_this_week)))
    blocks.append(_expand_block("Game Environment", _render_game_environment_cell(record)))
    blocks.append(_expand_block("Slate Window", _render_slate_window_cell(record)))
    blocks.append(_expand_block("Injury", _render_injury_cell(record)))
    return '<div class="expand-grid">' + "".join(blocks) + "</div>"


def _position_sort_key(record: PlayerDetailRecord) -> tuple[int, str, str]:
    return (
        _POSITION_SORT_ORDER.get(record.position, 99),
        record.team,
        record.identity.display_name,
    )


def _render_position_filters(player_details: list[PlayerDetailRecord]) -> str:
    """A row of position-toggle buttons, styled/behaved like the Lineups/Exposure/Player Detail
    tab-switcher (`.tab`/`showTab`, see `_CSS`/`_JS`) rather than introducing a different UI
    pattern -- "All" plus one button per position that actually appears in `player_details` (never
    a hardcoded QB/RB/WR/TE/DST five-way, since a given render may not have every DK position,
    e.g. no DST rows)."""
    positions_present = sorted(
        {record.position for record in player_details},
        key=lambda p: (_POSITION_SORT_ORDER.get(p, 99), p),
    )
    buttons = [
        '<div class="pos-filter active" data-pos="ALL" onclick="setPositionFilter(\'ALL\', this)">'
        "All</div>"
    ]
    for pos in positions_present:
        esc_pos = _esc(pos)
        buttons.append(
            f'<div class="pos-filter" data-pos="{esc_pos}" '
            f"onclick=\"setPositionFilter('{esc_pos}', this)\">{esc_pos}</div>"
        )
    return '<div class="pos-filters">' + "".join(buttons) + "</div>"


def _player_view(position: str) -> str:
    """Coarse Skill/QB/DST grouping (ADR-0027, per the Fantasy Football Expert's finding that QB
    and DST are evaluated on fundamentally different mechanisms than the role-share/red-zone frame
    that correctly drives RB/WR/TE, and UI/UX's recommendation to split them as their own sub-tab
    rather than force them through that frame). Every field on `PlayerDetailRecord` already adapts
    to position on its own (`own_scheme_splits.applicable`, `usage` sections only populate for
    skill positions, etc.) -- this grouping controls which ROWS a sub-tab shows, not a second,
    separately-maintained column manifest; `_render_player_expand_content` already renders the
    right depth per row by construction, so a shared column set doesn't reintroduce the noise this
    split exists to remove.
    """
    if position in _SKILL_POSITIONS:
        return "skill"
    if position == "QB":
        return "qb"
    if position == "DST":
        return "dst"
    return "other"


_VIEW_LABELS: dict[str, str] = {"skill": "Skill (RB/WR/TE)", "qb": "QB", "dst": "DST"}


def _render_view_filters(player_details: list[PlayerDetailRecord]) -> str:
    views_present = sorted({_player_view(r.position) for r in player_details} & set(_VIEW_LABELS), key=lambda v: list(_VIEW_LABELS).index(v))
    buttons = [
        '<div class="view-filter active" data-view="ALL" onclick="setViewFilter(\'ALL\', this)">All positions</div>'
    ]
    for view in views_present:
        buttons.append(
            f'<div class="view-filter" data-view="{view}" onclick="setViewFilter(\'{view}\', this)">'
            f"{_esc(_VIEW_LABELS[view])}</div>"
        )
    return '<div class="view-filters">' + "".join(buttons) + "</div>"


# (column index, header label, sort type) for every sortable column -- column index must match the
# literal <td> order built in _render_player_detail_tab below.
_SORTABLE_COLUMNS: tuple[tuple[int, str, str], ...] = (
    (0, "Player", "str"),
    (1, "Pos", "str"),
    (2, "Team", "str"),
    (4, "Salary", "num"),
    (5, "Projection", "num"),
    (6, "Ceiling", "num"),
    (7, "Value", "num"),
    (8, "Proj Own%", "num"),
)


def _render_player_detail_tab(
    player_details: list[PlayerDetailRecord], lineup_membership: dict[str, list[int]]
) -> str:
    if not player_details:
        return '<div class="empty-state">No player-detail records supplied.</div>'

    legend = (
        "<details class=\"legend\"><summary>What&rsquo;s shown here, and what&rsquo;s left out"
        "</summary><div class=\"legend-body\">"
        "<p><strong>Default columns vs. row detail (ADR-0027):</strong> the visible grid is "
        "deliberately lean (identity, salary, projection, value, ownership/leverage, stack "
        "context) so a 300-600 row pool stays scannable -- click a row to expand the rest (role "
        "share, snap share, red zone, own man/zone scheme split, opponent coverage faced, game "
        "environment, slate window, injury). Skill-only sections (role/snap/red-zone) are simply "
        "omitted from the expand for QB/DST rather than shown empty, and <code>own_scheme_splits"
        "</code> is omitted whenever <code>applicable</code> is <code>False</code>.</p>"
        "<p><strong>Still excluded from every row:</strong> "
        "<code>matchup_this_week.own_unit_grade</code>/<code>opponent_unit_grade</code> "
        "(team-level run-block/run-defense, pass-block/pass-rush, or opponent coverage grade, "
        "per position) are computed by <code>MatchupContext</code> (ADR-0022 Round B, "
        "<code>matchup/context.py</code>) when a caller supplies <code>matchup_facets</code> to "
        "<code>build_player_detail_record</code> &mdash; but this dashboard's own render pipeline "
        "does not yet thread those facet pulls through, so these fields still read as "
        "<code>None</code> here specifically (not because the formula is unimplemented). Also "
        "still out: a lineup-*set*-level ownership rollup (does the 3-lineup set actually span "
        "chalk-to-leverage). The Ceiling column (ADR-0028) is real but partial: RB/WR only, "
        "Component A (role-share boom-rate) only, both-experts-backtested-and-signed-off -- "
        "TE/QB/DST and Components B/C (red-zone boom-rate, depth-of-target) remain uncalibrated, "
        "so a low or missing Ceiling read is not the same claim as \"this player has no upside.\"</p>"
        "<p><strong>Narrowed at this render layer</strong> (present in the underlying "
        "<code>PlayerDetailRecord</code>, not shown in full here): snap share's "
        "<code>defense_pct</code>/<code>st_pct</code> (structurally near-zero for a rostered "
        "offensive skill player, no lineup decision they change); the full raw "
        "<code>own_scheme_splits.grades</code> dict beyond the man/zone YPRR and route-grade "
        "differential (targets, yards, contested-target rate, etc. -- shown fields answer &ldquo;"
        "does this player earn more against man or zone,&rdquo; the rest is detail, not a "
        "decision); <code>GameEnvironmentScore</code>'s four sub-component breakdown (implied "
        "total/pace/PROE/weather individually) beyond the composite score plus its two confidence "
        "badges -- that breakdown is architecture/QA-shaped, not a per-player lineup call.</p>"
        "<p><sup>1</sup> = flagged unvalidated/external in the PRD (RB role tiers: empirically cut, "
        "not backtested, ADR-0019; weather curve: sourced from external research, damped to 60% "
        "for v1, ADR-0002/0007/0015). <sup>2</sup> = a live-validated finding (ADR-0020) that is "
        "still, per the PRD's own caveat, an unbacktested constant -- shown with a distinct badge "
        "style from the plain unvalidated marker above.</p>"
        "</div></details>"
    )

    view_filters = _render_view_filters(player_details)
    position_filters = _render_position_filters(player_details)

    search = (
        '<div class="search-row">'
        '<input type="text" id="player-search" placeholder="Filter by player, team, or position...'
        '" oninput="filterPlayerDetailRows()">'
        '<span id="player-search-count" class="search-count"></span>'
        "</div>"
    )
    viability = (
        '<div class="viability-row">'
        '<label><input type="checkbox" id="show-unviable" onchange="filterPlayerDetailRows()"> '
        "Show players with no salary/projection</label>"
        '<label>Min value ($/1K): <input type="number" id="value-floor" step="0.1" '
        'placeholder="0" onchange="filterPlayerDetailRows()"></label>'
        '<button type="button" onclick="resetPlayerDetailFilters()">Reset filters</button>'
        "</div>"
    )

    rows = []
    for record in sorted(player_details, key=_position_sort_key):
        identity = record.identity
        lineup_badge = _in_lineup_badges(identity.canonical_id, lineup_membership)
        player_cell = (
            f'<div class="cell-main">{_esc(identity.display_name)}</div>'
            + (f'<div class="badges">{lineup_badge}</div>' if lineup_badge else "")
        )
        opponent_cell = (
            "&mdash;" if record.opponent_team_this_week is None else _esc(record.opponent_team_this_week)
        )
        has_baseline = "1" if record.salary is not None and record.projection is not None else "0"
        value = record.value
        value_sort = "" if value is None else f"{value:.4f}"
        projected_own_sort = "" if record.ownership is None else f"{record.ownership.projected_ownership:.4f}"
        ceiling_sort = "" if record.ceiling_multiplier is None else f"{record.ceiling_multiplier:.4f}"
        rows.append(
            f'<tr class="player-row" data-position="{_esc(record.position)}" '
            f'data-view="{_player_view(record.position)}" data-has-baseline="{has_baseline}" '
            f'data-value="{value_sort}" onclick="togglePlayerExpand(this)">'
            f'<td data-sort-value="{_esc(identity.display_name)}">{player_cell}</td>'
            f'<td data-sort-value="{_esc(record.position)}">{_esc(record.position)}</td>'
            f'<td data-sort-value="{_esc(record.team)}">{_esc(record.team)}</td>'
            f"<td>{opponent_cell}</td>"
            f'<td data-sort-value="{record.salary or ""}">{_render_salary_cell(record)}</td>'
            f'<td data-sort-value="{record.projection if record.projection is not None else ""}">{_render_projection_cell(record)}</td>'
            f'<td data-sort-value="{ceiling_sort}">{_render_ceiling_cell(record)}</td>'
            f'<td data-sort-value="{value_sort}">{_render_value_cell(record)}</td>'
            f'<td data-sort-value="{projected_own_sort}">{_render_ownership_cell(record)}</td>'
            f"<td>{_render_stack_cell(record)}</td>"
            "</tr>"
            f'<tr class="player-expand-row" hidden><td colspan="10">{_render_player_expand_content(record)}</td></tr>'
        )

    header_cells = []
    all_headers = ["Player", "Pos", "Team", "Opp", "Salary", "Projection", "Ceiling", "Value", "Proj Own%", "Stack"]
    sortable_by_index = {idx: sort_type for idx, _label, sort_type in _SORTABLE_COLUMNS}
    for idx, label in enumerate(all_headers):
        if idx in sortable_by_index:
            sort_type = sortable_by_index[idx]
            header_cells.append(
                f'<th class="sortable" onclick="sortPlayerDetailRows({idx}, \'{sort_type}\', this)">{label}</th>'
            )
        else:
            header_cells.append(f"<th>{label}</th>")

    table = (
        '<div class="table-scroll"><table class="player-detail-table"><thead><tr>'
        + "".join(header_cells)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )

    return legend + view_filters + position_filters + search + viability + table


# ------------------------------------------------------------------------------------------
# Page shell
# ------------------------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #f8fafc; --bg2: #ffffff; --bg3: #f1f5f9;
  --fg: #1e293b; --fg2: #64748b; --fg3: #94a3b8;
  --accent: #4f46e5; --accent-light: #eef2ff;
  --green: #059669; --amber: #d97706; --red: #dc2626;
  --border: #e2e8f0; --radius: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f172a; --bg2: #1e293b; --bg3: #273549;
    --fg: #f1f5f9; --fg2: #94a3b8; --fg3: #64748b;
    --accent: #818cf8; --accent-light: #1e1b4b;
    --green: #34d399; --amber: #fbbf24; --red: #f87171;
    --border: #334155;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 20px 24px 48px;
  font-family: -apple-system, "Segoe UI", "Inter", sans-serif;
  background: var(--bg); color: var(--fg); font-size: 13px;
}
h1 { font-size: 1.35rem; color: var(--accent); margin: 0 0 2px; }
h3 { font-size: 0.95rem; margin: 18px 0 8px; }
.sub { color: var(--fg2); font-size: 0.82rem; margin: 0 0 16px; }
.tabs { display: flex; gap: 6px; margin: 16px 0; flex-wrap: wrap; }
.pos-filters { display: flex; gap: 6px; margin: 0 0 10px; flex-wrap: wrap; }
.tab, .pos-filter {
  padding: 7px 16px; border-radius: 20px; font-size: 0.82rem; font-weight: 600;
  cursor: pointer; background: var(--bg2); border: 1px solid var(--border); color: var(--fg2);
}
.tab.active, .pos-filter.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.panel { display: none; }
.panel.active { display: block; }
.table-scroll { width: 100%; overflow-x: auto; border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; background: var(--bg2); border-radius: var(--radius); overflow: hidden; }
.table-scroll table { min-width: 1100px; }
th {
  text-align: left; padding: 8px 10px; font-size: 0.68rem; text-transform: uppercase;
  letter-spacing: 0.03em; color: var(--fg2); border-bottom: 2px solid var(--border); white-space: nowrap;
}
td { padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.slot { font-weight: 700; color: var(--accent); }
.cell-main { font-weight: 600; }
.cell-sub { color: var(--fg2); font-size: 0.78rem; }
.muted { color: var(--fg3); }
.na { color: var(--fg3); font-style: italic; }
.diff { color: var(--fg2); }
.empty-state { color: var(--fg2); padding: 16px; font-style: italic; }
.lineups-wrap { display: flex; flex-direction: column; gap: 18px; }
.lineup-card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px; }
.lineup-head { font-weight: 700; font-size: 1.02rem; margin-bottom: 10px; }
.lineup-head .meta { font-weight: 400; color: var(--fg2); font-size: 0.8rem; }
.roster-table { margin-bottom: 12px; }
.rationale {
  background: var(--accent-light); border-left: 3px solid var(--accent);
  border-radius: 6px; padding: 10px 14px; margin-top: 8px;
}
.rationale-label { font-weight: 700; color: var(--accent); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.03em; }
.rationale p { margin: 4px 0 0; line-height: 1.5; }
.lineup-notes { margin-top: 8px; color: var(--fg2); font-size: 0.8rem; }
.badges { margin-top: 3px; display: flex; flex-wrap: wrap; gap: 4px; }
.badge {
  display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 0.65rem;
  font-weight: 700; white-space: nowrap;
}
.badge-leader { background: var(--green); color: #fff; }
.badge-lineup { background: var(--accent); color: #fff; }
.badge-validated { background: var(--accent-light); color: var(--accent); border: 1px solid var(--accent); }
.badge-warning { background: var(--amber); color: #1e1b0a; }
.badge-danger { background: var(--red); color: #fff; }
.badge-unvalidated {
  background: transparent; color: var(--fg2); border: 1px solid var(--fg3); font-weight: 600;
}
.badge-chalk { background: var(--amber); color: #1e1b0a; }
.badge-leverage { background: var(--green); color: #fff; }
.badge-stack-primary { background: var(--accent); color: #fff; }
.badge-stack-bringback { background: var(--accent-light); color: var(--accent); border: 1px solid var(--accent); }
.legend { margin-bottom: 12px; background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 14px; }
.legend summary { cursor: pointer; font-weight: 600; color: var(--fg2); font-size: 0.82rem; }
.legend-body { margin-top: 8px; font-size: 0.8rem; color: var(--fg2); line-height: 1.5; }
.legend-body p { margin: 6px 0; }
.legend-body code { background: var(--bg3); padding: 1px 4px; border-radius: 3px; }
.search-row { margin-bottom: 10px; display: flex; align-items: center; gap: 10px; }
#player-search {
  flex: 1; max-width: 360px; padding: 7px 12px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg2); color: var(--fg); font-size: 0.85rem;
}
.search-count { color: var(--fg2); font-size: 0.78rem; }
.view-filters { display: flex; gap: 6px; margin: 0 0 8px; flex-wrap: wrap; }
.view-filter {
  padding: 7px 16px; border-radius: 20px; font-size: 0.82rem; font-weight: 700;
  cursor: pointer; background: var(--bg2); border: 1px solid var(--accent); color: var(--accent);
}
.view-filter.active { background: var(--accent); color: #fff; }
.viability-row {
  margin-bottom: 10px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  font-size: 0.8rem; color: var(--fg2);
}
.viability-row input[type="number"] { width: 70px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg2); color: var(--fg); }
.viability-row button {
  padding: 5px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg2);
  color: var(--fg2); cursor: pointer; font-size: 0.78rem;
}
th.sortable { cursor: pointer; user-select: none; }
th.sortable:after { content: " ⇅"; color: var(--fg3); font-size: 0.7rem; }
th.sortable.sort-asc:after { content: " ↑"; color: var(--accent); }
th.sortable.sort-desc:after { content: " ↓"; color: var(--accent); }
tr.player-row { cursor: pointer; }
tr.player-row:hover { background: var(--bg3); }
tr.player-expand-row td { background: var(--bg3); border-bottom: 2px solid var(--border); padding: 12px 16px; }
.expand-grid { display: flex; flex-wrap: wrap; gap: 16px; }
.expand-block { min-width: 150px; }
.expand-label {
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--fg2);
  font-weight: 700; margin-bottom: 3px;
}
.trend-up { color: var(--green); font-weight: 700; }
.trend-down { color: var(--red); font-weight: 700; }
.ge-pair { display: flex; gap: 14px; }
.ge-pair > div { flex: 1; min-width: 90px; }
.cell-injury { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.cell-injury:first-child { margin-top: 0; }
footer { margin-top: 28px; color: var(--fg3); font-size: 0.72rem; }
"""

_JS = """
function showTab(name) {
  document.querySelectorAll('.tab').forEach(function (t) {
    t.classList.toggle('active', t.dataset.panel === name);
  });
  document.querySelectorAll('.panel').forEach(function (p) {
    p.classList.toggle('active', p.id === 'panel-' + name);
  });
}

var activePositionFilter = 'ALL';
var activeViewFilter = 'ALL';

function setPositionFilter(pos, btn) {
  activePositionFilter = pos;
  document.querySelectorAll('.pos-filter').forEach(function (b) {
    b.classList.toggle('active', b === btn);
  });
  filterPlayerDetailRows();
}

function setViewFilter(view, btn) {
  activeViewFilter = view;
  document.querySelectorAll('.view-filter').forEach(function (b) {
    b.classList.toggle('active', b === btn);
  });
  filterPlayerDetailRows();
}

function togglePlayerExpand(row) {
  var expandRow = row.nextElementSibling;
  if (!expandRow || !expandRow.classList.contains('player-expand-row')) { return; }
  var expanded = row.classList.toggle('expanded');
  expandRow.hidden = !expanded;
}

function filterPlayerDetailRows() {
  var query = document.getElementById('player-search').value.trim().toLowerCase();
  var showUnviable = document.getElementById('show-unviable').checked;
  var floorInput = document.getElementById('value-floor');
  var floor = floorInput && floorInput.value !== '' ? parseFloat(floorInput.value) : null;
  var rows = document.querySelectorAll('#panel-players tr.player-row');
  var shown = 0;
  rows.forEach(function (row) {
    var textMatch = row.textContent.toLowerCase().indexOf(query) !== -1;
    var posMatch = activePositionFilter === 'ALL' || row.dataset.position === activePositionFilter;
    var viewMatch = activeViewFilter === 'ALL' || row.dataset.view === activeViewFilter;
    var hasBaseline = row.dataset.hasBaseline === '1';
    var viableMatch = showUnviable || hasBaseline;
    var rawValue = row.dataset.value;
    var value = rawValue !== '' ? parseFloat(rawValue) : null;
    var floorMatch = floor === null || value === null || value >= floor;
    var match = textMatch && posMatch && viewMatch && viableMatch && floorMatch;
    row.style.display = match ? '' : 'none';
    var expandRow = row.nextElementSibling;
    if (expandRow && expandRow.classList.contains('player-expand-row')) {
      if (!match) { expandRow.hidden = true; row.classList.remove('expanded'); }
    }
    if (match) { shown += 1; }
  });
  var counter = document.getElementById('player-search-count');
  if (counter) { counter.textContent = shown + ' / ' + rows.length + ' players'; }
}

function resetPlayerDetailFilters() {
  document.getElementById('player-search').value = '';
  document.getElementById('show-unviable').checked = false;
  document.getElementById('value-floor').value = '';
  activePositionFilter = 'ALL';
  activeViewFilter = 'ALL';
  document.querySelectorAll('.pos-filter').forEach(function (b) { b.classList.toggle('active', b.dataset.pos === 'ALL'); });
  document.querySelectorAll('.view-filter').forEach(function (b) { b.classList.toggle('active', b.dataset.view === 'ALL'); });
  filterPlayerDetailRows();
}

function sortPlayerDetailRows(colIndex, type, headerEl) {
  var table = headerEl.closest('table');
  var tbody = table.querySelector('tbody');
  var mainRows = Array.prototype.slice.call(tbody.querySelectorAll('tr.player-row'));
  var pairs = mainRows.map(function (row) {
    var next = row.nextElementSibling;
    var expandRow = (next && next.classList.contains('player-expand-row')) ? next : null;
    return [row, expandRow];
  });
  var desc = headerEl.dataset.sortDir !== 'desc';
  table.querySelectorAll('th.sortable').forEach(function (th) {
    th.classList.remove('sort-asc', 'sort-desc');
    delete th.dataset.sortDir;
  });
  headerEl.dataset.sortDir = desc ? 'desc' : 'asc';
  headerEl.classList.add(desc ? 'sort-desc' : 'sort-asc');
  pairs.sort(function (a, b) {
    var va = a[0].children[colIndex].dataset.sortValue;
    var vb = b[0].children[colIndex].dataset.sortValue;
    if (type === 'num') {
      var na = (va === '' || va === undefined) ? -Infinity : parseFloat(va);
      var nb = (vb === '' || vb === undefined) ? -Infinity : parseFloat(vb);
      return desc ? nb - na : na - nb;
    }
    var sa = (va || '').toLowerCase();
    var sb = (vb || '').toLowerCase();
    if (sa < sb) { return desc ? 1 : -1; }
    if (sa > sb) { return desc ? -1 : 1; }
    return 0;
  });
  pairs.forEach(function (pair) {
    tbody.appendChild(pair[0]);
    if (pair[1]) { tbody.appendChild(pair[1]); }
  });
}

document.addEventListener('DOMContentLoaded', function () {
  var counter = document.getElementById('player-search-count');
  if (counter) {
    var total = document.querySelectorAll('#panel-players tr.player-row').length;
    counter.textContent = total + ' / ' + total + ' players';
  }

  // Default the soft viability floor to this slate's own bottom-quartile value, applied
  // immediately (not just hinted) so the page opens already scannable -- never a silent/hardcoded
  // cutoff, though: the input stays visible, pre-filled (not blank), and one click (Reset filters)
  // from showing everyone again.
  var floorInput = document.getElementById('value-floor');
  if (floorInput) {
    var values = Array.prototype.slice.call(document.querySelectorAll('#panel-players tr.player-row'))
      .map(function (row) { return row.dataset.value; })
      .filter(function (v) { return v !== '' && v !== undefined; })
      .map(function (v) { return parseFloat(v); })
      .sort(function (a, b) { return a - b; });
    if (values.length > 0) {
      var defaultFloor = values[Math.floor(values.length * 0.25)];
      floorInput.value = defaultFloor.toFixed(2);
    }
  }
  filterPlayerDetailRows();
});
"""

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NFL DFS Optimizer &mdash; Weekly Dashboard</title>
<style>__CSS__</style>
</head>
<body>
<h1>NFL DFS Optimizer &mdash; Weekly Dashboard</h1>
<p class="sub">__SUBTITLE__ &middot; generated __GENERATED_AT__</p>

<div class="tabs">
  <div class="tab active" data-panel="lineups" onclick="showTab('lineups')">Lineups</div>
  <div class="tab" data-panel="exposure" onclick="showTab('exposure')">Exposure</div>
  <div class="tab" data-panel="players" onclick="showTab('players')">Player Detail</div>
  <div class="tab" data-panel="slate" onclick="showTab('slate')">Slate Overview</div>
</div>

<div id="panel-lineups" class="panel active">__LINEUPS_HTML__</div>
<div id="panel-exposure" class="panel">__EXPOSURE_HTML__</div>
<div id="panel-players" class="panel">__PLAYERS_HTML__</div>
<div id="panel-slate" class="panel">__SLATE_HTML__</div>

<footer>NFL DFS Optimizer &mdash; static, offline dashboard. No external network calls; no data leaves this file.</footer>

<script>__JS__</script>
</body>
</html>
"""


def _build_lineup_membership(weekly_output: WeeklyOutput) -> dict[str, list[int]]:
    """`canonical_id -> [1-based lineup indices it appears in]`, for the Player Detail tab's
    "In L1/L2/L3" cross-reference badge."""
    membership: dict[str, list[int]] = {}
    for i, lineup in enumerate(weekly_output.lineups, start=1):
        for player in lineup.players:
            membership.setdefault(player.canonical_id, []).append(i)
    return membership


def render_dashboard_html(
    weekly_output: WeeklyOutput,
    player_details: list[PlayerDetailRecord],
    slate_games: list[SlateGameRow] | None = None,
) -> str:
    """Render the full weekly dashboard as one self-contained HTML string -- inline CSS, vanilla
    JS for tab-switching and the Player Detail search filter only, no external dependency of any
    kind. Pure rendering: every value comes from `weekly_output`/`player_details`/`slate_games`
    as-is, per this module's docstring.

    `slate_games` is new and optional (default `None`, same "unpopulated, not touched" default
    pattern `GameEnvironmentScore.injury_uncertainty_flag` uses) so every existing caller of this
    function keeps working unchanged and just gets an empty Slate Overview tab.
    """
    lineup_membership = _build_lineup_membership(weekly_output)

    lineups_html = _render_lineups_tab(weekly_output)
    exposure_html = _render_exposure_tab(weekly_output)
    players_html = _render_player_detail_tab(player_details, lineup_membership)
    slate_html = _render_slate_overview_tab(slate_games or [])

    if player_details:
        first = player_details[0]
        subtitle = f"Season {first.season}, Week {first.week} &middot; {len(weekly_output.lineups)} lineup(s), {len(player_details)} player(s) detailed"
    else:
        subtitle = f"{len(weekly_output.lineups)} lineup(s), 0 player(s) detailed"
    if slate_games:
        subtitle += f" &middot; {len(slate_games)} game(s) in slate overview"

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html_out = _PAGE_TEMPLATE
    html_out = html_out.replace("__CSS__", _CSS)
    html_out = html_out.replace("__JS__", _JS)
    html_out = html_out.replace("__SUBTITLE__", subtitle)
    html_out = html_out.replace("__GENERATED_AT__", generated_at)
    html_out = html_out.replace("__LINEUPS_HTML__", lineups_html)
    html_out = html_out.replace("__EXPOSURE_HTML__", exposure_html)
    html_out = html_out.replace("__PLAYERS_HTML__", players_html)
    html_out = html_out.replace("__SLATE_HTML__", slate_html)
    return html_out


def write_dashboard_html(
    path: str,
    weekly_output: WeeklyOutput,
    player_details: list[PlayerDetailRecord],
    slate_games: list[SlateGameRow] | None = None,
) -> None:
    """Render and write the dashboard to a real file on disk, ready to open directly in a
    browser -- no server, no build step. Mirrors `output/csv_export.py`'s
    `write_dk_csv_file`'s split between pure render and file I/O, for the same testability reason
    (callers/tests that only need the HTML string never have to touch the filesystem)."""
    html_out = render_dashboard_html(weekly_output, player_details, slate_games)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_out)
