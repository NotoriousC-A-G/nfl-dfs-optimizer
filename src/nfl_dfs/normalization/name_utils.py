"""Name normalization and the mandatory name-verification gate (ADR-0013 decision 2, steps 2-3).

Normalization rules are exactly ADR-0013's list: lowercase; strip Jr./Sr./II/III/IV suffixes;
strip punctuation/periods; strip apostrophes; transliterate accents via NFKD decomposition.
Used by both the crosswalk name-verification gate and the fallback composite matcher, so a
name only needs to be normalized once per comparison side.
"""

from __future__ import annotations

import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    decomposed = unicodedata.normalize("NFKD", raw)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    stripped = _NON_ALNUM.sub("", lowered)
    tokens = [t for t in stripped.split() if t not in _SUFFIXES]
    return _WHITESPACE.sub(" ", " ".join(tokens)).strip()


def names_match(a: str, b: str) -> bool:
    """Exact match after normalization — no fuzzy/edit-distance tolerance.

    Considered and rejected: tolerating things like "Jahmyr Gibbs" vs "J. Gibbs" as a "minor
    formatting difference." Rejected for three reasons. First, this function backs the
    crosswalk name-verification gate (ADR-0013 decision 2 step 2), whose entire job is to catch
    a wrong-ID mapping like the Gibbs/Gibbens case — a gate that tolerates near-misses defeats
    its own purpose, since a false *positive* here silently corrupts identity exactly the way
    the ungated pfr_id join would have. Second, every source currently routed through this gate
    (today: PFF only, via pff_id) is confirmed live to emit full first+last names, never
    initials (`docs/phase0/data-availability.md`'s PFF samples: "Christian McCaffrey", "Chase
    Brown") — so there is no real case among gated sources that actually needs initials
    tolerance. Third, genuine minor formatting noise (suffixes, accents, punctuation) is already
    absorbed by `normalize_name` before this comparison runs; that's what normalization is for,
    and a name that still differs after it is a real disagreement, not formatting noise. A
    failed gate isn't a dead end for the player — ADR-0013 routes it straight to the fallback
    matcher (step 3), so strictness here costs a few extra fallback lookups, not lost coverage.
    """
    return normalize_name(a) == normalize_name(b)
