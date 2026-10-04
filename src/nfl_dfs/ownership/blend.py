"""Blends RotoGrinders' `POWN` with Footballguys' roster percentages (Chris, 2026-10-04: "it's
projection information so there isn't a gold source").

Per main-slate row, matched on (normalized name, position):
  - both sources have a real read      -> simple mean of the two
  - RotoGrinders only (FBG unlisted)   -> RotoGrinders as-is (FBG lists only the top of each position)
  - Footballguys only                  -> Footballguys, when RotoGrinders is BLANK for the whole team

"Blank for the whole team" = every main-slate row for that RotoGrinders team abbreviation is 0.0. That
is how RotoGrinders reports a game whose ownership it has not published yet (week 4: `0.00%` for
Purdy and Mahomes), not a real estimate -- so those 0.0s are treated as missing, never averaged in.
A blank-team player that Footballguys also doesn't list stays 0.0: it is below FBG's listing floor.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace

from nfl_dfs.ingestion.footballguys_ownership import FbgOwnershipRow
from nfl_dfs.ingestion.rotogrinders import LineupHqOwnershipRow

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv)\b")


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z ]", "", name.lower().replace(".", ""))
    return re.sub(r"\s+", " ", _SUFFIX.sub("", cleaned)).strip()


@dataclass(frozen=True)
class OwnershipBlendReport:
    blank_teams: list[str]
    blended: int  # both sources
    fbg_only: int  # RG blank team, FBG read used
    rg_only: int  # FBG unlisted, RG kept
    unresolved_blank: int  # RG blank team and FBG unlisted -> left at 0.0
    fbg_unmatched: list[str]  # FBG rows that matched no RG row (name/position), for QA


def blank_teams(rows: list[LineupHqOwnershipRow]) -> set[str]:
    by_team: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.team:
            by_team[r.team].append(r.projected_ownership or 0.0)
    return {t for t, vals in by_team.items() if vals and all(v == 0.0 for v in vals)}


def blend_ownership_rows(
    rows: list[LineupHqOwnershipRow], fbg_rows: list[FbgOwnershipRow]
) -> tuple[list[LineupHqOwnershipRow], OwnershipBlendReport]:
    blank = blank_teams(rows)
    fbg_by_key: dict[tuple[str, str], list[float]] = defaultdict(list)
    for f in fbg_rows:
        fbg_by_key[(normalize_name(f.name), f.position)].append(f.projected_ownership)
    matched: set[tuple[str, str]] = set()
    blended = fbg_only = rg_only = unresolved = 0
    out: list[LineupHqOwnershipRow] = []
    for r in rows:
        key = (normalize_name(r.name), r.position)
        fbg_vals = fbg_by_key.get(key)
        fbg = fbg_vals[0] if fbg_vals and len(fbg_vals) == 1 else None  # ambiguous duplicate -> skip
        if fbg is not None:
            matched.add(key)
        rg_blank = r.team in blank
        if fbg is None:
            if rg_blank:
                unresolved += 1
            else:
                rg_only += 1
            out.append(r)
        elif rg_blank:
            fbg_only += 1
            out.append(replace(r, projected_ownership=fbg))
        else:
            blended += 1
            out.append(replace(r, projected_ownership=(r.projected_ownership + fbg) / 2))
    unmatched = sorted(f"{f.name} ({f.position})" for f in fbg_rows if (normalize_name(f.name), f.position) not in matched)
    return out, OwnershipBlendReport(sorted(blank), blended, fbg_only, rg_only, unresolved, unmatched)
