# Working conventions for this repo

## Environments: `main` is production, everything else is staging

There's no separate deployed environment for this project (same as the sister MLB DFS optimizer)
-- **`main` on the local checkout at `/Users/cgasparro/Developer/nfl-dfs-optimizer` *is* production.**
Running any of the `scripts/live_integration_check_*.py` / `scripts/*_backtest.py` tools, or
generating a real slate's lineups, is always done from `main`, on real live data.

**Rule: no development work happens directly on `main`.**

- New work (a feature, a bug fix, a new `CeilingMultiplier` component, anything nontrivial) starts
  on its own branch, cut from `main` -- either a plain `git checkout -b`, or an isolated worktree
  (the Agent tool's `isolation: "worktree"` option, or `git worktree add`) when the work benefits
  from not disturbing the main checkout while it's in progress.
- That branch gets committed to as normal.
- Before it's considered part of "production," it's merged into `main` via a **GitHub pull
  request** (`gh pr create`, reviewed, then merged) -- not a silent local merge. This matches how
  this repo's own early history worked (PR #1, PR #2) before drifting to direct-to-main commits;
  going forward, direct commits to `main` should be exceptional (a trivial doc fix, a one-line
  typo), not the default path for real feature work.
- `main` is kept pushed to `origin/main` (`https://github.com/NotoriousC-A-G/nfl-dfs-optimizer`)
  promptly -- don't let local `main` drift ahead of the remote for long stretches. If a push would
  need to be force-pushed or otherwise looks non-trivial, stop and ask first.

**Why this matters here specifically:** this project's own live scripts read real credentials, hit
real vendor APIs, and (once lineup generation is trusted) will inform real money entered into real
DK contests. Keeping `main` as the one trusted, always-runnable branch -- and never running the
live tool from a half-finished feature branch -- is the actual safety mechanism, not a formality.

## Running things

- Python env: `.venv/bin/python` / `.venv/bin/pytest` (a local venv, not a global interpreter).
- Test suite: `.venv/bin/pytest -q` from the repo root -- fast, no live network, safe to run anytime.
- Live scripts (`scripts/live_integration_check_*.py`, the various `*_backtest.py` research
  scripts, `scripts/injury_snapshot_capture.py`, etc.) are NOT part of `pytest` -- they hit live
  vendor APIs and are run by hand: `PYTHONPATH=. .venv/bin/python scripts/<name>.py`. Most carry a
  `SEASON`/`WEEK` (or `TARGET_WEEK`) module-level constant near the top that has to be kept current
  by hand -- there's no auto-detection of "what week is it," since DraftKings' own slate feed
  doesn't expose a week number (only whichever slate happens to be currently live/unlocked).
