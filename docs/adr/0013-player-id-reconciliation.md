# ADR-0013: Player ID reconciliation across DK, PFF, RotoGrinders, and Footballguys

**Status:** Accepted (design), pending Data Integration Engineer implementation review and a QA sign-off on the fallback-matching policy before it's treated as final (see Consequences)
**Date:** 2026-09-13
**Owner:** Architect
**Related:** PRD Section 5 (Normalization, step 2), Section 11 item 3, `docs/phase0/data-availability.md`

## Context

Section 5 step 2 requires the Normalization stage to "reconcile player IDs across sources, resolve name collisions, flag players missing from any source." Section 11 item 3 flagged this as unresolved: DK, PFF, RotoGrinders, and Footballguys each use their own player ID with no shared standard. Phase 0 confirmed each source's native ID shape:

- **DraftKings**: `playerDkId` (numeric), DK-internal, no crosswalk to anyone else.
- **PFF**: `player_id` (numeric), PFF-internal.
- **RotoGrinders**: `PLAYERID` / `RGID` (numeric, same value under two field names) in the LineupHQ projections payload.
- **Footballguys**: `data-playerid` on player table rows, observed format `"GibbJa00"` for Jahmyr Gibbs — visually a Pro-Football-Reference-style `LastFFNN` ID.

Before designing fuzzy-matching from scratch, this ADR checks live whether the nflverse/ffverse ecosystem's ID crosswalk (`nfl_data_py`'s `import_ids()`, sourced from `dynastyprocess/data`) already solves part of this.

## Live crosswalk check

Pulled `nfl_data_py.import_ids()` directly (not assumed from memory) against the project's installed `nfl_data_py==0.3.2`:

- **12,494 rows, `db_season` 2026 (current)**, 35 columns: `mfl_id, sportradar_id, fantasypros_id, gsis_id, pff_id, sleeper_id, nfl_id, espn_id, yahoo_id, fleaflicker_id, cbs_id, pfr_id, cfbref_id, rotowire_id, rotoworld_id, ktc_id, stats_id, stats_global_id, fantasy_data_id, swish_id, name, merge_name, position, team, birthdate, age, draft_*, twitter_username, height, weight, college`.
- **No DraftKings ID column and no RotoGrinders ID column exist at all.** The crosswalk does not cover these two sources — confirmed by column absence, not inferred.
- **`pff_id` is present (7,562/12,494 non-null) and verified to be PFF's own live `player_id`, not a lookalike.** Cross-checked directly: crosswalk row for Jahmyr Gibbs gives `pff_id = 122474.0`; a live call to PFF's own API (`GET /v1/facet/rushing/summary?league=nfl&season=2025&week=1`, using the project's real `PFF_API_KEY`) returns Jahmyr Gibbs with `player_id: 122474` — an exact match. **This is a real, usable crosswalk for PFF.**
- **`pfr_id` is present (9,630/12,494 non-null) in the `LastFFNN` format Footballguys appears to use — but it does NOT reliably match Footballguys' `data-playerid` attribute.** Checked directly rather than assumed: the crosswalk's `pfr_id` for the real Jahmyr Gibbs (DET, RB) is `GibbJa01`, not `GibbJa00`. Searching the crosswalk for `pfr_id == "GibbJa00"` returns a completely different real player — **Jack Gibbens, a linebacker on Arizona.** Footballguys' own HTML (captured live in Phase 0, `data-playerid="GibbJa00" data-playername="Jahmyr Gibbs"`) therefore does not carry the actual PFR disambiguation suffix for Gibbs. Whether this is a bug in how Footballguys generates the attribute or a different, PFR-*looking* internal scheme, the practical conclusion is the same: **a bare `pfr_id`-to-`data-playerid` join would have silently mapped a real player (Gibbs) onto a different real player's identity (Gibbens)** — exactly the failure mode Section 5's "resolve name collisions" requirement exists to prevent. This is reported as found, not rounded up to "Footballguys is covered by the crosswalk."
- **`gsis_id` is present (8,007/12,494 non-null)** — nflverse's own canonical player ID, already the natural key for data pulled via `import_pbp_data()` (the foundation layer per Section 5 step 1 / Phase 0's `import_weekly_data` staleness finding). Coverage is ~64% crosswalk-wide, pulled down by not-yet-debuted draftees and retired/inactive rows in the file; not separately verified here whether coverage is materially higher restricted to players who'd actually appear on a DK slate — flagged as a QA spot-check once weekly pulls are live, not assumed.

**Two more live findings, incidental to the crosswalk check but directly relevant to fallback matching:**

- **Position labels differ across sources.** PFF's own API returns `position: "HB"` for running backs (confirmed live in the same Gibbs `rushing/summary` payload), where DK, the crosswalk, and the rest of this PRD use `"RB"`. A literal-string position match would incorrectly treat every PFF running back as a non-match.
- **Team abbreviations differ across sources.** A live DK draftables pull (`draftGroupId=153070`) returned team codes `GB, LV, MIN, PHI, WAS, ARI, LAC, MIA`. The nflverse crosswalk's own `team` column uses a different vocabulary for several of the same franchises: `GBP, LVR, KCC, JAC, NEP, NOS, SFO, TBB` (plus stale relocated-franchise codes `OAK, STL, SDC, RAM, FA*` for historical rows). A literal-string team match would fail for at least eight active franchises.

**Verdict:** the crosswalk is a real, partial win — not a full solution and not force-fit as one. It directly and verifiably covers **PFF**. It does not cover **DraftKings** or **RotoGrinders** at all. It nominally resembles a solution for **Footballguys** via `pfr_id`, but that resemblance is demonstrated to be unreliable on live data, so Footballguys is treated as **uncovered** by the crosswalk for join purposes, same as DK and RotoGrinders. `gsis_id` is retained as the preferred internal canonical-ID anchor where available, for reasons unrelated to source coverage (see Decision).

## Decision

### 1. Canonical ID

Use nflverse's `gsis_id` as the canonical internal player ID when the crosswalk resolves one for a player — not a fresh UUID-per-player — because the foundation layer (Section 5 step 1) is already `import_pbp_data()`-keyed on `gsis_id`-shaped player-ID columns (`passer_player_id`, `receiver_player_id`, etc.); reusing it avoids a second mapping hop for the single richest internal data source.

When the crosswalk has no `gsis_id` for a player (undrafted rookie not yet in the `dynastyprocess` file, a crosswalk refresh lag — the same kind of vendor staleness Phase 0 already found in `import_weekly_data`), mint a project-local UUID and persist it in a local `player_registry` store (first-seen wins; later weeks re-resolve to the same canonical ID via the fallback matcher below, not a fresh UUID each week).

DST (team defenses) are not people and are not in `gsis_id` at all: canonical ID is a fixed synthetic key, `DST_<normalized_team_abbr>`, resolved purely through team-abbreviation normalization (below), no player-matching logic involved.

### 2. Per-source matching: crosswalk probe, gated by mandatory name verification, then fallback

Applied uniformly to all four vendor sources (DK, PFF, RotoGrinders, Footballguys) rather than hand-writing a different rule for "sources the crosswalk covers" vs. "sources it doesn't" — this is what makes the policy generalize past the one anecdote this ADR happened to catch:

1. **Crosswalk-ID probe.** If the crosswalk exposes a native ID field for that source, look up the candidate row by ID. Today that's only `pff_id` for PFF. (`pfr_id` is *not* probed for Footballguys — see step 2's consequence below; if the crosswalk ever adds a genuine DK or RotoGrinders ID column, it becomes probeable here with no other design change.)
2. **Mandatory name-verification gate.** A crosswalk-ID candidate is provisional until the source's own player name (normalized — see step 3) matches the crosswalk row's `name`/`merge_name`, scoped to the same normalized team + normalized position. If it doesn't match, discard the candidate and fall through to step 3. This single rule is what would have caught the Gibbs/Gibbens Footballguys case automatically, and it's *why* PFF's `pff_id` path is trusted here (its name equality holds on every spot check performed) while Footballguys' `pfr_id` is not (its name equality demonstrably fails at least once). Practically, this makes `pfr_id` a no-op for Footballguys today — it never has a crosswalk-ID candidate to verify in the first place, since Footballguys' own `data-playerid` isn't what gets probed — so Footballguys falls to step 3 for every player, same as DK and RotoGrinders.
3. **Fallback composite match** (used for DK and RotoGrinders always; used for PFF/Footballguys whenever step 1–2 doesn't produce a verified hit): normalized name + normalized team + normalized position.
   - **Name normalization:** lowercase; strip suffixes (`Jr.`, `Sr.`, `II`, `III`, `IV`); strip punctuation and periods (`D.J.` → `dj`); strip apostrophes; transliterate accented/diacritic characters (`é`→`e`, etc.) via Unicode NFKD decomposition.
   - **Team normalization:** a small static alias table (Data Integration Engineer-owned, alongside the ID mapping layer) resolving every source's team vocabulary to one canonical abbreviation set — concretely needed per the live findings above (`GBP`→`GB`, `LVR`→`LV`, `KCC`→`KC`, `JAC`→`JAX`, `NEP`→`NE`, `NOS`→`NO`, `SFO`→`SF`, `TBB`→`TB`, plus retired codes `OAK`/`SDC`/`STL`/`RAM` mapped to their current franchise for historical joins).
   - **Position normalization:** a small static alias table (`HB`→`RB` confirmed needed from PFF; `DEF`/`D`/`DST`→`DST` for team defenses; extend as other mismatches surface).

### 3. Outcome policy

- **No match found:** that source's field is left unresolved for the player; contributes to the player's coverage flags (Section 5's "flag players missing from any source"). The canonical entry is still created if at least one source resolved. DraftKings' weekly draftables pull is the anchor/master list for the week — every player who matters for lineup construction has a DK entry by definition (salaried, rosterable) — so the practical traversal is "for each DK player this week, resolve PFF/RotoGrinders/Footballguys," not the reverse; a canonical entry with zero resolved sources shouldn't occur and is itself worth a QA flag if it does.
- **Ambiguous / multiple plausible candidates:** never auto-resolved by picking the first or highest-similarity match. Recorded with `MatchMethod.AMBIGUOUS` and the full candidate list; excluded from automatic downstream blending until a human (QA, per Section 10) resolves it.
- **Genuine name collision** (two different real players, same normalized name + team — rare but must be handled): disambiguate with position first (usually sufficient), then jersey number where the source payload carries it (confirmed present in PFF's response; DK/RotoGrinders/Footballguys should be checked at implementation time, not assumed), then the crosswalk's own `birthdate`/`draft_year` as a last resort. If still tied, flag `MatchMethod.AMBIGUOUS` with a `name_collision` note rather than silently merging two different humans' stats into one canonical ID — that failure mode is worse than an unresolved player, since it corrupts data rather than just omitting it.

### 4. Output shape

A canonical `PlayerIdentity` record per player, plus a native-ID map — not a separate join table, so QA can filter the weekly normalized table directly for coverage gaps rather than joining across tables. Scaffolded in `src/nfl_dfs/normalization/identity.py` (see below); matching logic itself (the actual normalizer, alias tables, and crosswalk fetch/cache) is a follow-on implementation task, not built in this pass.

## Alternatives considered

- **Mint a fresh UUID as canonical ID for every player, ignore `gsis_id`.** Rejected — throws away a free, already-necessary join key for the richest internal data source (pbp-derived target share, red-zone share, aDOT) for no benefit; `gsis_id` absence is handled by the UUID fallback anyway, so this loses nothing by preferring `gsis_id` when available.
- **Trust the crosswalk's `pfr_id` for Footballguys outright, since the format matches.** Rejected — the whole point of checking live instead of assuming was to catch exactly this; the Gibbs/Gibbens result is a real, demonstrated player misassignment, not a hypothetical risk.
- **Hand-write a different matching rule per source** (crosswalk-only for PFF, name-match-only for DK/RotoGrinders/Footballguys). Rejected in favor of the uniform crosswalk-probe-then-verify-then-fallback rule — it produces the same practical routing (PFF gets a crosswalk hit, the other three don't) but generalizes automatically if the crosswalk ever adds coverage for another source, and the mandatory name-verification gate is a real safety net rather than a special case bolted on after the Gibbs finding.
- **Skip fallback matching design and wait for the crosswalk vendor to add DK/RotoGrinders columns.** Rejected — no indication that's coming, and Section 5 step 2 is a hard blocker for the Normalization stage now; per this ADR's own findings, DK and RotoGrinders need fallback matching regardless of what the crosswalk does next.

## Consequences

- Normalization (Section 5 step 2) now has a concrete algorithm: crosswalk fetch/cache (weekly, given `import_ids()` returns current-season data live), a persisted `player_registry` for UUID-fallback players, static team/position alias tables, and the composite fallback matcher — real implementation scope for the Data Integration Engineer, not a data dependency that's still missing.
- The mandatory name-verification gate adds a small amount of matching overhead even on the "confirmed working" PFF path, in exchange for catching exactly the class of silent-misassignment bug this ADR found on Footballguys.
- Team and position alias tables are now a standing maintenance surface (a new team relocation, a new PFF position label) — small, but real; owned by the Data Integration Engineer alongside the ID mapping layer per the Architect's ground rules.
- **Flagged, not resolved here:** whether `gsis_id` coverage is high enough among slate-relevant (rosterable, salaried) players specifically — as opposed to the crosswalk's full 12,494-row population, which includes many players who'll never appear on a DK slate — is worth a QA spot-check against a real weekly DK player list once Phase 1 ingestion is live, rather than assumed from the aggregate 64% figure above.
- **Flagged for QA/Data Integration Engineer before this is treated as fully final:** the fallback-matching policy (ambiguity handling, collision disambiguation order) is engineering/data-matching design, not a Section 6 scoring formula, so it doesn't need Model Analytics Expert / Fantasy Football Expert sign-off per the Architect's task scope here — but QA (owns edge-case testing, Section 10) and the Data Integration Engineer (owns this mapping layer's implementation, Section 10) should both review the policy against real multi-week data before it's load-bearing for live lineup construction.
