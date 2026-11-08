from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

LOG = logging.getLogger(__name__)


def _json(
    payload: Any,
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(jsonable_encoder(payload), status_code=status_code)


def _publish_admin_change(web_app, request: Request) -> None:
    try:
        web_app._publish_admin_post_streams(request)
    except Exception:
        LOG.exception("admin change committed but live publication failed path=%s", request.url.path)
