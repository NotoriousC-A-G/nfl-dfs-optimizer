# ADR-0042: Player-props ingestion from The Odds API (new endpoints, not a new vendor)

**Status:** Accepted (built, unit-tested, live-verified end to end against a real DK anchor pool)
**Date:** 2026-09-28
**Owner:** Data Integration Engineer
**Related:** `ingestion/odds_api.py` (the existing game-level integration this extends), ADR-0003
(implied-total population spec, same vendor), `normalization/matcher.py`/`identity.py` (ADR-0013
player-ID reconciliation, whose `normalize_name` primitive and DK-anchor posture this reuses),
`normalization/matcher.py`'s `_resolve_dst` (team-abbreviation DST matching, mirrored here),
ADR-0019/0020 (this project's convention for shipping a disclosed, not-yet-backtested signal
rather than withholding it until backtested)

## Context

`config.odds_api_key` and `ingestion/odds_api.py` are already live in production, pulling
game-level spreads/totals for `GameEnvironmentScore`'s implied-team-total component. Chris asked
for the same account's **player-props** markets — not a new vendor, not a new credential, the same
API surfaced through different endpoints. The motivating idea (his words): a DK player-prop line's
Over/Under pricing is "the odds themselves, not just the number" — an implied probability
distribution around a stat, not just a market-consensus median. That's a genuinely different kind
of signal from this project's existing vendor point-projection blend
(`projection/blend.py`'s `VENDOR_PROJECTION_SOURCES`), and from the game-level odds pull, which
only ever produced one number (`implied_total`) per team-week.

This ADR documents what was actually confirmed live, the real market subset chosen and why, the
odds→probability conversion, and is explicit that **none of this is backtested** — it's new
ingestion, reviewed for correctness of what it pulls and parses, not for whether prop-implied
volatility actually improves lineup construction. That's separate, future formula work (see
Consequences).

## Decision

### 1. Same vendor, two endpoints, confirmed live this session (2026-09-28)

- `GET /v4/sports/americanfootball_nfl/events?apiKey=...` — **0 credits**, confirmed via
  `x-requests-last: 0` on a real pull (17 upcoming 2026 events returned). Same full-franchise-name
  shape `odds_api.py`'s `TEAM_NAME_TO_ABBR` already covers.
- `GET /v4/sports/americanfootball_nfl/events/{id}/odds?...&regions=us&markets=<csv>` — real,
  per-event player props. Confirmed live against a real event (Eagles @ Bears, week 3): 7 requested
  markets × 1 region = **7 credits charged** (`x-requests-last: 7`), matching the previously
  documented cost model (markets × regions, charged regardless of how many of those markets
  actually return outcomes for that event). DraftKings was one of 8 bookmakers returned
  (`williamhill_us`, `fanduel`, `betrivers`, `betonlineag`, `betmgm`, `bovada`, `fanatics` also
  present) — filtered to DK only, reusing `odds_api.py`'s existing `DK_BOOKMAKER_KEY`, not a new
  constant.
- **A real, live-confirmed difference from the game-level endpoint**: on `/events/{id}/odds`, the
  bookmaker dict itself has no `"last_update"` field (`{"key", "title", "markets"}` only, confirmed
  live) — only each individual *market* does. `odds_api.py`'s `GameOdds.bookmaker_last_update`
  reads the bookmaker-level field on the different all-events endpoint; this module reads
  `market["last_update"]` instead, since that's what's actually present here.

### 2. Two real outcome shapes, not one — confirmed live, not assumed from the task brief

Over/under markets: `{"name": "Over"|"Under", "description": "<player>", "price": <int>,
"point": <float>}` — e.g. `{"name": "Over", "description": "Jalen Hurts", "price": 123,
"point": 1.5}` (player_pass_tds).

`player_anytime_td` is genuinely single-sided — DraftKings offers only a `"Yes"` outcome per
player, confirmed live (`{"name": "Yes", "description": "Saquon Barkley", "price": -115}`) — **no
`"point"` key at all**, not a null value. `parse_dk_player_props` uses "does this outcome carry a
`point` key" as the live signal for which shape it's looking at, rather than hard-coding by market
name, so a future single-sided market (`player_1st_td`/`player_last_td`, confirmed available per
DK but not yet exercised by this module) gets the same handling for free.

### 3. Market subset: 7 of the 18 confirmed DK markets, chosen against DK Classic's real scoring table

`DEFAULT_PROP_MARKETS` = `player_pass_yds`, `player_pass_tds`, `player_pass_interceptions`,
`player_rush_yds`, `player_receptions`, `player_reception_yds`, `player_anytime_td` — one market
per DK-scored offensive category (`docs/PRD.md` Section 3's scoring table: passing yards
0.04/yd + 300+ bonus, rushing/receiving yards 0.1/yd + 100+ bonus, full-PPR receptions, TDs), plus
`player_anytime_td` as an independent read on the single highest-value scoring event (+6, any TD
type) regardless of how the yardage lines price the path there.

Deliberately excluded, with reasons:
- `player_pass_attempts`/`player_pass_completions`/`player_rush_attempts` — volume proxies, not
  themselves a DK-scored category; the yardage/TD lines already price the outcome that matters,
  so these would be redundant signal for real credit cost.
- `player_rush_longest`/`player_reception_longest` — not a DK scoring input at all (DK scores
  total yards and yardage-threshold bonuses, never a single longest play).
- `player_1st_td`/`player_last_td` — strictly narrower than `player_anytime_td` (a 2+-TD player is
  neither first nor last scorer) and therefore redundant given anytime_td's inclusion.
- `player_kicking_points`/`player_field_goals`/`player_sacks`/`player_tackles_assists` —
  kicker/DST/IDP-facing. This project is DK Classic offense + DST; DST is already projected from
  settled box-score pbp (`ingestion/dst_actual_scoring.py`), not player props, and there's no IDP
  format in scope.

### 4. Odds → implied probability: the standard, disclosed formula, not invented here

`american_to_implied_probability(odds)`: positive odds → `100 / (odds + 100)`; negative odds →
`-odds / (-odds + 100)`. Both sides' price AND implied probability are kept on every
`PlayerProp` row — per Chris's direction, the odds themselves are the point, not just the line.

### 5. Player matching: DK-anchor, name-only (with a live-discovered DST special case) — a disclosed, narrower gate than the full pipeline

`PropMatchMethod` is a **new, separate enum from `identity.py`'s `MatchMethod`**, not a reuse of
it — that enum's `NAME_TEAM_POSITION` asserts a position match, and the props payload carries no
team or position field for the player at all (an outcome is only
`{name, description, price, [point]}`). Claiming `NAME_TEAM_POSITION` here would assert an input
this module never received. What IS legitimately available: the event itself constrains a
player's team to one of its own two rosters (not fabricated — definitional), so
`match_prop_to_dk_player` narrows the DK pool to the event's home/away teams before comparing
normalized names (`normalize_name`, reused unchanged from `normalization/name_utils.py` —
the same primitive `matcher.py`'s own fallback matcher uses, not a second name-comparison
routine). Outcomes: `NAME_TEAM` (unique hit), `UNRESOLVED` (no candidate), `AMBIGUOUS` (2+
candidates, never auto-picked — same "surface for QA" posture as `identity.py`'s own AMBIGUOUS).

**Real finding from the first live pull against an actual DK anchor pool (week 3, 619-player
pool):** `player_anytime_td` includes team defenses, not just offensive players — confirmed live,
`"Philadelphia Eagles D/ST"` and `"Chicago Bears D/ST"` both appeared as real outcomes. DK's own
draftables list that same defense as `SourcePlayer(name="Eagles", team="PHI", position="DST")` —
no name-normalization bridges those two strings. `match_prop_to_dk_player` special-cases the
literal `" D/ST"` suffix (confirmed live) and resolves by team abbreviation + `position == "DST"`
instead, mirroring `matcher.py`'s own `_resolve_dst` reasoning that a team defense isn't a person
and name-matching it is a category error.

`dk_native_id` is DK's own `playerDkId` (the anchor ID, ADR-0013 decision 3) — **not** this
project's full `PlayerIdentity.canonical_id` (gsis_id / local UUID). Resolving further requires
that week's already-computed `PlayerIdentity` table (`matcher.reconcile_week`, which needs the
nflverse crosswalk) — out of scope here; a caller needing the full canonical_id joins this
module's output against that table on `dk_native_id`.

**Live result (week 3, one real event, Eagles @ Bears, 67 parsed prop rows):** 65 resolved
`NAME_TEAM`, 2 resolved `TEAM_DST`, 0 `UNRESOLVED`, 0 `AMBIGUOUS` — 100% match rate on this one
real event. This is one event's result, not a claim about every future week; a 619-player DK pool
with two active teams' worth of real prop coverage is a reasonably strong first data point, not a
guarantee against future collisions or naming drift.

### 6. Persistence: raw API-response cache, not a parsed store — same rationale as ADR-0038

`storage/odds_api_props_store.py` saves the raw, unparsed `/events/{id}/odds` response bodies,
one envelope per `(season, week, calendar date captured)` — `data/raw/odds_api_props/<season>/
week<week>/<YYYY-MM-DD>.json`. Chosen over a parsed/normalized store for the same reason
`injury_snapshot_store.py` (ADR-0038) made this call for injury data: prop lines move throughout
the week (injuries, weather, line movement), so "what DK was actually pricing this player at, as
of this pull" is itself the fact worth preserving — a parsed-only store would have already
discarded whatever the current version of `parse_dk_player_props` doesn't extract, making a future
parser fix or bug retroactively unrecoverable. Filesystem-is-the-only-source-of-truth, atomic
writes (`.tmp` + `os.replace`) — same conventions as `resultsdb_store.py`/`injury_snapshot_store.py`
(ADR-0024/0038), not reinvented.

## Consequences

- **Disclosed, not backtested (ADR-0019/0020's convention)**: this ADR covers ingestion
  correctness only — what fields exist, at what granularity, under what match quality. Whether
  prop-implied volatility actually predicts DK ceiling outcomes better than this project's existing
  `CeilingMultiplier` components is untested and explicitly out of scope for this round. Wiring
  this into `ceiling_lean`/Component A (ADR-0028) is separate, future formula work, pending Model
  Analytics Expert / Fantasy Football Expert review, same as every other Section 6 formula change.
- **Real cost, not a design constraint today**: `len(markets) * events_pulled_this_week` credits
  per pull, against a 20,000-credit/month plan — comfortable headroom even at daily-capture
  cadence for a full week's slate, but a capture script that runs this on every page load or in a
  tight loop would not be free; `scripts/live_integration_check_odds_api_props.py`'s own docstring
  flags this.
- **A 100%-match live result on one event is not a guarantee.** Larger slates (Sunday's full
  afternoon window, 6+ simultaneous games) haven't been pulled this session — a genuinely more
  demanding test of the AMBIGUOUS path (same name across more concurrent rosters) than one
  Monday-night game provides. QA's validation pass (PRD Section 11 item 3) should re-run this
  against a full Sunday slate before treating match quality as settled.
- **DST coverage is now real for `player_anytime_td` specifically**, but the general pattern (a
  vendor payload naming a team entity differently from DK's own draftables) may recur in other
  markets or other DFS-relevant vendors this project ingests later — worth checking for, not
  assumed solved everywhere `_DST_SUFFIX`-style logic hasn't been applied.
