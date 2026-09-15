# ADR-0031: Injury-feed staleness check -- a real, reproducible measurement, and a structural finding neither source alone can fix

**Status:** Accepted (investigation complete, both new modules implemented, unit-tested, and
live-verified; no dashboard wiring changes made this round -- see "Decision" and "Consequences")
**Date:** 2026-09-15
**Owner:** Chris, per his explicit direction ("injury-feed staleness" investigation)
**Related:** `src/nfl_dfs/ingestion/rotogrinders_injuries.py` (the only injury source currently
wired into the dashboard), `src/nfl_dfs/ingestion/official_injury_report.py` (new this round),
`scripts/injury_staleness_check.py` (new this round), `docs/PRD.md` (ADR-0017's injury/role
uncertainty flag design)

## Context

An earlier session found something described as a "1/23 match rate" between RotoGrinders'
Situation Room injury report (this project's only wired injury source, feeding
`PlayerInjuryDetail`/`compute_injury_uncertainty_flag`) and "LineupHQ" -- but that number was never
committed to this repo as a script, a fixture, or even a note. It was an ad-hoc, in-session
calculation that's now unrecoverable except as a remembered figure. This round set out to redo
that investigation properly: measure it for real, and commit the measurement so it can be
re-run in a future week rather than lost again.

**First real decision: LineupHQ was the wrong comparison target.** LineupHQ and Situation Room are
BOTH RotoGrinders products (confirmed in `rotogrinders_injuries.py`'s own module docstring --
Situation Room was chosen over parsing LineupHQ's `INJURY` field specifically because Situation
Room has a real graded severity score). Comparing RotoGrinders against RotoGrinders can't cleanly
distinguish "genuine staleness" from "the same underlying vendor feed populated on two different
internal update cadences." Chris chose instead to compare against a genuinely independent source:
the NFL's own official weekly injury report, via `nfl_data_py.import_injuries()` -- already
confirmed reliable and gsis_id-keyed (the same join space `receiving_profile.py`/
`qb_rushing_profile.py`/`player_detail.py` already use), no new vendor dependency.

## Decision

Built two new pieces, both real and committed (unlike the original lost investigation):

1. **`ingestion/official_injury_report.py`** -- wraps `nfl_data_py.import_injuries()`. Confirmed
   live (2026-09-15): this feed returns **one row per (player, week)**, not a daily practice-report
   time series -- already collapsed to what appears to be that week's final pre-game designation
   (`report_status`: Out/Doubtful/Questionable/`None`; `practice_status`: Full/Limited/DNP/`None`).
   `latest_week_entries` pulls out just the most recently aggregated week, since a caller
   shouldn't assume the current week's official report is available yet (see Finding 1 below).

2. **`scripts/injury_staleness_check.py`** -- a live comparison script, reusing the same DK/PFF/
   RotoGrinders/Footballguys reconciliation bootstrap `live_integration_check_dashboard.py` already
   uses, bridging RotoGrinders' native-id space (Situation Room's join key) to nflverse's gsis_id
   space (the official report's join key) through the already-reconciled `PlayerIdentity` pool --
   no new crosswalk built. Reports three things: players on Situation Room only, players on the
   official report only (a real coverage gap, if Situation Room should have them and doesn't), and
   -- for players on BOTH with a real official game-status -- an agreement/disagreement rate
   against a disclosed, non-backtested rough mapping (Out->`"O"`, Doubtful->`"D"`,
   Questionable->`"Q"`).

## Findings (real, live, 2026-09-15 -- not a hypothetical)

**Finding 1 (the structural one, more important than any single-week number): nflverse's official
feed is not a real-time source -- it lags by construction, and RotoGrinders' Situation Room has no
archive at all, so a genuine same-week staleness comparison is not currently possible with this
pipeline, full stop.** Live-confirmed: `import_injuries([2026])` returns data through week 1 only
(182 rows) -- there is no week 2 data yet, even though RotoGrinders' Situation Room CURRENT live
pull already reflects a later week's injury designations (RotoGrinders exposes only a live current
snapshot, no history/archive endpoint -- confirmed in `rotogrinders_injuries.py`'s original
investigation, re-confirmed live this round: the CSV endpoint has no week/date param that changes
the response). The practical consequence, observed directly in this round's live run: **zero**
players landed in the "present on both, comparable game-status" bucket at all -- every player who
appeared on both feeds this pull was being compared across two different, non-overlapping weeks
(Situation Room's live "next week" read vs. the official feed's now-historical "last week's final
report"), so no apples-to-apples agreement/disagreement rate could be computed this round. This is
not a bug in the comparison script -- it's the actual, disclosed shape of what these two sources
can offer each other right now.

**Finding 2 (a real, if week-mismatch-confounded, coverage gap):** 3 players carried a genuine
official `Questionable` game-status designation for the most recent aggregated week (Jeremiyah
Love/ARI-Ankle, Rome Odunze/CHI-Calf, LeQuint Allen Jr./JAX-Hip) with **no corresponding row at all**
on RotoGrinders' current Situation Room pull. Read with real caution given Finding 1: because the
official rows are for a now-past week, this is at least partly (perhaps entirely) explained by
those players' designations having simply resolved by the time of this pull (they played, or their
status cleared) -- NOT necessarily evidence Situation Room missed a live, still-relevant
designation. Reported honestly as a real observation from a real run, not oversold as a confirmed
staleness failure.

**Finding 3 (confirms and extends `rotogrinders_injuries.py`'s own unresolved question):** this
round's live pull observed **`"D"` (Doubtful)** on Situation Room for the first time -- 3 real
rows (Josh Jacobs, Tua Tagovailoa, Kyler Murray) -- resolving that module's own flagged open
question ("`'D'` NOT confirmed live this pass... re-verify this once real data from a week closer
to game day is available"). `RESOLVED_INJURY_STATUSES` in `game_environment/score.py` still
correctly treats `"D"` as unresolved/uncertain (only `"O"` is a resolved status) -- this finding
confirms that treatment was already correct, it doesn't require a code change.

## Consequences

**No dashboard wiring changes this round.** Finding 1 means there is currently no reliable,
same-week signal to wire in even if this project wanted to flag "Situation Room may be stale" on
the dashboard -- the comparison itself can't be run validly on a rolling basis without a real fix
to the underlying data-availability mismatch (see below). Shipping a "staleness warning" derived
from a structurally invalid comparison would be worse than shipping nothing.

**The real fix, if this is worth pursuing further, is a longitudinal snapshot archive, not a
better one-shot script.** Since RotoGrinders exposes no history and nflverse's official feed only
becomes available in arrears, the only way to genuinely validate "was Situation Room's read
correct, as of when it mattered" is to start SAVING Situation Room's live pulls on an ongoing
basis (e.g., a daily/weekly cron snapshot into `resultsdb_backfill.py`-style local storage) and
retrospectively compare each saved snapshot against nflverse's official report once THAT week's
official data becomes available (one week later). That's real, scoped follow-on infrastructure
work, not built this round -- named here so it isn't lost the way the original "1/23" finding was.

**This module set is real, committed, and reproducible** even though it didn't produce a clean
match-rate percentage this round -- `scripts/injury_staleness_check.py` can be re-run any week
and will report Finding-1-style results (0 or few comparable players) until the archive-based fix
above is built, at which point it becomes the retrospective comparison tool that fix would need.
