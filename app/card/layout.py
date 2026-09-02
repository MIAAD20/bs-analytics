"""
Footer stat badges shown on the card ("Main: Colt", "Recent WR: 68.2%"...).

To add a new badge later: add one dict here with a `key` matching a field
on PlayerCardData (see schema.py), an icon name (see icons.ICONS), a label,
and a `format` function turning the raw value into display text. Nothing
in generator.py needs to change.

If a badge's `key` is None on the PlayerCardData object (data unavailable),
the generator skips that badge entirely rather than showing a blank or
fake value - this is what makes missing API fields "gracefully hidden"
per the project spec.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass
class BadgeDef:
    key: str
    icon: str
    label: str
    format: Callable[[object], str]


BADGE_DEFINITIONS: list[BadgeDef] = [
    BadgeDef("favorite_brawler", "flame", "Main", lambda v: str(v)),
    BadgeDef("favorite_mode", "target", "Best Mode", lambda v: str(v)),
    BadgeDef("recent_win_rate_pct", "checkmark", "Recent WR", lambda v: f"{v:.1f}%"),
    BadgeDef("global_percentile_estimate", "globe", "Top % Global (est.)", lambda v: f"Top {v:.1f}%"),
    BadgeDef("global_rank", "globe", "Global Rank", lambda v: f"#{v:,}"),
]


# Same idea for the two headline number blocks at the top of the card.
@dataclass
class StatBlockDef:
    key: str
    icon: str
    label: str
    format: Callable[[object], str]


STAT_BLOCK_DEFINITIONS: list[StatBlockDef] = [
    StatBlockDef("trophies", "trophy", "Trophies", lambda v: f"{v:,}"),
    StatBlockDef("highest_trophies", "chevron_up", "Highest", lambda v: f"{v:,}"),
    StatBlockDef("total_wins", "checkmark", "Total Wins", lambda v: f"{v:,}"),
    StatBlockDef("win_rate_pct", "target", "Win Rate", lambda v: f"{v:.1f}%"),
]
