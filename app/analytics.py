"""
Per-player analytics engine. Pure functions: dict/list in, structured
stats out. Free of database or network calls, which keeps it independently
testable and reusable by the HTTP API, the Telegram bot, and the Player
Card generator alike.

Every returned value is a Stat (see stats_utils.py) tagged with its
provenance - "api", "derived", or "estimate" - and no field is fabricated
when the underlying data is insufficient; in that case its value is None,
optionally with an explanatory note.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from app.battle_outcome import resolve_battle_outcome
from app.stats_utils import Stat, confidence_level

MIN_SAMPLE_OVERALL_WINRATE = 10
MIN_SAMPLE_BRAWLER_WINRATE = 5
MIN_SAMPLE_MODE_WINRATE = 5
RECENT_WINDOW = 20


@dataclass
class BattleOutcome:
    """Normalized representation of one stored battle row."""
    is_win: Optional[bool]  # None = unresolved mode, excluded from win-rate math
    mode: Optional[str]
    map_name: Optional[str]
    brawler_name: Optional[str]
    trophy_change: Optional[int]
    battle_time: Any


def classify_battle(battle: dict) -> BattleOutcome:
    """
    Converts a stored battle row (see the `battles` table / ingest.py) into
    a BattleOutcome. Win/loss classification is delegated to
    battle_outcome.resolve_battle_outcome() so ingestion-time and
    analysis-time classification never diverge.
    """
    return BattleOutcome(
        is_win=resolve_battle_outcome(battle.get("result"), battle.get("rank"), battle.get("mode")),
        mode=battle.get("mode"),
        map_name=battle.get("map"),
        brawler_name=battle.get("brawler_name"),
        trophy_change=battle.get("trophy_change"),
        battle_time=battle.get("battle_time"),
    )


def trophy_progress(current_trophies: int, highest_trophies: int) -> dict:
    """API data (both numbers) + one derived percentage."""
    pct = (current_trophies / highest_trophies * 100) if highest_trophies else None
    return {
        "current_trophies": Stat(current_trophies, "api"),
        "highest_trophies": Stat(highest_trophies, "api"),
        "current_vs_highest_pct": Stat(
            round(pct, 2) if pct is not None else None, "derived"
        ),
        "trophies_below_peak": Stat(
            max(highest_trophies - current_trophies, 0), "derived"
        ),
    }


def win_loss_stats(battles: list[dict]) -> dict:
    """
    Overall win rate, streaks, recent form. Only counts battles with a
    resolvable is_win (see classify_battle) - ambiguous modes are excluded
    and reported via `excluded_count` rather than silently dropped.
    """
    outcomes = [classify_battle(b) for b in battles]
    # sort oldest -> newest for streak math
    outcomes.sort(key=lambda o: o.battle_time or 0)

    resolvable = [o for o in outcomes if o.is_win is not None]
    excluded = len(outcomes) - len(resolvable)

    wins = sum(1 for o in resolvable if o.is_win)
    n = len(resolvable)
    win_rate = (wins / n * 100) if n else None

    longest_win_streak = current_streak = 0
    cur_type = None
    streak_len = 0
    for o in resolvable:
        if o.is_win == cur_type:
            streak_len += 1
        else:
            cur_type = o.is_win
            streak_len = 1
        if cur_type is True:
            longest_win_streak = max(longest_win_streak, streak_len)
    if resolvable:
        last_type = resolvable[-1].is_win
        run = 0
        for o in reversed(resolvable):
            if o.is_win == last_type:
                run += 1
            else:
                break
        current_streak = run if last_type else -run

    recent = resolvable[-RECENT_WINDOW:]
    recent_wins = sum(1 for o in recent if o.is_win)
    recent_win_rate = (recent_wins / len(recent) * 100) if recent else None

    return {
        "battles_analyzed": Stat(n, "derived", note=f"{excluded} battles excluded (no resolvable win/loss, e.g. special events)"),
        "win_rate_pct": Stat(
            round(win_rate, 1) if win_rate is not None else None,
            "derived",
            sample_size=n,
            confidence=confidence_level(n, MIN_SAMPLE_OVERALL_WINRATE, 40),
        ),
        "recent_win_rate_pct": Stat(
            round(recent_win_rate, 1) if recent_win_rate is not None else None,
            "derived",
            sample_size=len(recent),
            confidence=confidence_level(len(recent), 5, 15),
            note=f"last {len(recent)} resolvable battles in stored history (not full account history)",
        ),
        "longest_win_streak": Stat(longest_win_streak, "derived", sample_size=n),
        "current_streak": Stat(
            current_streak, "derived",
            note="positive = win streak, negative = loss streak, 0 = no data",
        ),
    }


def brawler_breakdown(battles: list[dict]) -> dict:
    """Pick rate + win rate per brawler, from stored battlelog history."""
    outcomes = [classify_battle(b) for b in battles if b.get("brawler_name")]
    total = len(outcomes)
    by_brawler: dict[str, list[BattleOutcome]] = defaultdict(list)
    for o in outcomes:
        by_brawler[o.brawler_name].append(o)

    result = {}
    for name, rows in by_brawler.items():
        resolvable = [r for r in rows if r.is_win is not None]
        n = len(resolvable)
        wins = sum(1 for r in resolvable if r.is_win)
        win_rate = (wins / n * 100) if n else None
        result[name] = {
            "pick_count": Stat(len(rows), "derived"),
            "pick_rate_pct": Stat(
                round(len(rows) / total * 100, 1) if total else None, "derived"
            ),
            "win_rate_pct": Stat(
                round(win_rate, 1) if win_rate is not None else None,
                "derived",
                sample_size=n,
                confidence=confidence_level(n, MIN_SAMPLE_BRAWLER_WINRATE, 20),
                note=None if n >= MIN_SAMPLE_BRAWLER_WINRATE else "small sample - win rate may be misleading",
            ),
        }

    favorite = max(by_brawler.items(), key=lambda kv: len(kv[1]))[0] if by_brawler else None

    qualified = {
        name: data for name, data in result.items()
        if data["win_rate_pct"].sample_size and data["win_rate_pct"].sample_size >= MIN_SAMPLE_BRAWLER_WINRATE
    }
    most_successful = None
    if qualified:
        most_successful = max(qualified.items(), key=lambda kv: kv[1]["win_rate_pct"].value or 0)[0]

    return {
        "per_brawler": result,
        "favorite_brawler": Stat(favorite, "derived", note="most-picked brawler in stored battle history"),
        "most_successful_brawler": Stat(
            most_successful, "derived",
            note="highest win rate among brawlers with >= "
                 f"{MIN_SAMPLE_BRAWLER_WINRATE} recorded battles" if most_successful
                 else "insufficient sample size for any brawler",
        ),
        "brawler_diversity": _diversity_index(by_brawler, total),
    }


def _diversity_index(by_brawler: dict, total: int) -> Stat:
    """
    Shannon entropy normalized 0-1: 0 = plays one brawler exclusively,
    1 = perfectly even spread across all played brawlers. A simple,
    honest measure of specialization vs versatility.
    """
    if not total or len(by_brawler) <= 1:
        return Stat(0.0 if total else None, "derived", note="specialization score, 0=one-trick, 1=fully versatile")
    counts = [len(v) for v in by_brawler.values()]
    probs = [c / total for c in counts]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(len(by_brawler))
    normalized = entropy / max_entropy if max_entropy else 0.0
    return Stat(round(normalized, 3), "derived", note="specialization score, 0=one-trick, 1=fully versatile")


def mode_and_map_breakdown(battles: list[dict]) -> dict:
    outcomes = [classify_battle(b) for b in battles]

    def _agg(key_fn):
        buckets: dict[str, list[BattleOutcome]] = defaultdict(list)
        for o in outcomes:
            k = key_fn(o)
            if k:
                buckets[k].append(o)
        out = {}
        for k, rows in buckets.items():
            resolvable = [r for r in rows if r.is_win is not None]
            n = len(resolvable)
            wins = sum(1 for r in resolvable if r.is_win)
            wr = (wins / n * 100) if n else None
            out[k] = {
                "play_count": Stat(len(rows), "derived"),
                "win_rate_pct": Stat(
                    round(wr, 1) if wr is not None else None, "derived",
                    sample_size=n,
                    confidence=confidence_level(n, MIN_SAMPLE_MODE_WINRATE, 20),
                ),
            }
        return out

    by_mode = _agg(lambda o: o.mode)
    by_map = _agg(lambda o: o.map_name)

    favorite_mode = max(by_mode.items(), key=lambda kv: kv[1]["play_count"].value)[0] if by_mode else None

    return {
        "by_mode": by_mode,
        "by_map": by_map,
        "favorite_mode": Stat(favorite_mode, "derived", note="most-played mode in stored battle history"),
    }


def performance_score(win_rate_pct: Optional[float], diversity: Optional[float], trophy_pct: Optional[float]) -> Stat:
    """
    Composite 0-100 ESTIMATE, not an official metric. Weighted blend of
    current form, versatility and trophy-vs-peak. Explicitly labeled as
    estimate since the weighting is a design choice, not derived from
    Supercell data.
    """
    parts = [p for p in (win_rate_pct, (diversity or 0) * 100, trophy_pct) if p is not None]
    if not parts:
        return Stat(None, "estimate", note="insufficient data")
    score = 0.0
    total_w = 0.0
    if win_rate_pct is not None:
        score += win_rate_pct * 0.5
        total_w += 0.5
    if diversity is not None:
        score += diversity * 100 * 0.2
        total_w += 0.2
    if trophy_pct is not None:
        score += trophy_pct * 0.3
        total_w += 0.3
    final = score / total_w if total_w else None
    return Stat(
        round(final, 1) if final is not None else None,
        "estimate",
        note="weighted blend of win rate (50%), brawler diversity (20%), trophies-vs-peak (30%) - not an official Supercell metric",
    )


def leaderboard_percentile(player_trophies: int, pool_trophies: list[int]) -> dict:
    """
    Estimate only. There is no API for total player counts or a true global
    percentile, so any percentile/rank claim within the sampled pool is
    explicitly an estimate, never presented as fact.
    """
    if not pool_trophies:
        return {
            "in_top1000_range": Stat(None, "estimate", note="no leaderboard snapshot available"),
            "estimated_percentile_within_pool": Stat(None, "estimate"),
        }
    sorted_pool = sorted(pool_trophies, reverse=True)
    in_range = player_trophies >= sorted_pool[-1]
    rank_within_pool = None
    for i, t in enumerate(sorted_pool):
        if player_trophies >= t:
            rank_within_pool = i + 1
            break
    pct_within_pool = (
        round((1 - (rank_within_pool - 1) / len(sorted_pool)) * 100, 2)
        if rank_within_pool else None
    )
    return {
        "in_top1000_range": Stat(in_range, "estimate", note="whether trophies meet the deepest currently tracked cutoff"),
        "estimated_rank_within_pool": Stat(rank_within_pool, "estimate", note="rank estimate strictly within the tracked pool, not global"),
        "estimated_percentile_within_pool": Stat(pct_within_pool, "estimate"),
    }


def estimate_rank_from_pool(trophies: int, pool: list[tuple[int, int]]) -> Stat:
    """
    Rank for a player NOT on the API-visible leaderboard (which tops out at
    200 - deeper ranks are published nowhere).

    Deliberately conservative: the trophy curve across the visible top-200
    is nearly flat, so long-range extrapolation is wildly wrong (a naive
    power-law fit put a 3.7k-trophy player at rank 1e25). Only a short-range
    estimate within 10% of the cutoff is offered; anything deeper gets None
    plus a note - no rank beats a fabricated one. `pool` is (rank, trophies)
    pairs in ranked order from the tracked table.
    """
    points = [(r, t) for r, t in pool if r > 0 and t > 0]
    if trophies <= 0 or len(points) < 10:
        return Stat(None, "estimate", note="not enough leaderboard data to estimate a rank")

    deepest_rank = max(r for r, _ in points)
    cutoff_trophies = min(t for _, t in points)  # trophies at the deepest visible rank
    if trophies >= cutoff_trophies:
        # Meets the visible cutoff but is not in the tracked table - the
        # table is one refresh behind. Next collection makes this exact.
        return Stat(
            None, "estimate",
            note=(f"trophies meet the top-{deepest_rank} cutoff ({cutoff_trophies:,}) - "
                  "rank becomes exact after the next leaderboard refresh"),
        )

    if trophies >= 0.9 * cutoff_trophies:
        xs = [math.log(r) for r, _ in points]
        ys = [math.log(t) for _, t in points]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        sxx = sum((x - mean_x) ** 2 for x in xs)
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        if sxx == 0:
            return Stat(None, "estimate", note="not enough leaderboard data to estimate a rank")
        slope = sxy / sxx
        intercept = mean_y - slope * mean_x
        if slope < 0:
            estimated = math.exp((math.log(trophies) - intercept) / slope)
            estimated = max(deepest_rank + 1, round(estimated))
            return Stat(
                estimated, "estimate",
                note=(f"estimated from the visible top-{deepest_rank} trophy curve, "
                      "close to its cutoff - treat as approximate"),
            )

    return Stat(
        None, "estimate",
        note=(f"below the API-visible top-{deepest_rank} leaderboard (cutoff: "
              f"{cutoff_trophies:,} trophies) - Supercell does not publish deeper ranks"),
    )


def full_player_analytics(
    snapshot: dict,
    battles: list[dict],
    pool_trophies: Optional[list[int]] = None,
) -> dict:
    """Convenience aggregator combining everything above for one player."""
    tp = trophy_progress(snapshot["trophies"], snapshot["highest_trophies"])
    wl = win_loss_stats(battles)
    bb = brawler_breakdown(battles)
    mm = mode_and_map_breakdown(battles)
    perf = performance_score(
        wl["win_rate_pct"].value,
        bb["brawler_diversity"].value,
        tp["current_vs_highest_pct"].value,
    )
    lb = leaderboard_percentile(snapshot["trophies"], pool_trophies or [])

    return {
        "trophy_progress": tp,
        "win_loss": wl,
        "brawlers": bb,
        "modes_maps": mm,
        "performance_score": perf,
        "leaderboard_estimate": lb,
    }
