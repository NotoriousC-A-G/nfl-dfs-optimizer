"""NFL-native retrospective: did our real pre-game signals predict what actually happened, plus
what the real missed high scorers have in common. Same JOB as the sister MLB project's own
`postmortem/retrospective.py` (read directly before porting) -- comparing a high-tier group's real
settled points against a low-tier group's -- but every signal here is grounded in a field this
project's own `PlayerDetailRecord` actually carries. None of MLB's baseball-specific checks (HR
stars, pitch matchup, batting order, bullpen ISO, pitcher-environment outlier) are ported; those
would just be nulled out forever against fields this project has no equivalent of.

**The four signals chosen, and why these four:** every other real per-player field on
`PlayerDetailRecord` this early in a season is trailing-stat-driven (`role_share`, `snap_share`,
receiving/QB-rushing profiles all need completed weeks `1..W-1`, confirmed by that module's own
docstring, and this project is currently two weeks into a season) -- checking those now would
mostly produce "insufficient sample" nulls, not real signal. The four below are each populated
from schedule/odds data or this week's own slate-construction outputs, not trailing box scores, so
they're the ones actually likely to have real population this early:

  1. Ownership (chalk vs. leverage) -- `ownership.is_chalk`/`is_leverage` (ADR-0026's live
     chalk/leverage layer).
  2. Game environment (`game_environment.composite_score`) -- schedule/odds-driven, available
     from week 1.
  3. Stack context primary-candidate flag (`stack_context.is_primary_stack_candidate`) -- derived
     from this week's own `StackProfile`, not trailing data.
  4. Ceiling multiplier (`ceiling_multiplier`, Component A) -- RB/WR only, and per this project's
     own live runs, still frequently unpopulated ("no Component A ceiling signal") this early;
     included anyway since the per-signal guard below (both groups must be non-empty) naturally
     excludes it from a grade when there's nothing to check, exactly like every guard in this
     module and in MLB's own version.

**Every threshold below is a disclosed draft, not backtested** -- same "not empirically derived,
flagged as a starting point" convention this project already uses for the GPP-grade bands and the
DST-correlation objective fractions (ADR-0019/0020).
"""

from __future__ import annotations

import math

from nfl_dfs.tracking.postmortem.models import CeilingPatterns, ProcessGrade

# Disclosed draft (not backtested): the per-player-point-delta scale at which a signal check is
# considered "maximally strong". MLB's own DELTA_SATURATION (5.0) was calibrated to "about half a
# typical HR" on its own scoring scale; NFL DK's higher per-player point totals warrant a somewhat
# higher value, not a blind reuse of MLB's constant.
_DELTA_SATURATION = 6.0

_GRADE_MIN_EVALUATED = 3
_GRADE_BANDS: tuple[tuple[str, float], ...] = (("A", 86.0), ("B", 71.0), ("C", 57.0), ("D", 43.0), ("F", 0.0))

# Disclosed draft thresholds for each signal's high/low split.
_GAME_ENV_HIGH = 60.0
_GAME_ENV_LOW = 40.0
_CEILING_MULTIPLIER_HIGH = 1.15
_CEILING_MULTIPLIER_LOW = 1.0


def _avg(values: list[float]) -> float:
    return sum(values) / len(values)


def compute_signal_verdicts(player_pool: list[dict], actual_points_by_canonical_id: dict[str, float]) -> dict:
    """Real signal-vs-outcome checks over one week's full snapshot pool. A signal only appears in
    the returned dict when BOTH its high and low groups are non-empty -- same "excluded, not
    counted as zero" guard MLB's own module uses, so a signal that simply has no real population
    this week (e.g. ceiling_multiplier, most weeks so far) doesn't silently penalize the grade."""
    rows = []
    for r in player_pool:
        canonical_id = (r.get("identity") or {}).get("canonical_id")
        actual = actual_points_by_canonical_id.get(canonical_id) if canonical_id else None
        if actual is None:
            continue
        rows.append({**r, "_actual": actual})

    verdicts: dict[str, dict] = {}
    if not rows:
        return verdicts

    # 1. Ownership: chalk vs. leverage.
    chalk = [r for r in rows if (r.get("ownership") or {}).get("is_chalk")]
    leverage = [r for r in rows if (r.get("ownership") or {}).get("is_leverage")]
    if chalk and leverage:
        avg_chalk = _avg([r["_actual"] for r in chalk])
        avg_leverage = _avg([r["_actual"] for r in leverage])
        delta = round(avg_leverage - avg_chalk, 2)
        verdicts["ownership_leverage"] = {
            "worked": delta > 0,
            "signal": "Ownership (leverage vs. chalk)",
            "detail": f"Leverage plays averaged {avg_leverage:.1f} pts vs {avg_chalk:.1f} for chalk plays ({delta:+.1f})",
            "delta": delta,
            "n_high": len(leverage),
            "n_low": len(chalk),
        }

    # 2. Game environment.
    high_env = [r for r in rows if ((r.get("game_environment") or {}).get("composite_score") or -1) >= _GAME_ENV_HIGH]
    low_env = [
        r
        for r in rows
        if (r.get("game_environment") or {}).get("composite_score") is not None
        and (r.get("game_environment") or {}).get("composite_score") <= _GAME_ENV_LOW
    ]
    if high_env and low_env:
        avg_high = _avg([r["_actual"] for r in high_env])
        avg_low = _avg([r["_actual"] for r in low_env])
        delta = round(avg_high - avg_low, 2)
        verdicts["game_environment"] = {
            "worked": delta > 0,
            "signal": f"Game Environment (>={_GAME_ENV_HIGH:.0f} vs <={_GAME_ENV_LOW:.0f})",
            "detail": f"High-environment players averaged {avg_high:.1f} pts vs {avg_low:.1f} for low-environment ({delta:+.1f})",
            "delta": delta,
            "n_high": len(high_env),
            "n_low": len(low_env),
        }

    # 3. Stack context: primary-stack candidates vs. everyone else.
    primary = [r for r in rows if (r.get("stack_context") or {}).get("is_primary_stack_candidate")]
    non_primary = [r for r in rows if not (r.get("stack_context") or {}).get("is_primary_stack_candidate")]
    if primary and non_primary:
        avg_primary = _avg([r["_actual"] for r in primary])
        avg_non = _avg([r["_actual"] for r in non_primary])
        delta = round(avg_primary - avg_non, 2)
        verdicts["primary_stack_candidate"] = {
            "worked": delta > 0,
            "signal": "Primary Stack Candidate",
            "detail": f"Flagged primary-stack players averaged {avg_primary:.1f} pts vs {avg_non:.1f} for the rest of the pool ({delta:+.1f})",
            "delta": delta,
            "n_high": len(primary),
            "n_low": len(non_primary),
        }

    # 4. Ceiling multiplier (Component A, RB/WR only).
    high_ceiling = [r for r in rows if (r.get("ceiling_multiplier") or 0) >= _CEILING_MULTIPLIER_HIGH]
    low_ceiling = [
        r for r in rows if r.get("ceiling_multiplier") is not None and r.get("ceiling_multiplier") <= _CEILING_MULTIPLIER_LOW
    ]
    if high_ceiling and low_ceiling:
        avg_high = _avg([r["_actual"] for r in high_ceiling])
        avg_low = _avg([r["_actual"] for r in low_ceiling])
        delta = round(avg_high - avg_low, 2)
        verdicts["ceiling_multiplier"] = {
            "worked": delta > 0,
            "signal": f"Ceiling Multiplier (>={_CEILING_MULTIPLIER_HIGH} vs <={_CEILING_MULTIPLIER_LOW})",
            "detail": f"High-ceiling-multiplier players averaged {avg_high:.1f} pts vs {avg_low:.1f} for low ({delta:+.1f})",
            "delta": delta,
            "n_high": len(high_ceiling),
            "n_low": len(low_ceiling),
        }

    return verdicts


def compute_process_grade(verdicts: dict) -> ProcessGrade:
    """Weighted signal-strength aggregation -- direct port of MLB's own `compute_process_grade`
    formula (tanh-normalized delta strength, sqrt(min(n_high, n_low)) sample-size weighting,
    percent-score mapped to a letter grade). The formula itself is sport-agnostic; only the
    `verdicts` it's fed (from `compute_signal_verdicts` above) are NFL-specific."""
    if not verdicts:
        return ProcessGrade(letter="N/A", score=None, signals_hit=0, signals_evaluated=0, summary="No signals evaluated.")

    hits: list[str] = []
    missed: list[str] = []
    strength_sum = 0.0
    weight_sum = 0.0
    evaluated = 0

    for data in verdicts.values():
        worked = data.get("worked")
        if worked is None:
            continue
        evaluated += 1
        friendly = data.get("signal", "signal")
        (hits if worked else missed).append(friendly)

        delta = data.get("delta")
        strength = math.tanh(delta / _DELTA_SATURATION) if delta is not None else (1.0 if worked else -1.0)

        legs = [data[k] for k in ("n_high", "n_low") if isinstance(data.get(k), (int, float)) and data[k] > 0]
        weight = math.sqrt(min(legs)) if len(legs) >= 2 else 1.0

        strength_sum += strength * weight
        weight_sum += weight

    if evaluated < _GRADE_MIN_EVALUATED or weight_sum <= 0:
        return ProcessGrade(
            letter="N/A",
            score=None,
            signals_hit=len(hits),
            signals_evaluated=evaluated,
            signal_names_hit=hits,
            signal_names_missed=missed,
            summary=f"Only {evaluated} signal(s) evaluable -- need {_GRADE_MIN_EVALUATED}+ for a grade.",
        )

    aggregate_strength = strength_sum / weight_sum
    percent_score = ((aggregate_strength + 1.0) / 2.0) * 100.0
    letter = next(band_letter for band_letter, band_floor in _GRADE_BANDS if percent_score >= band_floor)

    return ProcessGrade(
        letter=letter,
        score=round(percent_score, 1),
        signals_hit=len(hits),
        signals_evaluated=evaluated,
        signal_names_hit=hits,
        signal_names_missed=missed,
        summary=f"{len(hits)}/{evaluated} signals hit -- weighted strength {percent_score:.0f}/100",
    )


# Disclosed draft DK-NFL salary bands (MLB's own bands were tuned to its own $2K-$10K-ish scale;
# NFL DK salaries run roughly $2,500-$9,000, so these are a fresh, NFL-scaled draft, not a reuse
# of MLB's numbers).
def _salary_bucket(salary: int) -> str:
    if salary < 3500:
        return "<$3.5K"
    if salary < 5000:
        return "$3.5K-$5K"
    if salary < 6500:
        return "$5K-$6.5K"
    if salary < 8000:
        return "$6.5K-$8K"
    return "$8K+"


def extract_ceiling_patterns(missed_players: list) -> CeilingPatterns:
    """`missed_players` is a list of `PlayerOutcome` -- real high scorers not rostered by any
    lineup this week. See MLB's own `extract_ceiling_patterns` for the original design this
    mirrors (salary-bucket + position clustering, top-3 highlights)."""
    if not missed_players:
        return CeilingPatterns(missed_count=0)

    total = len(missed_players)
    buckets: dict[str, int] = {}
    for p in missed_players:
        b = _salary_bucket(p.salary or 0)
        buckets[b] = buckets.get(b, 0) + 1
    dominant_bucket = max(buckets, key=lambda k: buckets[k])
    if buckets[dominant_bucket] / total >= 0.4:
        salary_summary = f"{buckets[dominant_bucket]}/{total} missed in {dominant_bucket}"
    else:
        salary_summary = "Missed spread across salaries (no dominant bucket)"

    pos_counts: dict[str, int] = {}
    for p in missed_players:
        pos_counts[p.position] = pos_counts.get(p.position, 0) + 1
    dominant_pos = max(pos_counts, key=lambda k: pos_counts[k])
    if pos_counts[dominant_pos] / total >= 0.4:
        position_theme = f"{pos_counts[dominant_pos]}/{total} missed were {dominant_pos}"
    else:
        position_theme = "Position mix varied (" + ", ".join(f"{pos_counts[p]}{p}" for p in sorted(pos_counts)) + ")"

    top3 = sorted(missed_players, key=lambda p: p.actual or 0.0, reverse=True)[:3]
    highlights = [
        f"{p.display_name} @ ${p.salary:,} -- scored {p.actual:.1f} pts ({p.team} {p.position})" for p in top3
    ]

    return CeilingPatterns(
        missed_count=total, salary_bucket_summary=salary_summary, position_theme=position_theme, highlights=highlights
    )
