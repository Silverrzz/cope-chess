from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import datetime

from .errors import ApplicationError


def page_basis(connection, cursor: str | None, scope_key: object) -> tuple[datetime,tuple | None,str]:
    scope = hashlib.sha256(json.dumps(scope_key,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if cursor is None:
        return connection.execute("SELECT clock_timestamp() AS cut").fetchone()["cut"],None,scope
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if set(data)!={"scope","cut","created","id"} or data["scope"]!=scope:
            raise ValueError()
        cut,created = datetime.fromisoformat(data["cut"]),datetime.fromisoformat(data["created"])
        if cut.tzinfo is None or created.tzinfo is None or created>cut:
            raise ValueError()
        return cut,(created,str(uuid.UUID(data["id"]))),scope
    except (ValueError,TypeError,KeyError,UnicodeError,AttributeError) as error:
        raise ApplicationError("invalid_cursor","Refresh this list; its continuation does not match the current query and credential.",422) from error


def page_cursor(scope: str, cut: datetime, created: str, resource_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"scope":scope,"cut":cut.isoformat(),"created":created,"id":resource_id},separators=(",",":")).encode()).decode()
