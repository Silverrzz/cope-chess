from urllib.parse import urlencode

import discord


ALERTS = {
    "game_started": "Game starts",
    "tournament_started": "Tournament starts",
    "event_game_started": "Event game starts",
    "event_started": "Event starts",
    "rating_placed": "First ratings",
    "rating_updated": "Rating updates",
}
DEFAULT_ALERTS = ["tournament_started", "event_started", "rating_placed", "rating_updated"]


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


def embed_for(fact: dict, origin: str) -> discord.Embed:
    data = fact["payload"]
    kind = fact["kind"]
    title = safe(data["title"], 110)
    description = time_control(data.get("time_control"))
    if kind in {"rating_placed", "rating_updated"}:
        rating = str(data["elo"])
        if data["uncertainty"] is not None:
            rating += f' +/- {data["uncertainty"]}'
        description = f'{safe(data["engine"], 120)} {title} elo: {rating} (#{data["rank"]})'
        if kind == "rating_updated" and data["elo"] != data["previous_elo"]:
            description += f' · {data["elo"] - data["previous_elo"]:+d} Elo'
        description += f'\n{data["games"]:,} rated games'
        if kind == "rating_placed":
            title = "First rating"
        else:
            title = "Back on the rating list" if data["returned"] else "Rating updated"
        path = f'/ratings/{fact["rating_slug"] or fact["rating_list_id"]}'
    elif kind == "event_started":
        title = f"Event started · {title}"
        path = f'/events/{fact["event_slug"]}'
    elif kind == "tournament_started":
        title = f"Tournament started · {title}"
        path = f'/tournaments/{fact["tournament_slug"] or fact["tournament_id"]}'
    else:
        white = safe(" / ".join(data["white"] or []), 145)
        black = safe(" / ".join(data["black"] or []), 145)
        match = f"{white} vs {black}"
        description = f"{match}\n{description}".rstrip()
        if kind == "game_started":
            title = f"Game started · {title}"
            tournament = fact["tournament_slug"] or fact["tournament_id"]
            path = f'/tournaments/{tournament}/games/{fact["game_id"]}'
        else:
            title = f"Event game started · {title}"
            path = f'/events/{fact["event_slug"]}/arena'
            if data["event_type"] in {"engine-relay", "engine-relay-finale"}:
                path += "?" + urlencode({"game_id": fact["game_id"]})
    return discord.Embed(
        title=title[:256], description=description or None, url=origin + path,
        timestamp=fact["occurred_at"], colour=discord.Colour.from_rgb(41, 122, 92),
    )
