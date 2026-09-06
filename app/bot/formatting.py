"""
Formats text for Telegram/HTML chat messages.
"""
from html import escape


def _val(d: dict, key: str):
    v = d.get(key)
    return v.get("value") if isinstance(v, dict) else v


def _streak_text(streak: int) -> str:
    n = abs(streak)
    if n == 1:
        return "1 win" if streak > 0 else "1 loss"
    return f"{n} wins" if streak > 0 else f"{n} losses"


def _rank_text(rank: dict) -> str:
    """Renders one rank Stat dict (already JSON-able) as chat text."""
    value = rank.get("value")
    if value is None:
        return "Meh 🤷‍♂️"
    text = f"<b>#{value:,}</b>"
    if rank.get("kind") == "estimate":
        text += " (est.)"
    return text


def format_player_summary(player: dict, analytics: dict, rank: dict | None = None) -> str:
    wl = analytics.get("win_loss", {})
    bb = analytics.get("brawlers", {})
    mm = analytics.get("modes_maps", {})
    lb = analytics.get("leaderboard_estimate", {})

    name = escape(player.get("name", "?"))
    lines = [f"🎮 <b>{name}</b>  {escape(player['tag'])}"]

    trophies, highest = player.get("trophies"), player.get("highest_trophies")
    if trophies is not None:
        line = f"🏆 <b>{trophies:,}</b> trophies"
        if highest is not None:
            line += f" (highest {highest:,})"
        lines.append(line)

    if player.get("club_name"):
        lines.append(f"🛡 Club: {escape(player['club_name'])}")

    wr = _val(wl, "win_rate_pct")
    if wr is not None:
        n = _val(wl, "battles_analyzed")
        recent = _val(wl, "recent_win_rate_pct")
        line = f"📊 Win rate: <b>{wr}%</b>"
        if n:
            line += f" ({n} battles tracked)"
        if recent is not None:
            line += f" · recent: {recent}%"
        lines.append(line)

    streak = _val(wl, "current_streak")
    if streak:
        lines.append(f"🔥 Current streak: {_streak_text(streak)}")

    fav_brawler = _val(bb, "favorite_brawler")
    fav_mode = _val(mm, "favorite_mode")
    if fav_brawler or fav_mode:
        parts = []
        if fav_brawler:
            parts.append(f"Main: {escape(str(fav_brawler))}")
        if fav_mode:
            parts.append(f"Best mode: {escape(str(fav_mode))}")
        lines.append("🎯 " + " · ".join(parts))

    pct = _val(lb, "estimated_percentile_within_pool")
    if pct is not None:
        lines.append(f"🌍 Est. top {pct}% globally")

    rank_global = (rank or {}).get("global")
    if rank_global and rank_global.get("value") is not None:
        lines.append(f"🏅 Global rank: {_rank_text(rank_global)}")

    rank_local = (rank or {}).get("local")
    if rank_local and rank_local.get("value") is not None:
        cc = (rank or {}).get("local_country")
        label = f"Local ({(cc or '?').upper()}) rank" if cc else "Local rank"
        lines.append(f"🏠 {label}: {_rank_text(rank_local)}")

    lines.append("\nUse /card to get the graphical version.")
    return "\n".join(lines)


def format_rank_summary(rank_info: dict) -> str:
    """Renders the /rank command reply from player_service.get_player_rank output."""
    rank = rank_info.get("rank") or {}
    g = rank.get("global") or {}
    lines = [
        f"🎮 <b>{escape(rank_info.get('name', '?'))}</b>  {escape(rank_info['tag'])}",
        f"🏆 {rank_info.get('trophies', 0):,} trophies",
        f"🏅 Global rank: {_rank_text(g)}",
    ]
    note = g.get("note")
    if note:
        lines.append(f"<i>{escape(note)}</i>")

    local = rank.get("local") or {}
    if local:
        cc = rank.get("local_country")
        label = f"Local ({(cc or '?').upper()}) rank" if cc else "Local rank"
        if local.get("value") is not None:
            lines.append(f"🏠 {label}: {_rank_text(local)}")
            if local.get("kind") == "estimate" and local.get("note"):
                lines.append(f"<i>{escape(local['note'])}</i>")
        elif local.get("note"):
            lines.append(f"🏠 {label}: <i>{escape(local['note'])}</i>")
    return "\n".join(lines)


def format_rate_summary(player: dict, analytics: dict) -> str:
    wl = analytics.get("win_loss", {})
    name = escape(player.get("name", "?"))
    wr = _val(wl, "win_rate_pct")
    recent = _val(wl, "recent_win_rate_pct")
    n = _val(wl, "battles_analyzed")

    if wr is None:
        return f"🎮 <b>{name}</b>\nCome back after a few more games."

    lines = [f"🎮 <b>{name}</b>", f"📊 Win rate: <b>{wr}%</b> ({n} battles)"]
    if recent is not None:
        lines.append(f"📈 Recent form: {recent}%")
    streak = _val(wl, "current_streak")
    if streak:
        lines.append(f"🔥 Current streak: {_streak_text(streak)}")
    return "\n".join(lines)


def _power_badge(power: int) -> str:

    return "⭐" if power >= 11 else f"Lvl{power}"


def format_brawlers_list(player: dict) -> str:
    brawlers = player.get("brawlers", [])
    if not brawlers:
        return f"No brawler data found for {escape(player.get('name', 'this player'))} yet."
    name = escape(player.get("name", "?"))
    lines = [f"<b>{name}</b>'s brawlers ({len(brawlers)}) ;p"]
    for b in brawlers:
        lines.append(f"🏆 {b['trophies']:,}  {escape(b['name'])}  {_power_badge(b.get('power'))}")
    return "\n".join(lines)


def format_leaderboard_list(country_code: str, players: list[dict], limit: int = 10) -> str:
    if not players:
        return f"No leaderboard data collected yet for '{escape(country_code)}'. Maybe try again later :)"
    lines = [f"🌍 <b>Top {min(limit, len(players))} - {escape(country_code)}</b>"]
    for p in players[:limit]:
        lines.append(f"#{p['rank']}  {escape(p['name'])}  🏆 {p['trophies']:,}")
    return "\n".join(lines)


def format_top1000_stats(country_code: str, stats: dict) -> str:
    if not stats or stats.get("count", 0) == 0:
        return f"No leaderboard data collected yet for '{escape(country_code)}'."
    lines = [
        f"🌍 <b>Top {stats['count']} stats - {escape(country_code)}</b>",
        f"Mean: {stats['mean_trophies']:,}  ·  Median: {stats['median_trophies']:,}",
        f"Range: {stats['min_trophies']:,} - {stats['max_trophies']:,}",
    ]
    cutoffs = stats.get("rank_cutoffs", {})
    if cutoffs:
        cutoff_line = "  ·  ".join(f"#{rank}: {tr:,}" for rank, tr in sorted(cutoffs.items()) if tr is not None)
        lines.append(cutoff_line)
    return "\n".join(lines)


def format_meta_summary(tiers: dict, top_n: int = 5) -> str:
    most_popular = tiers.get("most_popular", [])
    underrated = tiers.get("underrated", [])
    overpicked = tiers.get("overpicked", [])

    if not most_popular:
        return (
            "🔥 <b>Brawler Meta</b>\n\n"
            "Not enough tracked battle data yet. This grows automatically as more "
            "players are looked up via /player, /rate, or /card."
        )

    lines = ["🔥 <b>Brawler Meta</b>  <i>(based on battles tracked by this bot, not the full player base)</i>"]

    lines.append("\n<b>Most picked</b>")
    for b in most_popular[:top_n]:
        wr = f", {b['win_rate_pct']}% win" if b["win_rate_pct"] is not None else ""
        lines.append(f"{escape(b['brawler_name'])} — {b['pick_rate_pct']}% pick{wr}")

    if underrated:
        lines.append("\n<b>Underrated</b> <i>(strong win rate, rarely picked)</i>")
        for b in underrated[:top_n]:
            lines.append(f"{escape(b['brawler_name'])} — {b['win_rate_pct']}% win, {b['pick_rate_pct']}% pick")

    if overpicked:
        lines.append("\n<b>Overpicked</b> <i>(picked often, underperforming)</i>")
        for b in overpicked[:top_n]:
            lines.append(f"{escape(b['brawler_name'])} — {b['pick_rate_pct']}% pick, {b['win_rate_pct']}% win")

    return "\n".join(lines)
