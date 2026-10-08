# Weekly build runbook (ADR-0046) -- from `main` only

All commands: `PYTHONPATH=. .venv/bin/python scripts/<name>.py`. Live scripts run from `main`, never a feature branch (CLAUDE.md).
Bump `WEEK` and `DRAFT_GROUP_ID` in `scripts/live_integration_check_dashboard.py` first (Week 5: draft group 154468 -- confirm it is the main slate).
`NFL_DFS_WEEK=<n>` overrides `WEEK` for dry runs against a saved earlier week; leave it unset for a real run.

## Wed / Thu / Fri / Sat / Sun morning -- capture practice evidence
`official_injury_capture.py` (archives the official report; the Wed -> Fri trajectory exists only if we capture it).

## Thursday evening -- first look (not the go/no-go)
1. `live_integration_check_dashboard.py` -- builds the legacy state (Chalk Anchor baseline + six legacy agents) and saves the snapshot, now
   including every Questionable/override decision (`redesign.availability`).
2. `render_evidence_packets.py` -- open `dashboard_output/evidence_packets.html`; read the data gaps and the vacated roles.
3. `run_llm_stages.py prepare-analysts` -> a backend answers each `prompt.md` with `response.json` -> `collect-analysts` (retry once on a rejected
   answer) -> `prepare-expert` -> answer -> `collect-expert`.
4. `build_pool_lineups.py` (one lineup per agent: its favorite variation, listed first by the expert; `--n 2`/`--n 3` also builds the alternates). An agent that under-spends or cannot be built goes back to the expert ONCE (exit code 2 while a repair request
   waits in `data/cache/build_llm/<season>/<week>/expert_repair/<agent>__<key>/prompt.md`); answer it, re-run. An agent still failing is reported
   unavailable with its diagnosis -- nothing is substituted.
   Every run writes a build record to `data/snapshots/redesign/` (theses, pools, lineups, failures) for the post-mortem.

## Friday -- generation (after practice reports)
Capture the injury report, then repeat steps 1-4. Q decisions are automatic (Full practice clears); override with `log_q_override.py` when you
know more, then re-run from step 1.

## Sunday ~noon -- the real go/no-go
`sunday_availability_check.py` re-pulls DK/official/RotoGrinders and prints every status change. If anything moved, re-run step 1 and then
steps 3-4: analyst answers are keyed on each game's MATERIAL facts (line to the half point, unit metrics, availability, vacated roles, weather),
so only games whose facts changed are re-asked; projection drift alone re-asks nothing. The expert is re-asked (its key includes every packet).
