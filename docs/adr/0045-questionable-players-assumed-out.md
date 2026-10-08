
## Amendment (2026-10-09): the rule is time-aware

A Questionable tag early in the week is weak evidence, and the same tag after a week of missed practice is strong (Chris). The 2026-10-08 first look
excluded 51 Q players, 35 of them on a Limited Wednesday or no report yet -- which then drove vacated-role shares, analyst calls and bets.

A Q player is now one of three states, decided by the day in the injury-report week (US/Eastern) and the archived practice trajectory
(`injury_lookup.time_aware_decision`; the feeds keep only the latest day, so the trajectory exists only from our own captures):

- **cleared** -- Full participation on the latest report (`Q_CLEARED`), as before;
- **unresolved** -- early week (Mon-Wed): Limited, a first DNP or no report yet; midweek (Thu): anything short of two DNP report days. Available
  (`Q_UNRESOLVED` is not an excluded status) and flagged; his vacated work is NOT assumed; a lineup holding one is flagged in the build record;
- **excluded** -- an official Out/Doubtful game status at any time; two DNP report days by Thursday; and from the Friday report on the original
  rule: anything but Full is out (Limited needs a positive report, i.e. an override).

Overrides still win. The nfl.com injury page was checked as a possible fresher source: it lists the same teams as the nflverse feed at the same
time, so the gap is the NFL's own publication timing and no scraper is needed. Capture `official_injury_capture.py` every day Thursday-Sunday.
