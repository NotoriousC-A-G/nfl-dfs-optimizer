"""Stage 5: GameEnvironmentScore per game (PRD Section 6).

Combination logic lives in `score.py`; see that module's docstring for the exact ingestion-output
shapes it expects and the judgment calls made where the spec (PRD/ADRs) doesn't fully determine
implementation behavior.
"""

from nfl_dfs.game_environment.score import (
    ComponentScore,
    GameEnvironmentScore,
    ImpliedTotalInput,
    PaceProeInput,
    WeatherInput,
    compute_game_environment_score,
    compute_weather_subscore,
)

__all__ = [
    "ComponentScore",
    "GameEnvironmentScore",
    "ImpliedTotalInput",
    "PaceProeInput",
    "WeatherInput",
    "compute_game_environment_score",
    "compute_weather_subscore",
]
