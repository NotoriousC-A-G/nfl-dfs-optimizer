# Phase 0 — Data Source Availability

Owner: Data Integration Engineer. Reviewed by: Architect. Status per PRD Section 4/11/12 — this
report is what Section 6's formulas get finalized against once all sources are confirmed.

## RotoGrinders

**Status: confirmed working, session captured.**

- No public API (as PRD expected). Authenticated via standard cookie-based session — captured
  the full `document.cookie` string from an already-authenticated session and stored it as
  `ROTOGRINDERS_SESSION_COOKIE` in `.env`.
- LineupHQ (`https://rotogrinders.com/lineuphq/nfl`) confirmed live for the current DK Classic
  slate. Underlying data call:
  `GET /api/user-projections?sport=nfl&site=draftkings&user_id=<id>&storage=<cloud_storage_key>&timestamp=<ms>&source=<grid_id>`
  — returns clean JSON, keyed by player ID, with salary, position, team, opponent, full stat-line
  projection, floor/ceiling, and **`POWN`** (projected ownership) per player. This is the
  ownership-grid data Section 4 calls for.
- A separate `/api/user-info?user=<id>&token=<token>` call resolves the account's `user_id` and
  `cloud_storage_key`, which the projections call needs. The `token` param appears page-embedded
  rather than derived purely from cookies — worth confirming its lifetime before relying on it in
  an unattended weekly run.
- Not yet checked: NFL WeatherEdge endpoint specifically (Section 4 also calls this out). Follow-up
  task.

## RotoGrinders ResultsDB (added post-Phase-0, ADR-0023)

**Status: confirmed working, no session cookie needed at all — the one reverse-engineered source in this
project that isn't auth-gated.** Full investigation, live findings, and the ingestion module built against it
are in ADR-0023 (`docs/adr/0023-resultsdb-contest-history.md`); summarized here for this report's index:

- `resultsdb/nfl` is a RotoGrinders-branded wrapper around a FantasyLabs "Contests Dashboard" app
  (`terminal.fantasylabs.com/contests?brand=rotogrinders&sportid=1&date=...`), confirmed via the page's own
  `<iframe>`. It's per-*contest* post-contest field data (real settled DK GPPs), not a projection archive.
- Underneath that UI is a public, unauthenticated JSON API: `service.fantasylabs.com/contest-sources` (which
  DK draft groups are live for a date) → `.../live-contests` (real contests within a draft group, `contest_id`
  + `is_primary`/`multi_entry_max`/etc.) → `dh5nxc6yx3kwy.cloudfront.net/contests/nfl/<date>/<contest_id>/
  data/` (the real payload: per-player ownership/actuals/box-score line, per-user roster/ROI, salary/flex/
  stack usage by percentile tier) and a sibling `.../lineups/` payload (per-distinct-roster dup counts,
  `lineupTrends`, team/game stacks — confirmed available, not yet ingested).
- One real auth-shaped gotcha: `service.fantasylabs.com` 403s Python's default `requests` User-Agent (basic
  bot-filtering, not session auth) — fixed the same way as RotoGrinders LineupHQ/Situation Room, a
  browser-shaped `User-Agent` header.
- **Coverage confirmed live: the 2020 season through today** (every 2020-2026 contest tried returns real data;
  every 2017-2019 contest tried 403s) — six real seasons, informing PRD Section 11 item 4's resolution.
- **Real surprise worth flagging for anyone building on top of this:** a single date/draft-group can carry
  more than one `is_primary` contest at once (a live 2023 pull found both the true, 150-max-entry, 28,029-
  entry "Millionaire" and a separate, smaller, pricier "MEGA Millionaire" both flagged `is_primary=true`).
  `is_primary` alone is not a safe way to pick "the" flagship contest — see ADR-0023's
  `select_millionaire_maker_contest` for the `multi_entry_max == 150` disambiguation this project uses
  instead.

## Footballguys

**Status: confirmed working end-to-end, session captured.**

- The PRD's assumed path (`subscribers.footballguys.com/myfbg/`) **404s** — that subdomain/path
  doesn't resolve. The real, working authenticated page is under the main domain:
  `https://www.footballguys.com/projections/duration/draftkings` (and `/fanduel` for FanDuel).
- Filter interactions (week, team, position) hit:
  `GET https://www.footballguys.com/projections?componentIdNum=<n>&week=<n>&nflTeam=<team|all>&pos=<pos|all>&durationTypeKey=weekly&posGroupKey=all&dfsSite=<draftkings|fanduel>&reload=1`
  — cookie-session-authenticated, no token/API key in the URL.
- **Response format confirmed: HTML, not JSON.** Replayed the request server-side (Python
  `requests`, not the browser) with the captured cookie — `200`, `text/html`, a server-rendered
  component fragment (`<div class="tnc-component ...">` wrapping a `<table>`), not a JSON API.
  Verified real per-player data is present (`<tr data-playerid="GibbJa00"
  data-playername="Jahmyr Gibbs" ...>`), so the cookie fully authenticates outside the browser
  too, not just for logged-in page loads. Ingestion for this source needs an HTML table parse
  (e.g. `pandas.read_html` or a targeted scrape on `tr[data-playerid]`), not a JSON decode like
  RotoGrinders. Architect should account for this when finalizing the ingestion design.
- A "Download Projections" button exists on the page; whether it produces a clean CSV via a
  capturable network request, or a client-side blob from already-rendered table data, is
  unconfirmed — no network request fired when clicked in this session's test. Not worth chasing
  further now that the HTML endpoint is confirmed working.
- **Session cookie captured and verified.** Claude in Chrome blocks JS-based cookie reads
  (`document.cookie` returns `[BLOCKED: Cookie/query string data]`) as a safety measure, so this
  one was captured by Chris manually via browser DevTools (Network tab → a `footballguys.com`
  request → Request Headers → `Cookie`) rather than automated — matches the "lightweight local
  auth step" fallback the PRD (Section 4) already anticipated. Stored as
  `FOOTBALLGUYS_SESSION_COOKIE` in `.env` and confirmed working with a live authenticated
  `requests.get` call outside the browser.

## PFF

**Status: confirmed working, and resolves most of Section 6's open granularity questions.**

- Base URL `https://api.pff.com`, bearer-token auth (`Authorization: Bearer <PFF_API_KEY>`,
  key format `ak_…`). `GET /v1/auth/whoami` confirmed the key: `tier: "pro"`, `entitled: true`.
- Full OpenAPI spec at `https://developer.pff.com/openapi.json` (70 endpoints) — pulled and
  inspected directly rather than relying on the docs site's rendered view, which wasn't loading
  interactively.
- **Pass-block efficiency by individual lineman: confirmed available**, resolving PRD Section 4's
  open question. `GET /v1/facet/offense/pass_blocking?league=nfl&season=&week=` returns one row
  per offensive lineman with `player_id`, `position` (e.g. `T`), `grades_pass_block`, and `pbe`
  (PFF's pass-block-efficiency stat), plus a `true_pass_set_*` variant for pressure/sacks/hurries
  on true dropbacks specifically. (Note: `/v1/facet/signature/pass-blocking/efficiency/line` is a
  *different*, team-level aggregate — 32 rows, no player_id — don't confuse the two.)
- **Run-block grade by lineman: confirmed available.** `GET /v1/facet/offense/run_blocking`
  returns per-player `grades_run_block`, plus a bonus split PRD Section 6 didn't ask for —
  `gap_grades_run_block` / `zone_grades_run_block` (gap vs. zone run-blocking scheme).
- **Run defense grade: confirmed.** `GET /v1/facet/defense/run` → response key
  `run_defense_summary`, per-defender `grades_run_defense`, `stop_percent`, `missed_tackle_rate`.
- **Coverage grade split by scheme (man vs. zone): confirmed available.**
  `GET /v1/facet/defense/coverage_scheme` returns per-defender `man_grades_coverage_defense` and
  `zone_grades_coverage_defense` side by side, plus the full man/zone stat lines (targets, yards,
  coverage snaps) each is built from.
- **Coverage grade split by alignment (slot vs. perimeter): only partially available — this is
  the one real gap.** `GET /v1/facet/signature/defense/slot_coverage` exists and returns
  slot-specific volume (coverage snaps, targets, receptions, yards, `yards_per_coverage_snap`)
  per defender, but **no PFF grade field** — no `grades_coverage_defense` equivalent scoped to
  slot alignment. The graded coverage number (`grades_coverage_defense` in
  `/v1/facet/defense/coverage_matchup`) is overall-only, not alignment-split, and there's no
  `perimeter_coverage` counterpart endpoint. **Recommendation for Architect/Model Analytics
  Expert:** the `MatchupContext` coverage formula (Section 6) can use the man/zone grade split
  directly, but the alignment-specific piece will need to be approximated — e.g. weighting the
  overall coverage grade by the receiver's own slot vs. perimeter snap share from
  `slot_coverage`'s volume data — rather than pulling a PFF-native alignment-scoped grade, since
  one doesn't exist in this API.
- General auth note: per-league access is enforced server-side — an unentitled/view-only caller
  gets `200` with fields stripped and a `restricted` key, not a `403`. Worth a defensive check in
  the ingestion code (verify expected fields are present, don't just check status code) so a
  silent entitlement downgrade doesn't get mistaken for real data.

## DraftKings public API

**Status: confirmed working.** No auth needed.

- `GET https://www.draftkings.com/lobby/getcontests?sport=NFL` — live contest list. Each contest
  carries a `dg` (draftGroupId) and `gameType` (`"Classic"`, `"Showdown Captain Mode"`, etc.) —
  filter on `gameType == "Classic"` to isolate the main-slate draft groups Section 2 cares about.
  Note: several draft groups can share a `gameType`/slate label but differ in which games/players
  they actually include (e.g. one Classic `dg` returned 0 players — likely a slate that had
  already locked) — always verify a candidate `dg` actually has players before using it.
- `GET https://api.draftkings.com/draftgroups/v1/draftgroups/{draftGroupId}/draftables` — clean
  JSON, one row per player: `salary`, `position`, `teamAbbreviation`, opposing team + game start
  time (`competition.startTime` — this is what Section 3's early-game/late-swap split should key
  off), DK's own projection value, bye week. Confirmed real Classic slate: correct position mix
  (QB/RB/WR/TE/DST, no CPT), multiple games represented.
- Player ID here (`playerDkId`) is DK's own ID — this is one side of the Section 11 item 3 ID
  reconciliation problem; PFF/RotoGrinders/Footballguys each use their own ID scheme and none of
  them are `playerDkId`.
- **Correction (ADR-0023, `docs/adr/0023-resultsdb-contest-history.md`): Section 4's "post-lock actual
  ownership for backtesting" claim does NOT hold for DK's public endpoints — live-tested this round, not
  assumed.** `GET https://api.draftkings.com/scores/v1/leaderboards/{contestId}?format=json&embed=leaderboard`
  and `GET https://api.draftkings.com/scores/v2/entries/{draftGroupId}/{entryKeys}?format=json&embed=roster`
  (the actual leaderboard/ownership-shaped endpoints, per community documentation) both returned a live
  `400 {"errorStatus":{"code":"SCO101","developerMessage":"Invalid userKey."}}` when called unauthenticated
  against a real settled contest — a DK session-auth rejection (`iv`/`jwe` cookies, confirmed against a
  third-party DK API client's own requirements), not a not-found or rate-limit error. `contests/v1/contests/
  {contestId}?format=json` (contest metadata/payout structure) IS public and confirmed working, but carries no
  ownership data. Post-lock ownership for backtesting instead comes from RotoGrinders ResultsDB (new section
  earlier in this report, right after RotoGrinders) — real, and confirmed to need no auth at all.

## nflverse / nfl-data-py

**Status: partially working — the high-level weekly-stats function is stale; raw play-by-play is
current.**

- `nfl.import_weekly_data([2025])` (aggregated per-player weekly stats, what Section 4 likely
  assumed) fails: the underlying GitHub release asset it reads
  (`nflverse-data` release tag `player_stats`) was last published in mid-2025 covering through the
  2024 season only — no current-season data behind that specific function in the installed
  `nfl_data_py` version.
- `nfl.import_pbp_data([2026], include_participation=False)` **does** work and is current —
  confirmed it returns live Week 1 play-by-play for games already underway today, with target,
  air-yards, and receiver-level columns present. This is enough to derive target share, red-zone
  share, aDOT, and snap-adjacent metrics ourselves, just not via the convenience function.
- **Implication for the Architect:** build the "foundation layer" (Section 5 step 1) on
  `import_pbp_data` aggregations rather than `import_weekly_data`, at least until the
  `player_stats` release catches up or the package is upgraded. Historical seasons (2023, 2024)
  are fine either way for backtesting.

## Odds API

**Status: confirmed working.** Reused the MLB DFS optimizer's existing the-odds-api.com key —
same account, same key covers all sports under one plan. (A direct Keychain read was blocked by
this session's auto-mode permission classifier — reasonable, since that's pulling a secret from
the credential store — so Chris ran `security find-generic-password -s mlb-dfs -a ODDS_API_KEY -w`
himself and handed the value over.)

- `GET https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds?regions=us&markets=spreads,totals&oddsFormat=american`
  — `200`, 212 games returned (full season, not just this week), per-bookmaker spreads and
  totals including **DraftKings' own line** specifically — useful since that's the book whose
  contest pricing the correlation model should track most closely. This is exactly the
  spread/total input `GameEnvironmentScore`'s implied-team-total component (Section 6, 40%
  weight) needs.
- 15,740 requests remaining on the shared plan at time of test — plenty of headroom for a weekly
  pull.

## Weather

**Status: no separate API/key needed — reusing the MLB build's approach.** Dropped the
placeholder `WEATHER_API_KEY` from `.env`/`.env.example`/`config.py` entirely.

- **Open-Meteo** (primary) — free, keyless, hourly wind/temp/precipitation. Domed/indoor
  stadiums (Section 6's weather-redistribution rule) short-circuit before the call.
- **NWS** (`api.weather.gov`, secondary cross-check) — free, keyless, US-only. The MLB build
  uses this specifically because Open-Meteo's underlying global model (GFS/ECMWF)
  under-reads thunderstorm probability vs. NWS's regional HRRR/RAP models — same risk applies
  to outdoor NFL games in the back half of the season (PRD Section 6's weather-impact
  component). Requires a descriptive `User-Agent` header (NWS mandates contact info in it).
- **RotoGrinders WeatherEdge** (empirical HR/park impact in the MLB build) — scraped via
  Claude-in-Chrome JS extraction from `roto.weatherbell.com/gameday/`, not a generic weather
  API call. The equivalent NFL WeatherEdge tool (PRD Section 4 mentions it under RotoGrinders)
  hasn't been checked yet — same scraping technique should apply, but the extraction JS itself
  is MLB-stat-shaped (HR%/Runs%/ERA%) and needs adapting for whatever NFL-relevant metrics
  WeatherEdge actually exposes there. Follow-up task.
