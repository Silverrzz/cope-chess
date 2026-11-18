from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from cope.application.errors import ApplicationError

from .definition import CONTENT_KINDS, NUMBER_KINDS
from .storage import definition_from_json, identity, public_form_view


def directory(connection, principal, limit: int, offset: int, show_closed: bool = False) -> dict:
    rows = connection.execute(
        """SELECT f.slug, f.closed, r.definition->>'title' AS title,
                  left(r.definition->>'description', 600) AS description,
                  COALESCE(r.definition->>'response_visibility', 'private') AS response_visibility
           FROM form_definitions f JOIN form_releases r ON r.id = f.published_release
           WHERE r.definition->'listed' = 'true'::jsonb AND r.definition->>'audience' <> 'invited'
             AND (%s OR NOT f.closed)
           ORDER BY r.created_at DESC, f.id LIMIT %s OFFSET %s""", (show_closed, limit + 1, offset)
    ).fetchall()
    return {"items": rows[:limit], "more": len(rows) > limit}


def results(connection, principal, slug: str, cursor: str | None, limit: int) -> dict:
    form = public_form_view(connection, slug, None if principal.actor == "public" else principal)
    definition = definition_from_json(form["definition"])
    mode = definition.response_visibility
    if mode == "private":
        raise ApplicationError("results_private", "Responses for this form are private.", 404)
    if not form["closed"]:
        return {"mode": mode, "published": False}
    form_id, release_id = form["id"], form["release_id"]
    if mode == "summary":
        rows = connection.execute(
            """SELECT v.answers FROM form_responses r
               JOIN form_response_revisions v ON v.response_id = r.id AND v.revision = r.current_revision
               WHERE r.form_id = %s AND r.release_id = %s AND r.withdrawn_at IS NULL""", (form_id, release_id)
        )
        return {"mode": mode, "published": True, **summarize(definition, rows)}
    after = identity(cursor) if cursor else None
    rows = connection.execute(
        """SELECT r.id, v.answers
           FROM form_responses r JOIN form_response_revisions v ON v.response_id = r.id AND v.revision = r.current_revision
           WHERE r.form_id = %s AND r.release_id = %s AND r.withdrawn_at IS NULL
             AND (%s::uuid IS NULL OR r.id > %s::uuid)
           ORDER BY r.id LIMIT %s""", (form_id, release_id, after, after, limit + 1)
    ).fetchall()
    return {"mode": mode, "published": True,
            "items": [{"id": str(row["id"]), "answers": row["answers"]} for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None}


def summarize(definition, rows) -> dict:
    fields = [field for field in definition.fields if field.kind not in CONTENT_KINDS]
    answered: Counter[str] = Counter()
    counts = {field.id: Counter() for field in fields}
    numeric: dict[str, Decimal] = {}
    numeric_counts: Counter[str] = Counter()
    total = 0
    for row in rows:
        total += 1
        for field in fields:
            value = row["answers"].get(field.id)
            if value is None:
                continue
            answered[field.id] += 1
            if field.kind in {"choice", "dropdown", "multiple_choice"}:
                counts[field.id].update(value if isinstance(value, list) else [value])
            elif field.kind in {"checkbox", "yes_no", "consent"}:
                counts[field.id].update(["true" if value else "false"])
            elif field.kind in NUMBER_KINDS:
                try:
                    number = Decimal(value)
                except (InvalidOperation, ValueError, TypeError):
                    continue
                if number.is_finite():
                    numeric[field.id] = numeric.get(field.id, Decimal(0)) + number
                    numeric_counts[field.id] += 1

    output = []
    for field in fields:
        options = [{"label": choice.label, "count": counts[field.id][choice.id]} for choice in field.choices] if field.kind in {
            "choice", "dropdown", "multiple_choice",
        } else []
        if field.kind in {"checkbox", "yes_no", "consent"}:
            options = [{"label": label, "count": counts[field.id][key]} for key, label in (("true", "Yes"), ("false", "No"))]
        output.append({"id": field.id, "label": field.label, "answered": answered[field.id], "options": options,
                       "average": format(numeric[field.id] / numeric_counts[field.id], ".5g")
                       if numeric_counts[field.id] else None})
    return {"total": total, "fields": output}
