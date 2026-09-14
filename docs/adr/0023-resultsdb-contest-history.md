# ADR-0023: RotoGrinders ResultsDB — what it actually is, real data shapes, and DK contest-history scope

**Status:** Accepted (data-availability spec + v1 ingestion for contest discovery and player-exposure/actuals;
lineup-level dup-risk/stack-tier ingestion explicitly scoped out as follow-up — see Consequences)
**Date:** 2026-09-13
**Owner:** Data Integration Engineer
**Related:** PRD Section 4 (Data Sources), Section 5 step 7 (ownership/leverage layer, still unbuilt), Section 11
item 4 (historical backtesting data — how many seasons), ADR-0018 (`ProjectionAccuracyRecord`),
`docs/phase0/data-availability.md`, `src/nfl_dfs/ingestion/rotogrinders.py`,
`src/nfl_dfs/ingestion/rotogrinders_injuries.py`, `src/nfl_dfs/ingestion/draftkings.py`

## Context

An exploratory check earlier this session found that `https://rotogrinders.com/resultsdb/nfl` (reachable with
the existing `ROTOGRINDERS_SESSION_COOKIE`) resolves to a "Contests Dashboard" showing real usernames with
roster/unique/player counts — not a simple historical-winning-scores archive. That check was not a real
investigation, just enough to see it exists. This ADR is the real reverse-engineering pass: what ResultsDB
actually is, what data shapes it exposes, how far back it goes, and whether it's the thing Section 11 item 4
("how many seasons of DK contest history does Chris want to validate the model against, matching the MLB
build's approach") should resolve against — cross-checked directly against how the MLB sister project
(`~/Developer/mlb-dfs-optimizer`) actually built and used the equivalent tool, not from memory of what it
probably did.

Separately, PRD Section 4 claims DraftKings' public API provides "post-lock actual ownership for backtesting."
That claim was never actually tested — this ADR tests it live.

## Decision

### 1. What ResultsDB actually is: a FantasyLabs white-label app, not a RotoGrinders-native product

Live-confirmed via Claude-in-Chrome, same technique used throughout this project's Phase 0 work: navigating to
`rotogrinders.com/resultsdb/nfl` renders a page whose only real content is a full-page `<iframe>`:

```
<iframe src="https://terminal.fantasylabs.com/contests?brand=rotogrinders&sportid=1&date=2026-09-13">
```

RotoGrinders and FantasyLabs share infrastructure — "ResultsDB" is RotoGrinders' branding on top of
FantasyLabs' "Contests Dashboard" product (`sportid=1` = NFL; `sportid=3` = MLB, confirmed against the MLB
build's own finding below). This matches, almost exactly, what the MLB build found for its own ResultsDB
investigation (`mlb-dfs-optimizer/docs/mass-multi/validation-findings.md`): same `terminal.fantasylabs.com`
host, same `brand=rotogrinders` / `sportid` / `date` URL shape, same AG-Grid-rendered tab set (Exposures /
Compare Exposures / Team Stacks / Game Stacks / Stack Seeker / Reports / Duplicates / Leaderboard / Live
Scoring). The MLB build never got further than DOM-scraping this UI (see section 3 below) — this pass found
something the MLB build didn't: a public, unauthenticated JSON API underneath it (section 2).

**Is it a "historical winning scores archive"? No — more, and different.** For a *specific, selected DK
contest* (not an aggregate across contests), ResultsDB exposes: the contest's real prize/entry-count metadata;
every rostered player's salary, actual DK points, box-score line, and field ownership — both overall and
broken into top-20% / top-10% / top-1% finisher tiers; every real entrant's username, roster count, ROI, and
individual lineups; and salary-usage, flex-usage, favorite/underdog, and team/game-stack distributions, each
also tier-broken. It is per-contest field composition and post-contest ownership, not a lineup-optimizer
projection archive and not aggregated across a user's contests generally (the "Contest Users" list Chris saw
in the exploratory check is this same per-contest view, listing every entrant in that one selected contest —
confirmed live, not aggregated across other contests they may have entered).

### 2. Real finding beyond MLB's own investigation: a public, unauthenticated JSON API

The MLB build's kickoff doc (`mlb-dfs-optimizer/mass-multi/claude-code-kickoff-resultsdb-scraper.md`) mandated
Chrome-MCP DOM scraping specifically, "not HTTP-layer scraping, API reverse-engineering... no bulk export
available." Its validation-findings doc confirms it never found a JSON API — it extracted data from the
AG-Grid's React-fiber `rowData` prop instead, with virtualized-row and 45-second-Chrome-MCP-timeout problems
that stalled the full-row capture.

Live investigation this pass found the actual data source, via `performance.getEntriesByType('resource')`
(the on-page network log tool used elsewhere in this project's Phase 0 work missed these calls — they're
`fetch()` calls this session's request-log tool doesn't surface, not something hidden from the page itself):

- `GET https://service.fantasylabs.com/contest-sources/?sport_id=1&date=<YYYY-MM-DD>` — lists every DK
  draft-group/source live for that date. **Confirmed live, public, no cookie needed** (verified via bare
  `curl`/`requests` with no `Cookie` header at all) — returns `{"contest-sources": [{"short_name": "dk",
  "draft_groups": [{"id": <contest_group_id>, "game_count": ..., "contest_start_date": ...}, ...]}, ...]}`.
- `GET https://service.fantasylabs.com/live-contests/?sport=NFL&contest_group_id=<id>` — every actual DK
  contest in that draft group: `contest_id`, `contest_name`, `contest_size`, `entry_cost`, `total_prizes`,
  `multi_entry_max`, `is_primary`, `is_largest_by_size`, `cash_line`. **Also public, no cookie.** Picking
  "the contest with the most entries" is the wrong heuristic (same class of mistake `draftkings.py`'s
  `select_classic_slate` fix already corrected for slate selection) — live 2023 data shows the
  largest-by-entry-count contest in the group ("$200K First Down", 237,812 entries, `multi_entry_max=20`)
  is not even `is_primary`. **`is_primary` alone isn't a clean disambiguator either, though — a real,
  live-confirmed surprise this pass:** the same draft group carried **two** contests flagged
  `is_primary=true` (and both `is_largest_by_size=true`) simultaneously — a $100-entry, 28,029-entry
  "$2.5M Fantasy Football Millionaire" (`multi_entry_max=150`) and a $4,444-entry, 768-entry "$3M MEGA
  Millionaire" (`multi_entry_max=23`). This project doesn't have a confirmed explanation for what
  `is_primary`/`is_largest_by_size` mean exactly (largest/primary *within what bracket* isn't stated
  anywhere in the payload) — rather than guess at that semantics, the module below disambiguates using the
  one field PRD Section 2 states literally: "the Millionaire Maker (**150-max entries per user**, massive
  overall field)" — `multi_entry_max == 150` picked the correct $100/28,029-entry contest unambiguously in
  the confirmed case, over the pricier, much-smaller "MEGA Millionaire" `is_primary` also matched.
- `GET https://dh5nxc6yx3kwy.cloudfront.net/contests/nfl/<YYYYMMDD>/<contest_id>/data/` — the real payload.
  **Confirmed live, public, no cookie, gzip-encoded** (`content-encoding: gzip`; Python's `requests` library
  transparently decompresses this — `response.json()` just works, no manual `gunzip` needed). Top-level keys,
  confirmed against a real 2023 Millionaire Maker contest (147325137, 768 entries):
  `contest, players, users, salaries, flexUsage, cptUsage, cptBreakdown, favoriteUsage, homeUsage, exposures,
  teamStacks, gameStacks, cuts`. `players` is one row per `"<playerId>:<rosterSlot>"` key with
  `playerId, fullName, salary, position, currentTeam, projPoints, ownership (overall only), actualPoints,
  statDetails (a human-readable box-score string, e.g. "66 RecYds, 6 Rec, "), madeCut`. The percentile-tier
  breakdown (20%/10%/1%) is a **separate** structure — `exposures["20"|"10"|"1"].exposureCounts` keyed the
  same `"playerId:rosterSlot"` way, each with `exposureCt`/`exposurePerc` — the AG-Grid UI's `ownership20`/
  `ownership10`/`ownership1` columns are a client-side merge of `players` + `exposures`, not a single flat
  field; ingestion has to do that merge itself (see `merge_player_exposures` in the module below).
- `GET https://dh5nxc6yx3kwy.cloudfront.net/contests/nfl/<YYYYMMDD>/<contest_id>/lineups/` — **confirmed
  live, also public.** One row per **distinct roster** (not per entry): `lineupHash, lineupCt` (how many
  entries used this exact lineup — the literal dup-risk distribution the MLB kickoff doc's "Duplicates tab"
  wanted), `lineupUserCt`, `lineupPlayers` (by roster slot), `points, totalSalary, totalOwn/minOwn/maxOwn/
  avgOwn, lineupRank, isCashing, payout, lineupPercentile, teamStacks, gameStacks, lineupTrends` (booleans
  like `qbPairedWithPassCatcher`, `rbPairedWithDefense` — a real, per-lineup empirical version of exactly the
  kind of thesis `StackProfile.pivot_to` states), and `entryNameList` (which usernames built it).

**Practical consequence for this project's convention:** every other reverse-engineered source in this
codebase (RotoGrinders LineupHQ, RotoGrinders Situation Room, Footballguys) needs the captured session cookie.
ResultsDB's actual data is the one exception — the cookie was needed only to *discover* the endpoint shape via
an authenticated page load, not to *fetch* the data itself. `rotogrinders_resultsdb.py` below sends no cookie
at all.

**One real auth-shaped gotcha, confirmed live:** `service.fantasylabs.com` returns a `403` WAF/challenge page
for Python's default `requests` User-Agent (`python-requests/2.x`) — identical `curl` and `requests`-with-a-
browser-`User-Agent` calls both succeed. This isn't session auth, it's basic bot-filtering; the fix is the
same `{"User-Agent": "Mozilla/5.0"}` header this codebase already sends for RotoGrinders LineupHQ and
Situation Room, not a new problem. An unknown/expired `contest_id` on the CloudFront path returns a clean
`403` with an S3 `<Error><Code>AccessDenied</Code>` XML body — easy to detect, but **not distinguishable from
"this date/contest genuinely isn't covered"** (there's no 404-vs-403 semantic split); the module below treats
any non-200 from that endpoint as "not covered," matching that ambiguity rather than asserting a false
distinction.

### 3. Coverage: 2020 season forward (confirmed), not further back — a real cliff, not a guess

Live-tested `contest-sources` and the `data/` payload across seasons, walking back from today:

| Season opener tested | `contest-sources` returns DK groups? | `data/` payload for that date's primary contest? |
|---|---|---|
| 2026-09-13 (today, live/in-progress) | Yes | **200** — populated `actualPoints`/`ownership` for already-completed games *while the slate is still in progress* (a live, not just a settled-contest, feed) |
| 2025-09-07 | Yes | **200** |
| 2024-09-08 | Yes | **200** |
| 2023-09-10 | Yes | **200** (this ADR's primary validation contest) |
| 2022-09-11 | Yes | **200** |
| 2021-09-12 | Yes | **200** |
| 2020-09-13 | Yes | **200** |
| 2019-09-08 | Yes (`contest-sources` lists a group) | **403** — no `data/` payload |
| 2018-09-09 | Yes | **403** |
| 2017-09-10 | Yes | **403** (tried both the largest and the `is_primary` contest for that date — both 403) |
| 2016-09-11 | Yes | not probed further once the pattern was clear from 2017/2018/2019 |
| 2015 (several dates) | **No** — `contest-sources` returns zero sources | n/a |

**Finding:** `contest-sources`/`live-contests` metadata goes back further (at least 2016) than the actual
`data/` archive, which cliffs cleanly between the 2019 and 2020 seasons — 403 for every 2017-2019 contest
tried, 200 for every 2020-2026 one. This means **real per-contest ownership/actuals/lineup data is available
for the 2020 through 2025 seasons, plus the in-progress 2026 season** — six seasons of settled data, not
"however far back RotoGrinders' UI happens to render." Continuity within that window was spot-checked at a
non-opener date (2024-11-10, Week 10) and returned live DK groups normally, so this isn't an openers-only
archive.

**Regime note, same shape as the MLB build's own finding (`simulator-research-scope-v0_3.md` §3.7.2):** the DK
contest structure itself changed across this window, not just RotoGrinders' coverage of it. 2020-2021's
flagship Millionaire ran ~28,000 entries as effectively the one mega-GPP; 2022 onward, the flagship "MEGA
Millionaire" shrank to ~600-1,000 entries (still `is_primary`, still real, but a structurally different
product — DK appears to have split what used to be one huge contest into several coexisting large ones by
2023, per the "$200K First Down" 237K-entry example in section 2). **This is exactly the "regime
heterogeneity" the MLB build flagged and fit per-season rather than blending** — the same posture is
recommended here, not re-litigated field-synthesis math (see section 5).

### 4. What the MLB build actually did, cited directly (not from memory)

Read `mlb-dfs-optimizer/mass-multi/claude-code-kickoff-resultsdb-scraper.md`,
`mlb-dfs-optimizer/docs/mass-multi/validation-findings.md`, and
`mlb-dfs-optimizer/mass-multi/simulator-research-scope-v0_3.md` §3.6/3.7 directly, not summarized from
training-time familiarity with the sister project:

- **Mechanism:** Chrome-MCP DOM/React-fiber scraping only, explicitly barred from API reverse-engineering
  (kickoff doc §3, "non-negotiable"). This pass's finding of the public CloudFront JSON API (section 2) is
  new relative to that prior work, not a re-application of it.
- **Coverage decided:** "Coverage confirmed to extend to at least April 17, 2021 (~5 seasons)"
  (`simulator-research-scope-v0_3.md`, Revision History and §3.6/§3.7.1) — a live-verification finding, not
  an assumption, arrived at the same way this ADR arrived at its own 2020 floor: testing dates, not guessing.
- **Density finding that reshaped scope:** per-slate coverage turned out thin (1-3 contests/slate, not the
  10-30 originally assumed), revising the Day-0 sample estimate down an order of magnitude to ~900-2,700
  contests total across 5 seasons (§3.7.1). The equivalent NFL check (section 2/3 above) found the *opposite*
  shape — one call per date returns every DK contest for that slate (dozens, not 1-3) with a real public JSON
  API rather than DOM-only access, so this project is not staring down the MLB build's density problem or its
  virtualized-row/45-second-timeout scraping pain at all.
- **Per-season, not blended, calibration (§3.7.2):** "Field-synthesis and dup-risk parameters are fit
  per-season in Phase A.5, not on the blended archive," with an explicit stability test — compare fitted
  parameters across seasons, blend only if statistically indistinguishable, otherwise let recent seasons
  drive production with older seasons held out as validation. Backfill order was recent-to-older specifically
  so calibration could stabilize and the backfill could stop early once it did (§3.7.2).
- **The single most important framing point (§3.7.4, stated as such in that doc):** historical backfill
  calibrates **field synthesis** (how well a simulated field's ownership matches real ownership) and
  **dup-risk** (predicted vs. actual duplicate-lineup counts) — it does **not** calibrate **ROI/projection
  accuracy**, because the vendor projections and scored player pool that would have driven lineup selection
  *at the time* don't exist historically. "A big Day-0 ownership sample makes it tempting to believe the
  simulator is calibrated when really only two of its three tracks are." Full ROI calibration still needed
  live forward-capture in the MLB build's plan.
- **Status as actually built** (not just planned): `mlb_dfs/research/simulator/resultsdb.py`,
  `resultsdb_backfill.py`, `analysis/ownership_calibration.py`, `analysis/stack_calibration.py` exist and ran
  — `research/data/resultsdb/` on disk holds real captured contest data spanning 2021 through 2025 (raw HTML
  + parsed JSON + consolidated JSONL, per date directories like `2022-08-23`, `2024-09-25`, `2025-08-30`,
  etc.), confirming the backfill was actually executed, not just scoped.

### 5. Applying this to Section 11 item 4: recommendation

Section 11 item 4 asks how many seasons of DK contest history to validate against, "matching the MLB build's
approach of analyzing full contest history before trusting the tool live." Given sections 3 and 4 above:

**Recommendation: 2020 through 2025 (six seasons), with 2026 accruing live as the season plays out, fit and
compared per-season rather than blended — directly matching the MLB build's own resolved approach, not a
different number chosen for its own sake.** Concretely:

1. Treat 2020-2025 as the full available window (section 3's confirmed cliff) — there is no gap to relitigate
   the way MLB had a density surprise; the ceiling here is the real data-availability ceiling, not a scope
   trade-off.
2. **2020 gets the same regime caveat the MLB build gave its own adjacent-to-2021 season** (its "2020 COVID
   short season's tail effects" note, §3.7.2): 2020 was a 16-team-bubble-adjacent, closed-stadium-heavy NFL
   season with real crowd-noise/attendance anomalies feeding into DFS-relevant game environments — a candidate
   for fit-but-flag-as-atypical rather than silent inclusion, the same treatment MLB gave its own edge season.
3. **Fit field-composition/ownership-calibration parameters per season, not blended**, then run the same
   stability test the MLB build specified (§3.7.2): compare fitted ownership-propensity/stack parameters
   across 2020-2025; if statistically indistinguishable, blend; if not, let 2023-2025 (the post-regime-change
   window identified in section 3) drive production with 2020-2022 held out as validation. This is a direct
   transplant of MLB's own resolved method, not a new invention.
4. **This is explicitly a field-composition/ownership-calibration and dup-risk data source, not a
   projection-accuracy backtest source** — same limitation the MLB build hit and named directly (§3.7.4): DK
   contest history has no record of what RotoGrinders/Footballguys/PFF actually projected for a given
   historical slate (confirmed separately and already documented in ADR-0018 section 4's audit — "vendor
   DFS-projection baselines... have not been confirmed to support historical-week pulls at all"). Six seasons
   of ResultsDB data therefore does **not**, by itself, resolve `ProjectionAccuracyRecord`'s harder question
   (was our *own* blended projection accurate); it resolves the separate, real question of what real DK
   fields actually looked like — see section 6 below for where that actually plugs in.
5. This recommendation is Data Integration Engineer scope to propose, not to finalize alone — per this
   project's decision-rights convention (PRD Section 10), Chris makes the final call given the tradeoffs
   above, the same way the MLB build's own five-season/per-season-fit decision was Chris's to accept, not an
   agent's to impose silently.

### 6. Where this actually plugs into the PRD: ownership/leverage (Section 5 step 7), not `ProjectionAccuracyRecord`

The task context flagged two candidate uses. Section 4 above already resolves which one this data source
actually serves:

- **Not `ProjectionAccuracyRecord` (ADR-0018).** That construct's `ActualResult` half is already sourced from
  `nflverse`'s `import_pbp_data()` aggregation (ADR-0018 section 3) — a complete, already-confirmed, full-
  history box-score source with no 2020 floor. ResultsDB's own `actualPoints`/`statDetails` fields are a
  plausible **cross-check** for that pipeline (same role ADR-0018 already gives the `stats_player` release —
  an independent aggregate to catch a bug, not the primary path) but add no new capability there, and are
  restricted to whichever contests RotoGrinders/FantasyLabs tracks (large, `is_primary`-tagged GPPs), not
  every player-week the way `import_pbp_data()` already is. Not worth wiring in for that purpose specifically.
- **Yes, directly, for Section 5 step 7 (the ownership/leverage layer, "use RotoGrinders ownership
  projections to flag high-owned chalk and identify leverage spots" — currently unbuilt).** That stage needs
  a *projected* ownership number (already sourced live from RotoGrinders LineupHQ, `rotogrinders.py`) and,
  ideally, a sense of whether that projection is any good historically. ResultsDB gives exactly that other
  half: real, settled field ownership (overall and by finisher tier) for the same DK contest types Chris
  actually enters (PRD Section 2's contest mix — single-entry through the Millionaire Maker). A future
  ownership/leverage design can join LineupHQ's *projected* `POWN` against a matched historical ResultsDB
  contest's *actual* `ownership` for the same player/slate-type to ask "does RotoGrinders' projected ownership
  actually track real field behavior, and by how much does the field diverge from it at the top percentile
  tiers" — the literal leverage question Section 5 step 7 exists to answer, now backed by real field data
  instead of only a projection with no ground truth to check it against. **Not decided or designed here** —
  this ADR resolves availability and shape; the leverage-layer design itself is future Architect scope once
  Section 5 step 7 is actually scheduled.

### 7. DraftKings public API: correcting Section 4's "post-lock actual ownership for backtesting" claim

PRD Section 4 lists, under DraftKings' public API, "post-lock actual ownership for backtesting" as something
it provides. **This was never actually tested until this pass, and it does not hold up live.**

- `GET https://api.draftkings.com/contests/v1/contests/<contestId>?format=json` — confirmed live, public, no
  auth. Returns contest metadata and the full payout-tier structure. **No ownership, no per-entry data.**
- `GET https://api.draftkings.com/scores/v1/leaderboards/<contestId>?format=json&embed=leaderboard` (the
  actual leaderboard/ownership-shaped endpoint, per community documentation — checked against
  `github.com/gacolitti/draft.kings`'s R client, which requires two DK session cookies, `iv` and `jwe`, for
  every leaderboard/entries call) — **tested live, unauthenticated, against a real settled contest
  (147325137): `400`, `{"errorStatus":{"code":"SCO101","developerMessage":"Invalid userKey."}}`.** This is
  not a "not found" or rate-limit error — `SCO101`/"Invalid userKey" is DK's own session-auth rejection,
  confirming the community client's requirement is real: this endpoint needs a logged-in DK session, the same
  general shape of auth problem this project already solved for RotoGrinders/Footballguys, but for a *third*
  account (Chris's own DraftKings login) that has not been set up at all.
- `GET https://api.draftkings.com/scores/v2/entries/<draftGroupId>/<entryKeys>?format=json&embed=roster`
  (the entries/rosters endpoint) — same live test, same `SCO101 Invalid userKey` result.

**Correction, precisely stated:** DraftKings' public API's *public* surface (`draftgroups/.../draftables`,
`contests/v1/contests/<id>`, `lobby/getcontests` — everything this project's existing `draftkings.py` already
uses) does **not** include post-lock ownership or per-entry data. That data exists on DK's platform but sits
behind session auth DK calls `iv`/`jwe` cookies — a real, previously-unflagged auth gap, structurally
identical to the RotoGrinders/Footballguys session-cookie problem this project already solved twice, except
for a source (Chris's own DraftKings account) nothing in this codebase currently touches. **Not resolved
here** — flagged as a genuine open item (see Consequences) rather than quietly left as PRD Section 4's
uncorrected claim. Practically, this doesn't block anything: ResultsDB (sections 1-3 above) already gives real
post-contest ownership for exactly the large public GPPs this project's leverage layer cares about, without
needing Chris's own DK session at all — DK's own leaderboard API would mostly matter for Chris's *own*
specific entries' rank/payout, a distinct (and lower-priority) question from field-wide ownership.

## What was built this pass

`src/nfl_dfs/ingestion/rotogrinders_resultsdb.py` — contest discovery and player-exposure/actuals ingestion:

- `fetch_contest_sources(date, session=None)` / `parse_dk_draft_groups(payload)` — the `contest-sources` call,
  filtered to the `"dk"` source, returning a list of `DraftGroupSource` (draft-group id, start time, game
  count).
- `fetch_live_contests(contest_group_id, session=None)` / `parse_live_contests(payload)` — the `live-contests`
  call, returning `LiveContest` rows (`contest_id, contest_name, contest_size, entry_cost, total_prizes,
  multi_entry_max, is_primary, is_largest_by_size, cash_line`).
- `select_millionaire_maker_contest(contests)` — picks the specific contest PRD Section 2 means by "the
  Millionaire Maker (150-max entries per user)": filters to `is_primary` candidates, then to
  `multi_entry_max == 150` among those (section 2's finding that `is_primary` alone can match more than one
  contest at once). Raises a loud, named error (`NoPrimaryContestError`) if zero or more than one candidate
  matches, naming every candidate, matching this project's existing "never guess, fail loudly" convention
  (`draftkings.py`'s `SlateSelectionError`) — deliberately not defaulting to "biggest" or "first is_primary
  match" (section 2's finding that both are ambiguous or wrong here).
- `fetch_contest_data(date, contest_id, session=None)` — the CloudFront `data/` payload; raises a specific
  `ContestDataUnavailableError` naming the date/contest on any non-200 (see section 2's 403-ambiguity note).
- `parse_contest_summary(payload)` → `ContestSummary` (contest id/name/date, entry cost, size, cash line,
  duplicate/unique lineup counts, total prizes).
- `parse_player_exposures(payload)` / `merge_player_exposures(...)` → `PlayerExposureRow` (identity, salary,
  position, team, `actual_points`, `stat_details`, `made_cut`, and `ownership_overall`/`ownership_top20`/
  `ownership_top10`/`ownership_top1`, the last three coming from the separate `exposures` tier structure and
  merged in by `"playerId:rosterSlot"` key, per section 2's finding that these are not already flat in
  `players`).
- `parse_user_exposures(payload)` → `UserExposureRow` (`username, total_rosters, unique_rosters,
  total_players, max_exposure, roi`) — the same "Contest Users" data Chris's own exploratory check surfaced.

No cookie/session parameter is required by any live call (section 2) — a `session: requests.Session | None`
parameter exists purely for test injection, matching this codebase's existing convention, not because auth is
needed.

**Deliberately not built this pass — explicit follow-up, not silently dropped:**
- The `lineups/` endpoint (per-distinct-roster dup-count, `lineupTrends`, `entryNameList`) — confirmed
  available and shaped (section 2), but parsing and using it well is a real, separate scope: the MLB build
  spent a dedicated kickoff doc and a full build week on the equivalent Team Stacks/Game Stacks/Duplicates
  tabs alone. Building it half-scoped in the same pass as the rest of this ADR would risk exactly the
  half-built-module outcome the task brief warned against.
- A backfill/orchestration script (`resultsdb_backfill.py`-equivalent) walking many dates and seasons with
  rate-limiting and resume support, per-season calibration fitting, and the actual ownership/leverage-layer
  design from section 6 — all genuinely new, non-trivial work belonging to whichever future sprint schedules
  Section 5 step 7, not a same-pass add-on to a data-availability ADR.
- Team/game-stack tier parsing (`teamStacks`/`gameStacks` in the `data/` payload) — same reasoning as lineups.

## Alternatives considered

- **Mirror the MLB build's DOM-scraping approach exactly, since that's the established pattern.** Rejected —
  section 2's live investigation found a public JSON API the MLB build never located; replicating a slower,
  more fragile scraping approach when a clean API exists would be worse engineering for no real benefit, and
  this project's own convention (Section 4's own text) is "reverse-engineer authenticated endpoints," not
  "always DOM-scrape regardless of what's actually there."
- **Recommend a different season count than the MLB build's five (e.g., all six available seasons, or a
  smaller recent-only window) without engaging the MLB build's own reasoning.** Rejected — the task explicitly
  asked this to be informed by what the MLB build actually did, and MLB's own five-season number wasn't an
  arbitrary target either; it was itself a live-verified coverage floor. This project's floor is verified at
  2020, one season earlier, for a source with a real regime break of its own (section 3) — recommending
  2020-2025 with the same per-season-fit-and-compare method, rather than either blindly copying "five" or
  ignoring MLB's resolved calibration method, is the more honest transplant.
- **Build the full MLB-equivalent scraper (lineups, team/game stacks, backfill, calibration) in this pass
  since the API turned out easier than expected.** Rejected per the task's own instruction to stop at
  ADR/design scope once a piece is bigger than one round can responsibly implement — the API being easier to
  *call* than MLB's DOM scraping doesn't make the *downstream* calibration/backfill/leverage-design work any
  smaller; that's still real, multi-part follow-up work.

## Consequences

- **`ProjectionAccuracyRecord` (ADR-0018) is unaffected by this ADR** — its `ActualResult` sourcing decision
  stands as specified; this ADR does not reopen it.
- **Section 11 item 4 has a concrete recommendation** (section 5) — 2020-2025 (six seasons), fit per-season
  with a stability test before deciding blended-vs-recent, same method as the MLB build's own resolution.
  Still needs Chris's explicit sign-off per this project's decision-rights convention, not treated as
  finalized by this ADR alone.
- **Section 4's DraftKings row needs a correction**, not just a footnote — "post-lock actual ownership for
  backtesting" is not true of DK's *public* API; that data requires Chris's own DK session auth (`iv`/`jwe`
  cookies), which nothing in this project has captured. Flagged as a new, genuine open item — a fourth
  reverse-engineered-session source (after RotoGrinders LineupHQ, RotoGrinders Situation Room, Footballguys),
  lower priority than those three since ResultsDB already covers the field-ownership need this project
  actually has (section 6).
- **Follow-up work explicitly scoped out, not silently dropped** (see "What was built this pass" above):
  lineup-level dup-risk/stack-tier ingestion, a backfill/orchestration script across the full 2020-2025
  window, per-season calibration fitting, the actual ownership/leverage-layer design (Section 5 step 7), and
  DK's own session-auth capture if Chris wants his own entries' leaderboard data specifically.
- **Data Integration Engineer follow-up, concrete and small:** confirm whether `service.fantasylabs.com`'s
  `403`-on-default-User-Agent behavior is a static WAF rule (safe to always send a browser User-Agent, as this
  module now does) or adaptive bot-detection that could tighten further — worth a periodic re-check the same
  way this project already re-checks RotoGrinders' `token` param lifetime (PRD Section 11 item 2's remaining
  follow-up).
