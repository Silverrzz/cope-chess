import re
from urllib.parse import urlencode

import discord


ALERTS = {
    "game_started": "Tournament game starts",
    "tournament_started": "Tournament starts",
    "gauntlet_hero_started": "Gauntlet hero starts",
    "event_game_started": "Event game starts",
    "event_started": "Event starts",
    "rating_placed": "First ratings",
    "rating_updated": "Rating updates",
}
DEFAULT_ALERTS = ["tournament_started", "event_started", "rating_placed", "rating_updated"]
RATING_CATEGORY = re.compile(
    r"\b(?:\d+CPU\s+)?(?:(?:All[- ]Round|Top[- ]Class)\s+)?(?:Bullet|Blitz|Rapid|Classical)\b",
    re.IGNORECASE,
)
VARIANTS = {"duck": "Duck-Chess", "duck960": "Duck960", "dfrc": "DFRC"}
FORMATS = {"round_robin": "Round Robin", "swiss": "Swiss", "knockout": "Knockout", "gauntlet": "Gauntlet"}


def safe(text: str, limit: int = 200) -> str:
    return discord.utils.escape_markdown(" ".join(text.split())[:limit])


def time_control(control: dict | None) -> str:
    if not control:
        return ""
    match control["category"]:
        case "increment":
            return f'{control["initial_ms"] / 1000:g}+{control["increment_ms"] / 1000:g}'
        case "movetime":
            return f'{control["move_time_ms"] / 1000:g}s per move'
        case "movestogo":
            return f'{control["moves_to_go"]} moves in {control["initial_ms"] / 1000:g}s'
        case "movenodes":
            return f'{control["nodes"]:,} nodes per move'
        case _:
            return ""


def game_url(game: dict, origin: str) -> str:
    game_id = game["game_id"]
    if game["event_slug"]:
        query = urlencode({"game_id": game_id})
        return f'{origin}/events/{game["event_slug"]}/arena?{query}'
    tournament = game["tournament_slug"] or game["tournament_id"]
    return f"{origin}/tournaments/{tournament}/games/{game_id}"


def notification_text(fact: dict, origin: str) -> str:
    data = fact["payload"]
    kind = fact["kind"]
    title = safe(data["title"], 110)
    mentions = " ".join(f"<@{user_id}>" for user_id in fact["discord_ids"])
    engine = safe(fact["engine"], 100) if fact["engine"] else ""
    if kind in {"rating_placed", "rating_updated"}:
        rating = str(data["elo"])
        if data["uncertainty"] is not None:
            rating += f' +/- {data["uncertainty"]}'
        placement = f'#{data["rank"]}'
        if data.get("version_delta") is not None:
            placement += f', {data["version_delta"]:+d}'
        return f"{mentions} {engine} {title} elo: {rating} ({placement})"
    if kind == "event_started":
        url = f'{origin}/events/{fact["event_slug"]}'
        content = f"{engine} is included in {title}"
    elif kind == "tournament_started":
        tournament = fact["tournament_slug"] or fact["tournament_id"]
        url = f"{origin}/tournaments/{tournament}/standings"
        content = f"{engine} is included in {title}"
    elif kind == "event_game_started":
        url = game_url(fact, origin)
        content = f"{engine} is playing in {title}"
    else:
        if not engine:
            white = safe(" / ".join(data["white"] or []), 78)
            black = safe(" / ".join(data["black"] or []), 78)
            engine = f"{white} vs {black}"
        category = RATING_CATEGORY.search(data["title"])
        pace = category.group().title().replace("Cpu", "CPU") if category else time_control(data.get("time_control"))
        details = [VARIANTS.get(data.get("variant"), ""), pace, FORMATS.get(data.get("format"), "")]
        content = " ".join([engine, *filter(None, details)]) if any(details) else f"{engine} in {title}"
        url = game_url(fact, origin)
    return f"{mentions} {content}: {url}"
