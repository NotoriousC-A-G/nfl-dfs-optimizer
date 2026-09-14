# NFL DFS Optimizer

DraftKings NFL Classic GPP lineup construction tool. Full spec in [docs/PRD.md](docs/PRD.md).

## Status

Phase 0 (Discovery) — see PRD Section 12. `src/nfl_dfs/` has the pipeline package layout for Section 5's nine stages; each stage is currently an empty module.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env  # fill in API keys / session cookies
pytest
```

## Subagent team

The Section 10 agent team is configured under `.claude/agents/` (Product Owner, Architect, Data Integration Engineer, Model Analytics Expert, Fantasy Football Expert, Performance Analytics, QA, UI/UX) and can be invoked via Claude Code's `/agents`.
