from math import floor
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


def safe(text: str, limit: int = 200) -> str:
    return discord.utils.escape_markdown(" ".join(text.split())[:limit])


def rating_messages(label: str, ratings: list[dict]) -> list[str]:
    heading = f"{safe(label)} Elo:"
    messages = []
    message = heading
    for rating in ratings:
        elo = floor(rating["elo"] + 0.5)
        margin = rating["error_margin"]
        bounds = f" +/- {floor(margin + 0.5)}" if margin is not None else ""
        line = f'{safe(rating["rating_list"]["name"])}: {elo}{bounds} (#{rating["rank"]})'
        if len(message) + len(line) + 1 > 2000:
            messages.append(message)
            message = heading
        message += "\n" + line
    if not ratings:
        message += "\nThis version is not rated on any list yet."
    messages.append(message)
    return messages


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
        label = "elo"
        if kind == "rating_updated":
            label = "elo adjusted"
            previous_elo = data.get("previous_elo")
            if previous_elo is not None:
                placement += f', {data["elo"] - previous_elo:+d}'
        elif data.get("version_delta") is not None:
            placement += f', {data["version_delta"]:+d}'
        return f"{mentions} {engine} {title} {label}: {rating} ({placement})"
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
        if engine:
            content = f"{engine} started a game in {title}"
        else:
            white = safe(" / ".join(data["white"] or []), 78)
            black = safe(" / ".join(data["black"] or []), 78)
            content = f"{white} vs {black} started in {title}"
        url = game_url(fact, origin)
    return f"{mentions} {content}: {url}"
