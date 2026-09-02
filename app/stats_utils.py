"""
Statistics primitives shared across the analytics engine (analytics.py) and
the meta/brawler analytics engine (meta.py). Centralizing this avoids two
independent implementations of the same provenance-tagging and
sample-size-confidence logic drifting apart over time.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Confidence(str, Enum):
    """Sample-size confidence for a rate-based statistic (e.g. win rate)."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def confidence_level(sample_size: int, low_max: int, high_min: int) -> Confidence:
    """
    Classifies a sample size into LOW/MEDIUM/HIGH confidence.

    Args:
        sample_size: number of observations backing the statistic.
        low_max: sample sizes below this are LOW confidence.
        high_min: sample sizes at or above this are HIGH confidence.
    """
    if sample_size < low_max:
        return Confidence.LOW
    if sample_size >= high_min:
        return Confidence.HIGH
    return Confidence.MEDIUM


@dataclass
class Stat:
    """
    A single reported value with its provenance.

    `kind` is one of:
        "api"      - taken directly from the Brawl Stars API, unmodified
        "derived"  - a deterministic calculation from API data
        "estimate" - statistically inferred and inherently approximate
    """
    value: Any
    kind: str
    sample_size: Optional[int] = None
    confidence: Optional[Confidence] = None
    note: Optional[str] = None
