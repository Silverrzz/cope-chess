import re
from urllib.parse import parse_qsl, urlencode

from cope.db.events import list_event_tournament_links


def public_url_catalog(connection):
    slugs = {}
    aliases = []
    for row in connection.execute("SELECT kind, scope_id, resource_id, slug, canonical FROM public_slugs"):
        key = (row["kind"], row["scope_id"], row["resource_id"])
        if row["canonical"]:
            slugs[key] = row["slug"]
        else:
            aliases.append((key, row["slug"]))

    links = {"ratings": {}, "tournaments": {}, "engines": {}, "versions": {}}
    paths = {}
    entries = {}

    def add(key, prefix, id, category):
        slug = slugs.get(key)
        if slug is None:
            return None
        path = f"{prefix}/{slug}"
        links[category][key[2]] = path
        paths[path] = {"id": id, "path": path}
        entries[key] = (prefix, path)
        return path

    for row in connection.execute("SELECT id FROM rating_lists"):
        id = row["id"]
        add(("rating", 0, id), "/ratings", id, "ratings")

    event_ids = {row["tournament_id"] for row in list_event_tournament_links(connection)}
    for row in connection.execute("SELECT id FROM tournaments WHERE status <> 'draft'"):
        id = row["id"]
        if id not in event_ids:
            add(("tournament", 0, id), "/tournaments", id, "tournaments")

    versions = connection.execute(
        """SELECT id, engine_id FROM engine_versions WHERE NOT public_hidden
           ORDER BY COALESCE(source_date, created_at) DESC, created_at DESC, id DESC"""
    )
    for row in versions:
        id, family_id = row["id"], row["engine_id"]
        family_path = links["engines"].get(family_id)
        if family_path is None:
            family_path = add(("engine", 0, family_id), "/engines", id, "engines")
        if family_path:
            add(("version", family_id, id), f"{family_path}/versions", id, "versions")

    for key, slug in sorted(aliases, key=lambda alias: alias[0][0] == "engine"):
        if key not in entries:
            continue
        prefix, canonical = entries[key]
        alias = f"{prefix}/{slug}"
        paths[alias] = paths[canonical]
        if key[0] == "engine":
            for path, entry in tuple(paths.items()):
                if path.startswith(f"{canonical}/versions/"):
                    paths[alias + path[len(canonical):]] = entry

    return {**links, "paths": paths}


def public_redirect(connection, path, query):
    normalized = path.rstrip("/")
    match = re.fullmatch(r"/(ratings|engines|tournaments)(?:/([^/]+))?(.*)", normalized)
    if not match:
        return None
    section, token, suffix = match.groups()
    if section == "ratings" and suffix:
        return None
    if section == "engines" and suffix and not re.fullmatch(r"/versions/[^/]+", suffix):
        return None
    params = parse_qsl(query, keep_blank_values=True)
    values = dict(params)
    if not token and not (section == "ratings" and values.get("rating_list_id")):
        return normalized + (f"?{query}" if query else "") if normalized != path else None
    if section == "tournaments" and suffix and not re.fullmatch(r"/(?:standings|games(?:/[1-9][0-9]*)?|settings)", suffix):
        return None
    catalog = public_url_catalog(connection)
    if section == "ratings" and not token:
        token = values["rating_list_id"]
    if token.isascii() and token.isdecimal() and len(token) <= 19 and (section != "engines" or not suffix):
        canonical = catalog["versions" if section == "engines" else section].get(int(token))
    else:
        lookup = normalized if section == "engines" else f"/{section}/{token}"
        canonical = catalog["paths"].get(lookup, {}).get("path")
    if canonical is None:
        return None
    removed = set()
    if section == "ratings":
        removed.add("rating_list_id")
    if section == "tournaments":
        game = values.get("game_id", "")
        tab = values.get("tab", "")
        if not suffix and re.fullmatch(r"[1-9][0-9]{0,18}", game):
            suffix = f"/games/{game}"
        elif not suffix:
            suffix = f"/{tab}" if tab in {"standings", "games", "settings"} else "/standings"
        canonical += suffix
        removed.update(("game_id", "tab"))
    if canonical == path and not removed.intersection(values):
        return None
    remaining = urlencode([(key, value) for key, value in params if key not in removed])
    return canonical + (f"?{remaining}" if remaining else "")


def rename_public_slug(connection, kind, resource_id, slug):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or slug.isdecimal():
        raise ValueError("Use lowercase words separated by hyphens.")
    current = connection.execute(
        """SELECT scope_id, slug FROM public_slugs
           WHERE kind = ? AND resource_id = ? AND canonical FOR UPDATE""",
        (kind, resource_id),
    ).fetchone()
    if current is None:
        raise LookupError("Public resource not found.")
    if current["slug"] == slug:
        return
    scope = current["scope_id"]
    connection.execute(
        "UPDATE public_slugs SET canonical = false WHERE kind = ? AND scope_id = ? AND slug = ?",
        (kind, scope, current["slug"]),
    )
    row = connection.execute(
        """INSERT INTO public_slugs (kind, scope_id, resource_id, slug)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (kind, scope_id, slug) DO UPDATE SET canonical = true
           WHERE public_slugs.resource_id = EXCLUDED.resource_id
           RETURNING slug""",
        (kind, scope, resource_id, slug),
    ).fetchone()
    if row is None:
        raise ValueError("That slug is reserved by another resource.")
