from __future__ import annotations


def list_tags(connection):
    return [dict(row) for row in connection.execute(
        "SELECT id, name, color FROM engine_version_tags ORDER BY lower(name), id"
    )]


def tags_by_version(connection):
    tags = {}
    for row in connection.execute(
        """SELECT assignment.version_id, tag.id, tag.name, tag.color
           FROM engine_version_tag_assignments assignment
           JOIN engine_version_tags tag ON tag.id = assignment.tag_id
           ORDER BY lower(tag.name), tag.id"""
    ):
        tags.setdefault(row["version_id"], []).append({
            "id": row["id"], "name": row["name"], "color": row["color"],
        })
    return tags


def save_tag(connection, name, color, tag_id=None):
    if tag_id is None:
        cursor = connection.execute(
            "INSERT INTO engine_version_tags (name, color) VALUES (?, ?) RETURNING id, name, color",
            (name, color),
        )
    else:
        cursor = connection.execute(
            "UPDATE engine_version_tags SET name = ?, color = ? WHERE id = ? RETURNING id, name, color",
            (name, color, tag_id),
        )
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def delete_tag(connection, tag_id):
    return connection.execute("DELETE FROM engine_version_tags WHERE id = ?", (tag_id,)).rowcount > 0


def update_metadata(connection, version_id, public_hidden, tag_ids):
    row = connection.execute(
        "SELECT id FROM engine_versions WHERE id = ? FOR UPDATE", (version_id,),
    ).fetchone()
    if row is None:
        return False
    if tag_ids is not None:
        ids = sorted(set(tag_ids))
        found = connection.execute(
            "SELECT id FROM engine_version_tags WHERE id = ANY(?) ORDER BY id FOR KEY SHARE", (ids,),
        ).fetchall()
        if len(found) != len(ids):
            raise ValueError("Choose existing engine version tags.")
        connection.execute("DELETE FROM engine_version_tag_assignments WHERE version_id = ?", (version_id,))
        connection.execute(
            """INSERT INTO engine_version_tag_assignments (version_id, tag_id)
               SELECT ?, id FROM engine_version_tags WHERE id = ANY(?)""",
            (version_id, ids),
        )
    if public_hidden is not None:
        connection.execute(
            "UPDATE engine_versions SET public_hidden = ? WHERE id = ?", (public_hidden, version_id),
        )
    return True
