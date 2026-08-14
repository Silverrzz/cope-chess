from __future__ import annotations

import asyncio
import contextlib
import copy
import hmac
import ipaddress
import math
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlsplit, urlunsplit

import chess
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from cope.chat import (
    DEFAULT_COMMAND_REGISTRY,
    ChatCommandContext,
    ChatCommandError,
    parse_chat_command,
)
from cope.db import (
    DEFAULT_DATABASE_URL,
    SCHEMA_VERSION,
    ChatSettingsRecord,
    ChatMessageRecord,
    EventChatSettingsRecord,
    EventRecord,
    GameRecord,
    MoveRecord,
    TournamentRecord,
    active_engine_hardware_profiles,
    connect_database,
    create_chat_message,
    database_schema_version,
    get_chat_settings,
    get_chat_message,
    get_event_by_slug,
    get_event_chat_settings,
    get_game,
    get_opening_position,
    get_opening_suite,
    get_tournament,
    get_worker,
    get_worker_activity,
    get_service_endpoint,
    list_active_games,
    list_benchmarkers,
    list_engine_records,
    list_engines,
    list_games,
    list_events,
    list_moves,
    list_tournaments,
    list_tool_jobs,
    list_tournament_matches,
    list_upcoming_games,
    list_worker_tournament_ids,
    list_workers,
    list_worker_failures,
    list_worker_resource_samples,
    list_worker_activities,
    list_worker_event_ids,
    touch_service_heartbeat,
)
from cope.core.san import pv_to_san
from cope.core.models import ENGINE_PROCESS_MEMORY_OVERHEAD_MB, HardwareInfo, TournamentFormat
from cope.core.stream import (
    StreamEnvelope,
    StreamProtocolError,
    decode_stream_event,
    encode_stream_event,
    make_stream_event,
    sse_stream_event,
)
from cope.network import (
    ADMIN_TOKEN_ENV,
    DEFAULT_BENCHMARKER_PATH,
    LOCAL_EVENT_PUBLISHERS,
    default_admin_token,
    default_benchmark_server_port,
    default_web_event_token,
    DEFAULT_WORKER_PATH,
    WILDCARD_HOSTS,
    default_worker_port,
)
from cope.web.forms import form_value
from cope.version import app_version
from cope.tournament.estimates import TournamentEstimator


PACKAGE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST_DIR = PACKAGE_DIR / "frontend_dist"
FRONTEND_INDEX = FRONTEND_DIST_DIR / "index.html"
ADMIN_SESSION_MAX_AGE_SECONDS = 43_200
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MAX_BROADCAST_SNAPSHOT_GAMES = 1000

# Valid admin actions on a tournament, per current status.
TOURNAMENT_ACTIONS: dict[str, dict[str, str]] = {
    "scheduled": {"abort": "aborted"},
    "running": {"pause": "paused", "abort": "aborted"},
    "paused": {"resume": "running", "abort": "aborted"},
    "aborted": {"restore": "paused"},
}
CONNECTED_WORKER_STATUSES = {"connected", "downloading", "ready", "busy"}
WORKER_RECENT_SECONDS = 60


class StreamBacklogExceeded(RuntimeError):
    pass


class StreamSubscription:
    def __init__(self, topics: tuple[str, ...], *, max_queue: int) -> None:
        self.topics = topics
        self.queue: asyncio.Queue[StreamEnvelope | None] = asyncio.Queue(maxsize=max_queue)
        self.closed = False

    def enqueue(self, event: StreamEnvelope) -> None:
        if self.closed:
            return
        if self.queue.full():
            self.closed = True
            while not self.queue.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.queue.get_nowait()
            self.queue.put_nowait(None)
            return
        self.queue.put_nowait(event)

    def enqueue_ephemeral(self, event: StreamEnvelope) -> None:
        if self.closed or self.queue.qsize() >= max(1, self.queue.maxsize // 8):
            return
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(event)


class StreamHub:
    def __init__(self, *, max_subscribers: int = 256, max_queue: int = 512) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq_by_topic: dict[str, int] = {}
        self._subscribers: dict[str, set[StreamSubscription]] = {}
        self._non_spectator_subscriptions: set[StreamSubscription] = set()
        self._internal_clients: set[asyncio.Queue[StreamEnvelope | None]] = set()
        self._tournament_live: dict[int, dict[int, dict[str, Any]]] = {}
        self._event_tournaments: dict[int, set[int]] = {}
        self._tournament_events: dict[int, set[int]] = {}
        self._ephemeral_buckets: dict[str, tuple[float, float]] = {}
        self._ephemeral_cleanup_at = 0.0
        self._max_subscribers = max_subscribers
        self._max_queue = max_queue

    def bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            self._loop = loop

    def subscribe(self, *topics: str, spectator: bool = True) -> StreamSubscription:
        with self._lock:
            count = sum(len(items) for items in self._subscribers.values())
            if count >= self._max_subscribers:
                raise StreamBacklogExceeded("too many stream subscribers")
            subscription = StreamSubscription(tuple(topics), max_queue=self._max_queue)
            for topic in topics:
                self._subscribers.setdefault(topic, set()).add(subscription)
            if not spectator:
                self._non_spectator_subscriptions.add(subscription)
            return subscription

    def unsubscribe(self, subscription: StreamSubscription) -> None:
        with self._lock:
            subscription.closed = True
            self._non_spectator_subscriptions.discard(subscription)
            for topic in subscription.topics:
                subscribers = self._subscribers.get(topic)
                if subscribers is None:
                    continue
                subscribers.discard(subscription)
                if not subscribers:
                    self._subscribers.pop(topic, None)

    def tournament_spectator_count(self, tournament_id: int) -> int:
        with self._lock:
            subscriptions = self._spectator_subscribers(f"tournament.{tournament_id}")
            for event_id in self._tournament_events.get(tournament_id, ()):
                subscriptions.update(self._spectator_subscribers(f"event.{event_id}"))
                for linked_tournament_id in self._event_tournaments.get(event_id, ()):
                    subscriptions.update(self._spectator_subscribers(f"tournament.{linked_tournament_id}"))
            return len(subscriptions)

    def link_event_tournaments(self, event_id: int, tournament_ids: tuple[int, ...]) -> None:
        with self._lock:
            previous = self._event_tournaments.get(event_id, set())
            current = set(tournament_ids)
            for tournament_id in previous - current:
                events = self._tournament_events.get(tournament_id)
                if events is not None:
                    events.discard(event_id)
                    if not events:
                        self._tournament_events.pop(tournament_id, None)
            self._event_tournaments[event_id] = current
            for tournament_id in current:
                self._tournament_events.setdefault(tournament_id, set()).add(event_id)

    def event_spectator_count(self, event_id: int) -> int:
        with self._lock:
            subscriptions = self._spectator_subscribers(f"event.{event_id}")
            for tournament_id in self._event_tournaments.get(event_id, ()):
                subscriptions.update(self._spectator_subscribers(f"tournament.{tournament_id}"))
            return len(subscriptions)

    def _spectator_subscribers(self, topic: str) -> set[StreamSubscription]:
        return self._subscribers.get(topic, set()) - self._non_spectator_subscriptions

    def publish_event_spectators(self, event_id: int) -> int:
        with self._lock:
            tournament_ids = tuple(self._event_tournaments.get(event_id, ()))
        count = self.event_spectator_count(event_id)
        data = {"event_id": event_id, "spectator_count": count}
        self.publish(f"event.{event_id}", "spectators.changed", data, source="web")
        for tournament_id in tournament_ids:
            self.publish(
                f"tournament.{tournament_id}",
                "spectators.changed",
                {**data, "tournament_id": tournament_id},
                source="web",
            )
        return count

    def publish_tournament_spectators(self, tournament_id: int) -> int:
        tournament_topic = f"tournament.{tournament_id}"
        topics = (tournament_topic, "tournament-spectators")
        dispatches: list[tuple[StreamEnvelope, tuple[StreamSubscription, ...]]] = []
        with self._lock:
            event_ids = tuple(self._tournament_events.get(tournament_id, ()))
            subscriptions = self._spectator_subscribers(tournament_topic)
            for event_id in event_ids:
                subscriptions.update(self._spectator_subscribers(f"event.{event_id}"))
                for linked_tournament_id in self._event_tournaments.get(event_id, ()):
                    subscriptions.update(self._spectator_subscribers(f"tournament.{linked_tournament_id}"))
            spectator_count = len(subscriptions)
            data = {
                "tournament_id": tournament_id,
                "spectator_count": spectator_count,
            }
            for topic in topics:
                seq = self._seq_by_topic.get(topic, 0) + 1
                self._seq_by_topic[topic] = seq
                event = make_stream_event(
                    topic,
                    "spectators.changed",
                    data,
                    source="web",
                    seq=seq,
                    event_id=f"{topic}:{seq}",
                )
                dispatches.append((event, tuple(self._subscribers.get(topic, ()))))
            loop = self._loop
        if loop is not None:
            for event, subscribers in dispatches:
                for subscription in subscribers:
                    loop.call_soon_threadsafe(subscription.enqueue, event)
        for event_id in event_ids:
            self.publish(
                f"event.{event_id}",
                "spectators.changed",
                {"event_id": event_id, "tournament_id": tournament_id, "spectator_count": spectator_count},
                source="web",
            )
        return spectator_count

    def register_internal_client(self) -> asyncio.Queue[StreamEnvelope | None]:
        queue: asyncio.Queue[StreamEnvelope | None] = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._internal_clients.add(queue)
        return queue

    def unregister_internal_client(self, queue: asyncio.Queue[StreamEnvelope | None]) -> None:
        with self._lock:
            self._internal_clients.discard(queue)

    def publish(
        self,
        topic: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        source: str = "web",
        ephemeral: bool = False,
    ) -> StreamEnvelope:
        with self._lock:
            seq = self._seq_by_topic.get(topic, 0) + 1
            self._seq_by_topic[topic] = seq
            event = make_stream_event(
                topic,
                event_type,
                data,
                source=source,
                seq=seq,
                event_id=f"{topic}:{seq}",
            )
            self._record_live_event(event)
            subscribers = tuple(self._subscribers.get(topic, ()))
            linked_event_ids: tuple[int, ...] = ()
            linked_tournament_id: int | None = None
            if event_type in {"tournament.changed", "tournament.snapshot"}:
                linked_tournament_id = _event_tournament_id(event)
                if linked_tournament_id is not None:
                    linked_event_ids = tuple(
                        self._tournament_events.get(linked_tournament_id, ())
                    )
            loop = self._loop
        if loop is None:
            return event
        if ephemeral:
            loop.call_soon_threadsafe(_enqueue_ephemeral_subscribers, subscribers, event)
            return event
        for subscription in subscribers:
            loop.call_soon_threadsafe(subscription.enqueue, event)
        for event_id in linked_event_ids:
            self.publish(
                f"event.{event_id}",
                "event.changed",
                {
                    "event_id": event_id,
                    "tournament_id": linked_tournament_id,
                },
                source=source,
            )
        return event

    def allow_ephemeral(self, key: str, *, rate: float, burst: int) -> bool:
        now = time.monotonic()
        with self._lock:
            if now >= self._ephemeral_cleanup_at:
                cutoff = now - 300.0
                self._ephemeral_buckets = {
                    bucket_key: bucket
                    for bucket_key, bucket in self._ephemeral_buckets.items()
                    if bucket[1] >= cutoff
                }
                self._ephemeral_cleanup_at = now + 30.0
            current = self._ephemeral_buckets.get(key)
            if current is None:
                if len(self._ephemeral_buckets) >= 2048:
                    return False
                tokens = float(burst)
            else:
                tokens = min(float(burst), current[0] + (now - current[1]) * rate)
            allowed = tokens >= 1.0
            self._ephemeral_buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            return allowed

    def make_private_event(
        self,
        topic: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        source: str = "web",
    ) -> StreamEnvelope:
        return make_stream_event(
            topic,
            event_type,
            data,
            source=source,
            seq=0,
            event_id=f"{topic}:0",
        )

    def publish_to_internal(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> StreamEnvelope:
        event = self.publish("runner", event_type, data, source="web")
        with self._lock:
            clients = tuple(self._internal_clients)
            loop = self._loop
        if loop is None:
            return event
        for queue in clients:
            loop.call_soon_threadsafe(_enqueue_internal_event, queue, event)
        return event

    def tournament_live(
        self,
        tournament_id: int,
        game_id: int | None = None,
    ) -> dict[int, dict[str, Any]] | dict[str, Any] | None:
        with self._lock:
            live_games = self._tournament_live.get(tournament_id)
            if live_games is None:
                return None
            if game_id is not None:
                live = live_games.get(game_id)
                return copy.deepcopy(live) if live is not None else None
            return copy.deepcopy(live_games)

    def clear_tournament_live(self, tournament_id: int) -> None:
        with self._lock:
            self._tournament_live.pop(tournament_id, None)

    def prune_tournament_live(
        self,
        tournament_id: int,
        active_game_ids: set[int],
    ) -> None:
        with self._lock:
            games = self._tournament_live.get(tournament_id)
            if games is None:
                return
            for game_id in tuple(games):
                if game_id not in active_game_ids:
                    games.pop(game_id, None)
            if not games:
                self._tournament_live.pop(tournament_id, None)

    def _record_live_event(self, event: StreamEnvelope) -> None:
        tournament_id = _event_tournament_id(event)
        if tournament_id is None:
            return
        game_id = _event_game_id(event)
        if event.type == "game.move":
            if game_id is not None:
                games = self._tournament_live.get(tournament_id)
                if games is not None:
                    games.pop(game_id, None)
                    if not games:
                        self._tournament_live.pop(tournament_id, None)
            return
        if event.type == "tournament.live":
            live = event.data.get("live")
            if isinstance(live, dict):
                live_game_id = _positive_int(live.get("game_id")) or game_id
                if live.get("clear"):
                    if live_game_id is None:
                        self._tournament_live.pop(tournament_id, None)
                    else:
                        games = self._tournament_live.get(tournament_id)
                        if games is not None:
                            games.pop(live_game_id, None)
                            if not games:
                                self._tournament_live.pop(tournament_id, None)
                elif live_game_id is not None:
                    self._tournament_live.setdefault(tournament_id, {})[
                        live_game_id
                    ] = dict(live)
            return
        if event.type == "engine.info":
            side = event.data.get("side")
            engine_data = event.data.get("engine_data")
            if (
                game_id is None
                or side not in {"white", "black", "kibitzer"}
                or not isinstance(engine_data, dict)
            ):
                return
            live = self._tournament_live.setdefault(tournament_id, {}).setdefault(
                game_id,
                {"game_id": game_id, "engine_data": {}, "clocks": {}},
            )
            live["game_id"] = game_id
            live.setdefault("engine_data", {})[side] = dict(engine_data)
            return
        if event.type == "clock.sync":
            clocks = event.data.get("clocks_ms")
            if game_id is None or not isinstance(clocks, dict):
                return
            live = self._tournament_live.setdefault(tournament_id, {}).setdefault(
                game_id,
                {"game_id": game_id, "engine_data": {}, "clocks": {}},
            )
            live["game_id"] = game_id
            live["clocks"] = dict(clocks)
            live["clock_state"] = {
                "game_id": game_id,
                "clocks_ms": dict(clocks),
                "active_side": event.data.get("active_side"),
                "running": bool(event.data.get("running")),
                "observed_at": event.sent_at,
                "sent_at": event.sent_at,
            }


def _enqueue_internal_event(
    queue: asyncio.Queue[StreamEnvelope | None],
    event: StreamEnvelope,
) -> None:
    if queue.full():
        while not queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(None)
        return
    queue.put_nowait(event)


def _enqueue_ephemeral_subscribers(
    subscriptions: tuple[StreamSubscription, ...],
    event: StreamEnvelope,
) -> None:
    for subscription in subscriptions:
        subscription.enqueue_ephemeral(event)


def create_app(
    db_path: str | Path = DEFAULT_DATABASE_URL,
    *,
    worker_server_url: str | None = None,
    benchmarker_server_url: str | None = None,
    event_token: str | None = None,
    admin_token: str | None = None,
) -> FastAPI:
    app = FastAPI(title="COPE Chess")
    app.state.db_path = str(db_path)
    app.state.worker_server_url = worker_server_url
    app.state.benchmarker_server_url = benchmarker_server_url
    app.state.event_token = event_token or default_web_event_token()
    app.state.admin_token = admin_token or default_admin_token()
    app.state.stream_hub = StreamHub()
    app.state.request_limits = {}
    app.state.last_service_heartbeat = 0.0
    app.state.worker_snapshot_task = None
    app.state.tournament_snapshot_tasks = {}
    app.add_middleware(GZipMiddleware, minimum_size=1_000)
    frontend_assets = FRONTEND_DIST_DIR / "assets"
    if frontend_assets.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(frontend_assets)),
            name="frontend-assets",
        )
    frontend_audio = FRONTEND_DIST_DIR / "audio"
    if frontend_audio.is_dir():
        app.mount(
            "/audio",
            StaticFiles(directory=str(frontend_audio)),
            name="frontend-audio",
        )

    @app.get("/health/live", include_in_schema=False)
    def health_live():
        return JSONResponse(
            {
                "status": "live",
                "service": "cope-web",
                "version": app_version(),
            }
        )

    @app.get("/health/ready", include_in_schema=False)
    def health_ready():
        try:
            connection = connect_database(app.state.db_path)
            try:
                connection.execute("SELECT 1").fetchone()
                schema_version = database_schema_version(connection)
                from cope.web.api import _tournament_form_payload

                _tournament_form_payload(app, None, connection)
            finally:
                connection.close()
        except Exception:
            return JSONResponse(
                {"status": "not_ready"},
                status_code=503,
            )
        ready = schema_version == SCHEMA_VERSION
        return JSONResponse(
            {
                "status": "ready" if ready else "not_ready",
                "database": "ok",
                "schema_version": schema_version,
                "expected_schema_version": SCHEMA_VERSION,
            },
            status_code=200 if ready else 503,
        )

    @app.middleware("http")
    async def admin_security(request: Request, call_next):
        path = request.url.path
        if time.monotonic() - app.state.last_service_heartbeat >= 10:
            try:
                await asyncio.to_thread(_touch_web_heartbeat, app)
                app.state.last_service_heartbeat = time.monotonic()
            except sqlite3.Error:
                pass
        content_length = request.headers.get("content-length")
        if (
            not _is_large_upload_request(request)
            and content_length
            and content_length.isdigit()
            and int(content_length) > MAX_REQUEST_BODY_BYTES
        ):
            return JSONResponse({"detail": "Request body is too large."}, status_code=413)
        if request.method == "POST":
            if path in {"/admin/login", "/api/session"} and _rate_limited(
                request, "login", limit=10, window_s=300
            ):
                return JSONResponse({"detail": "Too many login attempts."}, status_code=429)
            if path.endswith("/chat") and _rate_limited(
                request, "chat", limit=12, window_s=60
            ):
                return JSONResponse({"detail": "Chat rate limit exceeded."}, status_code=429)
        private_event_page = (
            request.method == "GET"
            and await asyncio.to_thread(_private_event_page, app, path)
        )
        admin_api = path.startswith("/api/admin")
        admin_page = path.startswith("/admin") and path != "/admin/login"
        protected = admin_api or admin_page or private_event_page
        token = _admin_token(request) if protected else None

        if protected:
            if not token:
                return _security_error(
                    request,
                    f"Admin access requires {ADMIN_TOKEN_ENV}.",
                    status_code=503,
                )
            if not _request_is_secure_or_local(request):
                return _security_error(
                    request,
                    "Admin access requires HTTPS.",
                    status_code=403,
                )
            if not _admin_session_valid(request, token):
                if admin_api:
                    return JSONResponse(
                        {"detail": "Admin session required."},
                        status_code=401,
                    )
                if request.method == "GET":
                    next_path = request.url.path
                    if request.url.query:
                        next_path = f"{next_path}?{request.url.query}"
                    return RedirectResponse(
                        url="/admin/login?next=" + quote(next_path),
                        status_code=303,
                    )
                return HTMLResponse("Admin session required.", status_code=403)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                if admin_api:
                    supplied = request.headers.get("x-csrf-token", "")
                else:
                    await request.body()
                    form = await request.form()
                    supplied = str(form.get("csrf_token") or "")
                if not _csrf_token_valid(request, token, supplied):
                    return _security_error(
                        request,
                        "CSRF validation failed.",
                        status_code=403,
                    )

        if (
            request.method == "GET"
            and re.fullmatch(r"/tournaments/\d+/?", path) is not None
            and await asyncio.to_thread(_event_tournament_page, app, path)
        ):
            return RedirectResponse(url="/", status_code=307)

        if _is_spa_request(request) and FRONTEND_INDEX.is_file():
            preview_html = await asyncio.to_thread(_social_preview_html, request)
            response = (
                HTMLResponse(preview_html)
                if preview_html is not None
                else FileResponse(FRONTEND_INDEX, media_type="text/html")
            )
            response.headers["Cache-Control"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Frame-Options"] = "DENY"
            return response

        response = await call_next(request)
        if (
            protected
            and not admin_api
            and request.method == "POST"
            and 200 <= response.status_code < 400
        ):
            _publish_admin_post_streams(request)
        if path.startswith(("/assets/", "/audio/")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.get("/events/{slug}/tournaments/{tournament_id}/stream")
    @app.get("/tournaments/{tournament_id}/events")
    async def tournament_events(
        tournament_id: int,
        request: Request,
        slug: str | None = None,
    ):
        hub: StreamHub = request.app.state.stream_hub
        hub.bind_loop()
        event_ids = await asyncio.to_thread(_tournament_event_ids, request.app, tournament_id)
        for event_id in event_ids:
            hub.link_event_tournaments(
                event_id,
                await asyncio.to_thread(_event_tournament_ids, request.app, event_id),
            )
        selected_game_id = _positive_int(request.query_params.get("game_id"))
        counts_as_spectator = request.query_params.get("spectator", "1") != "0"

        if not await asyncio.to_thread(
            _public_tournament_exists,
            request.app,
            tournament_id,
            event_slug=(
                slug if request.url.path.startswith("/events/") else None
            ),
            admin_authenticated=_admin_request_authenticated(request),
        ):
            raise HTTPException(status_code=404, detail="tournament not found")

        def snapshot() -> dict[str, Any]:
            connection = connect_database(request.app.state.db_path)
            try:
                tournament = get_tournament(connection, tournament_id)
                if tournament is None or tournament.status == "draft":
                    return {"error": "tournament not found"}
                live = hub.tournament_live(tournament_id)
                return _tournament_live_payload(
                    connection,
                    tournament,
                    live,
                    selected_game_id=selected_game_id,
                )
            finally:
                connection.close()

        async def stream():
            topic = f"tournament.{tournament_id}"
            subscription = hub.subscribe(topic, spectator=counts_as_spectator)
            if counts_as_spectator:
                hub.publish_tournament_spectators(tournament_id)
            try:
                initial_snapshot = await asyncio.to_thread(snapshot)
                yield sse_stream_event(
                    hub.make_private_event(
                        topic,
                        "tournament.snapshot",
                        initial_snapshot,
                        source="web",
                    )
                )
                while True:
                    try:
                        event = await asyncio.wait_for(
                            subscription.queue.get(),
                            timeout=20,
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    if (
                        selected_game_id is not None
                        and event.type in {"game.move", "engine.info", "clock.sync"}
                        and _event_game_id(event) != selected_game_id
                    ):
                        continue
                    yield sse_stream_event(event)
            finally:
                hub.unsubscribe(subscription)
                if counts_as_spectator:
                    hub.publish_tournament_spectators(tournament_id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/tournament-spectators/events")
    async def tournament_spectator_events(request: Request):
        hub: StreamHub = request.app.state.stream_hub
        hub.bind_loop()

        def snapshot() -> dict[str, dict[str, int]]:
            connection = connect_database(request.app.state.db_path)
            try:
                counts = {
                    str(tournament.id): hub.tournament_spectator_count(tournament.id)
                    for tournament in list_tournaments(connection)
                    if tournament.status != "draft"
                }
                return {"spectator_counts": counts}
            finally:
                connection.close()

        async def stream():
            topic = "tournament-spectators"
            subscription = hub.subscribe(topic)
            try:
                initial_snapshot = await asyncio.to_thread(snapshot)
                yield sse_stream_event(
                    hub.make_private_event(
                        topic,
                        "spectators.snapshot",
                        initial_snapshot,
                        source="web",
                    )
                )
                while True:
                    try:
                        event = await asyncio.wait_for(
                            subscription.queue.get(),
                            timeout=20,
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    yield sse_stream_event(event)
            finally:
                hub.unsubscribe(subscription)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/events/{slug}/stream")
    async def public_event_stream(slug: str, request: Request):
        hub: StreamHub = request.app.state.stream_hub
        hub.bind_loop()
        event = await asyncio.to_thread(_event_by_slug, request.app, slug)
        if event is None:
            raise HTTPException(status_code=404, detail="event not found")
        if not _event_is_public(event) and not _admin_request_authenticated(request):
            raise HTTPException(status_code=401, detail="Admin session required.")
        tournament_ids = await asyncio.to_thread(_event_tournament_ids, request.app, event.id)
        hub.link_event_tournaments(event.id, tournament_ids)

        async def stream():
            topic = f"event.{event.id}"
            subscription = hub.subscribe(topic)
            hub.publish_event_spectators(event.id)
            try:
                yield sse_stream_event(
                    hub.make_private_event(
                        topic,
                        "event.snapshot",
                        {
                            "event_id": event.id,
                            "revision": event.revision,
                            "status": event.status,
                            "spectator_count": hub.event_spectator_count(event.id),
                        },
                        source="web",
                    )
                )
                while True:
                    try:
                        stream_event = await asyncio.wait_for(
                            subscription.queue.get(),
                            timeout=20,
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if stream_event is None:
                        break
                    yield sse_stream_event(stream_event)
            finally:
                hub.unsubscribe(subscription)
                hub.publish_event_spectators(event.id)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.websocket("/internal/stream")
    async def internal_stream(websocket: WebSocket):
        await websocket.accept()
        if not _internal_stream_peer_allowed(websocket):
            await websocket.close(code=4003, reason="stream peer not allowed")
            return

        hub: StreamHub = websocket.app.state.stream_hub
        hub.bind_loop()
        queue: asyncio.Queue[StreamEnvelope | None] | None = None
        try:
            hello = decode_stream_event(await websocket.receive_text())
            if hello.type != "stream.hello" or not _stream_hello_authorized(websocket, hello):
                await websocket.close(code=4003, reason="stream auth failed")
                return
            queue = hub.register_internal_client()
            await websocket.send_text(
                _stream_text(
                    make_stream_event("internal", "stream.ready", source="web")
                )
            )
            receiver = asyncio.create_task(_receive_internal_stream(websocket, hub))
            sender = asyncio.create_task(_send_internal_stream(websocket, queue))
            done, pending = await asyncio.wait(
                {receiver, sender},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
        except (StreamProtocolError, WebSocketDisconnect):
            return
        finally:
            if queue is not None:
                hub.unregister_internal_client(queue)

    @app.get("/admin/workers/events")
    async def admin_workers_events(request: Request, page: int = 1):
        hub: StreamHub = request.app.state.stream_hub
        hub.bind_loop()
        page = max(page, 1)
        per_page = 100

        def snapshot() -> dict[str, Any]:
            connection = connect_database(request.app.state.db_path)
            try:
                return _workers_snapshot_payload(
                    connection,
                    worker_server_url=_request_worker_server_url(request, connection),
                    worker_limit=per_page,
                    worker_offset=(page - 1) * per_page,
                )
            finally:
                connection.close()

        async def stream():
            subscription = hub.subscribe("workers")
            try:
                initial_snapshot = await asyncio.to_thread(snapshot)
                yield sse_stream_event(
                    hub.make_private_event(
                        "workers",
                        "workers.snapshot",
                        initial_snapshot,
                        source="web",
                    )
                )
                while True:
                    try:
                        event = await asyncio.wait_for(
                            subscription.queue.get(),
                            timeout=20,
                        )
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    yield sse_stream_event(event)
            finally:
                hub.unsubscribe(subscription)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/admin/workers/{worker_id:int}/events")
    async def admin_worker_events(worker_id: int, request: Request):
        hub: StreamHub = request.app.state.stream_hub
        hub.bind_loop()

        def snapshot() -> dict[str, Any]:
            connection = connect_database(request.app.state.db_path)
            try:
                row = _worker_admin_row(connection, worker_id)
                if row is None:
                    return {"worker_id": worker_id, "deleted": True}
                return _worker_admin_api_payload(
                    row,
                    connection=connection,
                    worker_server_url=_request_worker_server_url(request, connection),
                )
            finally:
                connection.close()

        async def stream():
            subscription = hub.subscribe("workers")
            try:
                initial_snapshot = await asyncio.to_thread(snapshot)
                yield sse_stream_event(
                    hub.make_private_event(
                        "workers",
                        "worker.snapshot",
                        initial_snapshot,
                        source="web",
                    )
                )
                while True:
                    try:
                        event = await asyncio.wait_for(subscription.queue.get(), timeout=20)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    current_snapshot = await asyncio.to_thread(snapshot)
                    yield sse_stream_event(
                        hub.make_private_event(
                            "workers",
                            "worker.snapshot",
                            current_snapshot,
                            source="web",
                        )
                    )
            finally:
                hub.unsubscribe(subscription)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    from cope.web.api import register_api_routes

    register_api_routes(app)
    return app


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _is_spa_request(request: Request) -> bool:
    if request.method != "GET":
        return False
    accept = request.headers.get("accept", "")
    if accept and "text/html" not in accept:
        return False

    path = request.url.path.rstrip("/") or "/"
    if path in {
        "/api",
        "/assets",
        "/audio",
        "/docs",
        "/internal",
        "/openapi.json",
        "/redoc",
    }:
        return False
    if path.startswith(
        ("/api/", "/assets/", "/audio/", "/docs/", "/internal/", "/redoc/")
    ):
        return False
    if path.endswith(".json"):
        return False
    if path == "/tournament-spectators/events" or re.fullmatch(
        r"/(?:tournaments/\d+|admin/workers(?:/\d+)?)/events",
        path,
    ):
        return False
    if re.fullmatch(r"/admin/workers/\d+/token", path) is not None:
        return False
    return True


def _touch_web_heartbeat(app: FastAPI) -> None:
    connection = connect_database(app.state.db_path)
    try:
        touch_service_heartbeat(connection, "web", app_version())
        connection.commit()
    finally:
        connection.close()


def _public_tournament_exists(
    app: FastAPI,
    tournament_id: int,
    *,
    event_slug: str | None = None,
    admin_authenticated: bool = False,
) -> bool:
    connection = connect_database(app.state.db_path)
    try:
        tournament = get_tournament(connection, tournament_id)
        if tournament is None or tournament.status == "draft":
            return False
        if event_slug is None:
            return not _is_event_tournament(connection, tournament_id)
        event = get_event_by_slug(connection, event_slug)
        return bool(
            event is not None
            and (_event_is_public(event) or admin_authenticated)
            and _event_has_tournament(connection, event.id, tournament_id)
        )
    finally:
        connection.close()


def _event_by_slug(app: FastAPI, slug: str) -> EventRecord | None:
    connection = connect_database(app.state.db_path)
    try:
        return get_event_by_slug(connection, slug)
    finally:
        connection.close()


def _event_is_public(event: EventRecord) -> bool:
    return event.published_at is not None and event.status != "draft"


def _admin_request_authenticated(request: Request) -> bool:
    token = _admin_token(request)
    return bool(
        token
        and _request_is_secure_or_local(request)
        and _admin_session_valid(request, token)
    )


def _private_event_page(app: FastAPI, path: str) -> bool:
    match = re.fullmatch(
        r"/events/([a-z0-9]+(?:-[a-z0-9]+)*)(?:/arena)?/?",
        path,
    )
    if match is None:
        return False
    event = _event_by_slug(app, match.group(1))
    return event is not None and not _event_is_public(event)


def _event_tournament_page(app: FastAPI, path: str) -> bool:
    match = re.fullmatch(r"/tournaments/(\d+)/?", path)
    if match is None:
        return False
    connection = connect_database(app.state.db_path)
    try:
        return _is_event_tournament(connection, int(match.group(1)))
    finally:
        connection.close()


def _event_linked_tournament_ids(connection: sqlite3.Connection) -> set[int]:
    return {
        int(row["tournament_id"])
        for row in connection.execute(
            "SELECT tournament_id FROM engine_relay_fixtures"
        )
    }


def _is_event_tournament(connection: sqlite3.Connection, tournament_id: int) -> bool:
    return connection.execute(
        "SELECT 1 FROM engine_relay_fixtures WHERE tournament_id = ?",
        (tournament_id,),
    ).fetchone() is not None


def _event_has_tournament(
    connection: sqlite3.Connection,
    event_id: int,
    tournament_id: int,
) -> bool:
    return connection.execute(
        "SELECT 1 FROM engine_relay_fixtures WHERE event_id = ? AND tournament_id = ?",
        (event_id, tournament_id),
    ).fetchone() is not None


def _event_tournament_ids(app: FastAPI, event_id: int) -> tuple[int, ...]:
    connection = connect_database(app.state.db_path)
    try:
        try:
            rows = connection.execute(
                "SELECT tournament_id FROM engine_relay_fixtures WHERE event_id = ?",
                (event_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple(int(row["tournament_id"]) for row in rows)
    finally:
        connection.close()


def _tournament_event_ids(app: FastAPI, tournament_id: int) -> tuple[int, ...]:
    connection = connect_database(app.state.db_path)
    try:
        try:
            rows = connection.execute(
                "SELECT event_id FROM engine_relay_fixtures WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple(int(row["event_id"]) for row in rows)
    finally:
        connection.close()


def _social_preview_html(request: Request) -> str | None:
    match = re.fullmatch(r"/tournaments/(\d+)/?", request.url.path)
    event_match = re.fullmatch(r"/events/([a-z0-9]+(?:-[a-z0-9]+)*)/?", request.url.path)
    if match is None and event_match is None:
        return None

    connection = None
    try:
        connection = connect_database(request.app.state.db_path)
        if event_match is not None:
            event = get_event_by_slug(connection, event_match.group(1))
            if event is None or event.published_at is None or event.status == "draft":
                return None
            title = f"{event.title} | COPE Chess"
            description = event.summary or event.subtitle or (
                "A special unrated COPE Chess exhibition."
            )
            template = FRONTEND_INDEX.read_text(encoding="utf-8")
            return _social_preview_document(
                template,
                title=title,
                description=description,
                url=str(request.url),
            )

        if match is None:
            return None
        tournament = get_tournament(connection, int(match.group(1)))
        if tournament is None or tournament.status == "draft":
            return None

        games = list_games(connection, tournament.id)
        engines = _engine_names(connection)
        game = None
        raw_game_id = request.query_params.get("game_id")
        if raw_game_id is not None:
            game_id = _positive_int(raw_game_id)
            game = next((item for item in games if item.id == game_id), None)

        format_label = tournament.config.format.value.replace("_", " ").title()
        time_control = _time_control_label(tournament.config.time_control)
        if game is not None:
            white = engines.get(game.white_engine_id, f"Engine {game.white_engine_id}")
            black = engines.get(game.black_engine_id, f"Engine {game.black_engine_id}")
            title = f"{white} vs {black} | {tournament.name} | COPE Chess"
            if game.result:
                state = game.result
                if game.termination:
                    state += f" · {game.termination.replace('_', ' ').title()}"
            elif game.status == "live":
                state = "Live now"
            elif game.status == "assigned":
                state = "Starting soon"
            elif game.status == "pending":
                state = "Scheduled"
            else:
                state = game.status.replace("_", " ").title()
            description = (
                f"Game #{game.id} · Round {game.round} · {state} · "
                f"{format_label}, {time_control}."
            )
        else:
            summary = _summarize_games(games)
            participant_count = len(tournament.config.participants)
            status = tournament.status.replace("_", " ").title()
            title = f"{tournament.name} | COPE Chess"
            description = (
                f"{format_label} tournament · {time_control} · "
                f"{participant_count} engines · {summary['finished']} of "
                f"{summary['total']} games complete · {status}."
            )

        template = FRONTEND_INDEX.read_text(encoding="utf-8")
        return _social_preview_document(
            template,
            title=title,
            description=description,
            url=str(request.url),
        )
    except Exception:
        return None
    finally:
        if connection is not None:
            connection.close()


def _social_preview_document(
    template: str,
    *,
    title: str,
    description: str,
    url: str,
) -> str:
    safe_title = escape(title, quote=True)
    safe_description = escape(description, quote=True)
    safe_url = escape(url, quote=True)
    document = re.sub(
        r"<title>.*?</title>",
        lambda _: f"<title>{safe_title}</title>",
        template,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    document = re.sub(
        r"<meta\s+name=[\"']description[\"'][^>]*>",
        lambda _: f'<meta name="description" content="{safe_description}" />',
        document,
        count=1,
        flags=re.IGNORECASE,
    )
    tags = "\n    ".join(
        (
            '<meta property="og:site_name" content="COPE Chess" />',
            '<meta property="og:type" content="website" />',
            f'<meta property="og:title" content="{safe_title}" />',
            f'<meta property="og:description" content="{safe_description}" />',
            f'<meta property="og:url" content="{safe_url}" />',
            f'<link rel="canonical" href="{safe_url}" />',
        )
    )
    return document.replace("</head>", f"    {tags}\n  </head>", 1)


def _is_large_upload_request(request: Request) -> bool:
    if request.method not in {"POST", "PUT"}:
        return False
    path = request.url.path
    return bool(re.fullmatch(r"/api/benchmarker/engine-artifacts/[0-9a-f]{64}", path)) or bool(
        re.fullmatch(r"/api/admin/engine-versions/\d+/artifact", path)
    ) or path in {"/admin/openings", "/api/admin/openings"} or bool(
        re.fullmatch(r"/(?:api/)?admin/openings/\d+", path)
    )


def _security_error(
    request: Request,
    detail: str,
    *,
    status_code: int,
) -> HTMLResponse | JSONResponse:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=status_code)
    return HTMLResponse(detail, status_code=status_code)


def _database(request: Request) -> Iterator[sqlite3.Connection]:
    # check_same_thread=False: FastAPI runs sync dependencies in a threadpool
    # while async endpoints run on the event loop, so a request's connection
    # crosses threads. Each connection is still scoped to a single request.
    connection = connect_database(request.app.state.db_path, check_same_thread=False)
    try:
        yield connection
    finally:
        connection.close()


async def _receive_internal_stream(websocket: WebSocket, hub: StreamHub) -> None:
    while True:
        event = decode_stream_event(await websocket.receive_text())
        await _dispatch_internal_stream_event(websocket.app, event)


async def _send_internal_stream(
    websocket: WebSocket,
    queue: asyncio.Queue[StreamEnvelope | None],
) -> None:
    while True:
        event = await queue.get()
        if event is None:
            await websocket.close(code=4008, reason="stream client backlog exceeded")
            return
        await websocket.send_text(_stream_text(event))


async def _dispatch_internal_stream_event(app: FastAPI, event: StreamEnvelope) -> None:
    hub: StreamHub = app.state.stream_hub
    if event.topic == "workers" or event.type.startswith("worker"):
        task = app.state.worker_snapshot_task
        if task is None or task.done():
            app.state.worker_snapshot_task = asyncio.create_task(
                _publish_worker_snapshot(app, source=event.source)
            )
        return

    tournament_id = _event_tournament_id(event)
    if tournament_id is None:
        hub.publish(event.topic, event.type, event.data, source=event.source)
        return

    topic = f"tournament.{tournament_id}"
    if event.type in {"engine.info", "clock.sync"}:
        hub.publish(topic, event.type, event.data, source=event.source)
        return

    if event.type == "tournament.live":
        hub.publish(topic, event.type, event.data, source=event.source)
        _schedule_tournament_snapshot(app, tournament_id)
        return

    if event.type == "game.move":
        hub.publish(topic, "game.move", event.data, source=event.source)
        return
    _schedule_tournament_snapshot(app, tournament_id)


async def _publish_worker_snapshot(app: FastAPI, *, source: str) -> None:
    await asyncio.sleep(1.0)
    hub: StreamHub = app.state.stream_hub
    hub.publish("workers", "workers.changed", {}, source=source)


def _schedule_tournament_snapshot(app: FastAPI, tournament_id: int) -> None:
    tasks: dict[int, asyncio.Task] = app.state.tournament_snapshot_tasks
    task = tasks.get(tournament_id)
    if task is None or task.done():
        tasks[tournament_id] = asyncio.create_task(
            _publish_tournament_snapshot(app, tournament_id)
        )


async def _publish_tournament_snapshot(app: FastAPI, tournament_id: int) -> None:
    await asyncio.sleep(0.5)
    payload = await asyncio.to_thread(
        _tournament_snapshot_for_broadcast,
        app,
        tournament_id,
    )
    hub: StreamHub = app.state.stream_hub
    topic = f"tournament.{tournament_id}"
    if payload is None:
        # A large snapshot is expensive to serialize once per subscriber and can
        # starve unrelated requests. Send a tiny invalidation event; clients
        # already coalesce these and refresh through the normal HTTP endpoint.
        hub.publish(
            topic,
            "tournament.changed",
            {"tournament_id": tournament_id},
            source="web",
        )
        return
    hub.publish(topic, "tournament.snapshot", payload, source="web")


def _tournament_snapshot_for_broadcast(
    app: FastAPI,
    tournament_id: int,
) -> dict[str, Any] | None:
    connection = connect_database(app.state.db_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM games WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()
        if row is not None and int(row["count"]) > MAX_BROADCAST_SNAPSHOT_GAMES:
            return None
    finally:
        connection.close()
    return _tournament_snapshot(app, tournament_id)


def _tournament_snapshot(app: FastAPI, tournament_id: int) -> dict[str, Any]:
    hub: StreamHub = app.state.stream_hub
    connection = connect_database(app.state.db_path)
    try:
        tournament = get_tournament(connection, tournament_id)
        if tournament is None:
            return {"error": "tournament not found"}
        payload = _tournament_live_payload(
            connection,
            tournament,
            hub.tournament_live(tournament_id),
        )
        hub.prune_tournament_live(
            tournament_id,
            {
                game["id"]
                for game in payload["active_games"]
                if game["status"] in {"assigned", "live"}
            },
        )
        return payload
    finally:
        connection.close()


def _stream_text(event: StreamEnvelope) -> str:
    return encode_stream_event(event)


def _event_tournament_id(event: StreamEnvelope) -> int | None:
    value = event.data.get("tournament_id")
    if value is None and event.topic.startswith("tournament."):
        value = event.topic.removeprefix("tournament.")
    return _positive_int(value)


def _event_game_id(event: StreamEnvelope) -> int | None:
    value = event.data.get("game_id")
    if value is None:
        live = event.data.get("live")
        if isinstance(live, dict):
            value = live.get("game_id")
    return _positive_int(value)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _workers_snapshot_payload(
    connection: sqlite3.Connection,
    *,
    worker_server_url: str | None = None,
    worker_limit: int | None = None,
    worker_offset: int = 0,
) -> dict[str, Any]:
    workers = list(list_workers(connection))
    benchmarkers = list(list_benchmarkers(connection))
    visible_workers = workers[worker_offset:]
    if worker_limit is not None:
        visible_workers = visible_workers[:worker_limit]
    visible_rows = _worker_admin_rows(connection, workers=visible_workers)
    summary_rows = [
        {"worker": worker, "status": _worker_effective_status(worker)}
        for worker in workers
    ]
    return {
        "workers": [
            _worker_admin_payload(row)
            for row in visible_rows
        ],
        "total_workers": len(workers),
        "connected_workers": sum(
            row["status"] in CONNECTED_WORKER_STATUSES for row in summary_rows
        ),
        "benchmarkers": [
            _benchmarker_admin_payload(benchmarker)
            for benchmarker in benchmarkers
        ],
        "total_benchmarkers": len(benchmarkers),
        "connected_benchmarkers": sum(
            benchmarker.status in {"connected", "busy"}
            for benchmarker in benchmarkers
        ),
        "machines": _worker_machine_payloads(summary_rows),
    }


def _publish_admin_post_streams(request: Request) -> None:
    hub: StreamHub = request.app.state.stream_hub
    path = request.url.path
    hub.publish_to_internal("runner.wake", {"reason": path})

    if (
        path.startswith("/admin/workers")
        or path.startswith("/api/admin/workers")
        or path.startswith("/api/admin/benchmarkers")
    ):
        hub.publish("workers", "workers.changed", {}, source="web")
    tournament_id = _admin_tournament_path_id(path)
    if tournament_id is not None:
        # Never build a potentially multi-thousand-game snapshot in the request
        # handler. Subscribers can refresh immediately, while the normal stream
        # coalescer builds at most one snapshot in a worker thread.
        hub.publish(
            f"tournament.{tournament_id}",
            "tournament.changed",
            {"tournament_id": tournament_id},
            source="web",
        )


def _admin_tournament_path_id(path: str) -> int | None:
    parts = path.strip("/").split("/")
    try:
        tournaments_index = parts.index("tournaments")
    except ValueError:
        return None
    if tournaments_index == 0 or parts[tournaments_index - 1] != "admin":
        return None
    if len(parts) <= tournaments_index + 1:
        return None
    try:
        value = int(parts[tournaments_index + 1])
    except ValueError:
        return None
    return value if value > 0 else None


def _admin_token(request: Request) -> str | None:
    return getattr(request.app.state, "admin_token", None) or None


def _admin_session_valid(request: Request, token: str) -> bool:
    value = request.cookies.get("cope_admin_session", "")
    if not value:
        return False
    nonce = _signed_value_nonce(token, value)
    return nonce is not None


def _csrf_token(request: Request, token: str | None) -> str:
    if token is None:
        return ""
    nonce = _signed_value_nonce(token, request.cookies.get("cope_admin_session", ""))
    if nonce is None:
        return ""
    return _csrf_for_nonce(token, nonce)


def _csrf_token_valid(request: Request, token: str, supplied: str) -> bool:
    expected = _csrf_token(request, token)
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def _signed_value(token: str, nonce: str, *, issued_at: int | None = None) -> str:
    timestamp = issued_at if issued_at is not None else int(datetime.now(UTC).timestamp())
    payload = f"{timestamp}.{nonce}"
    signature = hmac.digest(
        token.encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hex()
    return f"{payload}.{signature}"


def _signed_value_nonce(token: str, value: str) -> str | None:
    parts = value.split(".")
    if len(parts) != 3:
        return None
    timestamp_text, nonce, supplied = parts
    if not timestamp_text or not nonce or not supplied:
        return None
    try:
        issued_at = int(timestamp_text)
    except ValueError:
        return None
    payload = f"{timestamp_text}.{nonce}"
    expected = hmac.digest(
        token.encode("utf-8"),
        payload.encode("utf-8"),
        "sha256",
    ).hex()
    if not hmac.compare_digest(supplied, expected):
        return None
    now = int(datetime.now(UTC).timestamp())
    if issued_at > now or now - issued_at >= ADMIN_SESSION_MAX_AGE_SECONDS:
        return None
    return nonce


def _csrf_for_nonce(token: str, nonce: str) -> str:
    return hmac.digest(
        token.encode("utf-8"),
        f"csrf:{nonce}".encode("utf-8"),
        "sha256",
    ).hex()


def _request_is_secure(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    peer_is_private = False
    if request.client is not None:
        try:
            peer_is_private = ipaddress.ip_address(request.client.host).is_private
        except ValueError:
            peer_is_private = False
    return request.url.scheme == "https" or (
        peer_is_private and forwarded.split(",", 1)[0].strip() == "https"
    )


def _rate_limited(
    request: Request,
    bucket: str,
    *,
    limit: int,
    window_s: float,
) -> bool:
    peer = request.client.host if request.client is not None else "unknown"
    key = (bucket, peer)
    now = time.monotonic()
    attempts = request.app.state.request_limits.setdefault(key, [])
    attempts[:] = [attempt for attempt in attempts if now - attempt < window_s]
    if len(attempts) >= limit:
        return True
    attempts.append(now)
    return False


def _request_is_secure_or_local(request: Request) -> bool:
    if _request_is_secure(request):
        return True
    if request.client is None:
        return True
    return request.client.host in LOCAL_EVENT_PUBLISHERS


def _internal_stream_peer_allowed(websocket: WebSocket) -> bool:
    if getattr(websocket.app.state, "event_token", None):
        return True
    return websocket.client is None or websocket.client.host in LOCAL_EVENT_PUBLISHERS


def _stream_hello_authorized(websocket: WebSocket, hello: StreamEnvelope) -> bool:
    expected = getattr(websocket.app.state, "event_token", None)
    if not expected:
        return websocket.client is None or websocket.client.host in LOCAL_EVENT_PUBLISHERS
    supplied = str(hello.data.get("token") or "")
    return bool(supplied and hmac.compare_digest(supplied, expected))


def _worker_admin_rows(
    connection: sqlite3.Connection,
    *,
    limit: int | None = None,
    workers: list[Any] | None = None,
) -> list[dict[str, Any]]:
    engines = _engine_names(connection)
    rows: list[dict[str, Any]] = []
    source = workers if workers is not None else list_workers(connection)
    if limit is not None:
        source = source[:limit]
    activities = list_worker_activities(
        connection,
        worker_ids=(worker.id for worker in source),
    )
    tool_jobs = {
        job.worker_id: job
        for job in list_tool_jobs(connection, limit=200)
        if job.status == "running" and job.worker_id is not None
    }
    for worker in source:
        try:
            rows.append(
                _worker_admin_view(
                    worker,
                    engines,
                    activity=activities.get(worker.id),
                    tool_job=tool_jobs.get(worker.id),
                )
            )
        except (TypeError, ValueError, ValidationError, sqlite3.Error):
            continue
    return rows


def _worker_admin_row(connection: sqlite3.Connection, worker_id: int) -> dict[str, Any] | None:
    worker = get_worker(connection, worker_id)
    if worker is None:
        return None
    try:
        row = _worker_admin_view(
            worker,
            _engine_names(connection),
            activity=get_worker_activity(connection, worker.id),
            tool_job=next(
                (
                    job
                    for job in list_tool_jobs(connection, limit=200)
                    if job.status == "running" and job.worker_id == worker.id
                ),
                None,
            ),
        )
        row["failures"] = list_worker_failures(connection, worker.id, limit=20)
        return row
    except (TypeError, ValueError, ValidationError, sqlite3.Error):
        return None


def _worker_admin_view(
    worker,
    engines: dict[int, str],
    *,
    activity,
    tool_job=None,
) -> dict[str, Any]:
    effective_status = _worker_effective_status(worker)
    activity_view = _worker_activity_view(activity, engines)
    return {
        "worker": worker,
        "status": effective_status,
        "token": _worker_token_view(worker),
        "session": _worker_session_view(worker),
        "machine": _worker_machine_view(worker, effective_status),
        "work": activity_view or _worker_tool_activity(tool_job) or _worker_idle_activity(worker, effective_status),
    }


def _worker_admin_payload(row: dict[str, Any]) -> dict[str, Any]:
    worker = row["worker"]
    hardware = {
        "reported": False,
        "summary": "Not reported",
        "detail": "",
        "cores": "-",
        "memory": "-",
    }
    if worker.hw is not None:
        effective_cores = worker.capacity.threads if worker.capacity is not None else 0
        limit_detail = (
            f" · limited to {effective_cores} engine-thread slots"
            if effective_cores < worker.hw.logical_cores
            else ""
        )
        hardware = {
            "reported": True,
            "summary": _worker_resource_summary(effective_cores),
            "detail": (
                f"{worker.hw.logical_cores} accessible CPU threads / "
                f"{worker.hw.physical_cores} physical cores / {worker.hw.ram_gb}GB RAM"
                f"{limit_detail}"
            ),
            "cores": str(effective_cores),
            "memory": f"{worker.hw.ram_gb}GB",
        }
    return {
        "id": worker.id,
        "label": worker.label,
        "status": row["status"],
        "last_seen": worker.last_seen,
        "work": row["work"],
        "machine": row["machine"],
        "hardware": hardware,
    }


def _benchmarker_admin_payload(benchmarker) -> dict[str, Any]:
    hardware = {
        "reported": False,
        "summary": "Not reported",
        "detail": "",
    }
    if benchmarker.hw is not None:
        hardware = {
            "reported": True,
            "summary": benchmarker.hw.cpu_model,
            "detail": (
                f"{benchmarker.hw.physical_cores} physical / "
                f"{benchmarker.hw.logical_cores} logical cores · "
                f"{benchmarker.hw.ram_gb}GB RAM"
            ),
        }
    return {
        "id": benchmarker.id,
        "label": benchmarker.label,
        "status": benchmarker.status,
        "last_seen": benchmarker.last_seen,
        "machine_id": benchmarker.machine_id,
        "app_commit": benchmarker.app_commit,
        "hardware": hardware,
    }


def _worker_resource_summary(threads: int) -> str:
    return f"{threads} core{'s' if threads != 1 else ''}"


def _worker_machine_payloads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    machines: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        worker = row["worker"]
        if worker.machine_id:
            machines.setdefault(worker.machine_id, []).append(row)

    payloads: list[dict[str, Any]] = []
    for machine_id, machine_rows in machines.items():
        representative = next(
            (row["worker"] for row in machine_rows if row["worker"].hw is not None),
            machine_rows[0]["worker"],
        )
        hardware = representative.hw
        active_workers = sum(
            row["status"] in CONNECTED_WORKER_STATUSES for row in machine_rows
        )
        payloads.append(
            {
                "id": machine_id,
                "label": machine_id[:12],
                "worker_count": len(machine_rows),
                "active_worker_count": active_workers,
                "hardware": _machine_hardware_payload(hardware),
            }
        )
    return sorted(payloads, key=lambda machine: machine["label"])


def _machine_hardware_payload(hardware) -> dict[str, Any]:
    if hardware is None:
        return {"reported": False, "summary": "Not reported", "detail": ""}
    return {
        "reported": True,
        "summary": hardware.cpu_model,
        "detail": (
            f"{hardware.physical_cores} physical / {hardware.logical_cores} logical cores · "
            f"{hardware.ram_gb}GB RAM"
        ),
        "gpu": hardware.gpu,
        "os": hardware.os,
    }


def _worker_record_payload(worker) -> dict[str, Any]:
    hardware = None
    if worker.hw is not None:
        hardware = {
            "cpu_model": worker.hw.cpu_model,
            "physical_cores": worker.hw.physical_cores,
            "logical_cores": worker.hw.logical_cores,
            "ram_gb": worker.hw.ram_gb,
            "ram_mb": worker.hw.ram_mb,
            "gpu": worker.hw.gpu,
            "os": worker.hw.os,
            "python": worker.hw.python,
            "bench": {
                "nps_probe": worker.hw.bench.nps_probe,
            },
        }
    return {
        "id": worker.id,
        "label": worker.label,
        "token_expires_at": worker.token_expires_at,
        "status": worker.status,
        "session_id": worker.session_id,
        "app_version": worker.app_commit,
        "protocol_version": worker.protocol_version,
        "machine_id": worker.machine_id,
        "hw": hardware,
        "core_limit": worker.core_limit,
        "tournament_scope": worker.tournament_scope,
        "last_seen": worker.last_seen,
    }


def _worker_resource_sample_payload(sample) -> dict[str, Any]:
    return {
        "sampled_at": sample.sampled_at,
        "cpu_percent": sample.cpu_percent,
        "memory_used_mb": sample.memory_used_mb,
        "memory_total_mb": sample.memory_total_mb,
        "memory_available_mb": sample.memory_available_mb,
        "coordinator_cpu_cores": sample.coordinator_cpu_cores,
        "coordinator_memory_mb": sample.coordinator_memory_mb,
        "engine_cpu_cores": sample.engine_cpu_cores,
        "engine_memory_mb": sample.engine_memory_mb,
        "disk_used_mb": sample.disk_used_mb,
        "disk_free_mb": sample.disk_free_mb,
        "disk_total_mb": sample.disk_total_mb,
    }


def _worker_allocations_payload(
    connection: sqlite3.Connection,
    worker_id: int,
) -> list[dict[str, Any]]:
    from cope.events.engine_relay import relay_resources_for_tournament

    rows = connection.execute(
        """
        SELECT
          assignment.id AS assignment_id,
          assignment.status,
          game.id AS game_id,
          game.white_engine_id,
          game.black_engine_id,
          tournament.id AS tournament_id,
          tournament.name AS tournament_name
        FROM game_assignments assignment
        JOIN games game ON game.id = assignment.game_id
        JOIN tournaments tournament ON tournament.id = game.tournament_id
        WHERE assignment.worker_id = ?
          AND assignment.status IN ('assigned', 'acked', 'live')
        ORDER BY assignment.sent_at, assignment.id
        """,
        (worker_id,),
    ).fetchall()
    engine_names = _engine_names(connection)
    tournaments: dict[int, TournamentRecord | None] = {}
    allocations = []
    for row in rows:
        tournament_id = int(row["tournament_id"])
        if tournament_id not in tournaments:
            tournaments[tournament_id] = get_tournament(connection, tournament_id)
        tournament = tournaments[tournament_id]
        if tournament is None:
            continue
        relay_resources = relay_resources_for_tournament(
            connection,
            tournament_id,
            (int(row["white_engine_id"]), int(row["black_engine_id"])),
        )
        if relay_resources:
            threads = max(threads for threads, _ in relay_resources)
            engine_hash_mb = sum(hash_mb for _, hash_mb in relay_resources)
            process_memory_mb = ENGINE_PROCESS_MEMORY_OVERHEAD_MB * len(relay_resources)
        else:
            threads = tournament.config.engine_threads
            engine_hash_mb = tournament.config.engine_hash_mb * 2
            process_memory_mb = ENGINE_PROCESS_MEMORY_OVERHEAD_MB * 2
        allocations.append(
            {
                "assignment_id": int(row["assignment_id"]),
                "game_id": int(row["game_id"]),
                "status": row["status"],
                "tournament_id": tournament_id,
                "tournament_name": row["tournament_name"],
                "white_engine": engine_names.get(
                    int(row["white_engine_id"]),
                    f"Engine {row['white_engine_id']}",
                ),
                "black_engine": engine_names.get(
                    int(row["black_engine_id"]),
                    f"Engine {row['black_engine_id']}",
                ),
                "threads": threads,
                "engine_hash_mb": engine_hash_mb,
                "process_memory_mb": process_memory_mb,
                "memory_mb": engine_hash_mb + process_memory_mb,
            }
        )
    return allocations


def _worker_admin_api_payload(
    row: dict[str, Any],
    *,
    connection: sqlite3.Connection,
    worker_server_url: str | None = None,
) -> dict[str, Any]:
    worker = row["worker"]
    tournaments = [
        {
            "id": tournament.id,
            "name": tournament.name,
            "status": tournament.status,
        }
        for tournament in list_tournaments(connection)
        if tournament.status not in {"finished", "aborted"}
    ]
    available_tournament_ids = {tournament["id"] for tournament in tournaments}
    tournament_ids = [
        tournament_id
        for tournament_id in list_worker_tournament_ids(connection, worker.id)
        if tournament_id in available_tournament_ids
    ]
    events = [
        {
            "id": event.id,
            "title": event.title,
            "status": event.status,
            "scheduled_start_at": event.scheduled_start_at,
        }
        for event in list_events(connection)
        if event.status not in {"completed", "cancelled"}
    ]
    available_event_ids = {event["id"] for event in events}
    event_ids = [
        event_id
        for event_id in list_worker_event_ids(connection, worker.id)
        if event_id in available_event_ids
    ]
    event_claim = connection.execute(
        """
        SELECT claim.event_id, event.title AS event_title,
               claim.tournament_id, tournament.name AS fixture_title,
               tournament.status, tournament.scheduled_start_at,
               assignment.id AS assignment_id,
               assignment.status AS assignment_status
        FROM event_fixture_workers claim
        JOIN events event ON event.id = claim.event_id
        JOIN tournaments tournament ON tournament.id = claim.tournament_id
        LEFT JOIN games game
          ON game.tournament_id = tournament.id
         AND game.status IN ('assigned', 'live')
        LEFT JOIN game_assignments assignment
          ON assignment.game_id = game.id
         AND assignment.worker_id = claim.worker_id
         AND assignment.status IN ('assigned', 'acked', 'live')
        WHERE claim.worker_id = ?
          AND tournament.status IN ('scheduled', 'running', 'paused')
        ORDER BY assignment.id NULLS LAST
        LIMIT 1
        """,
        (worker.id,),
    ).fetchone()
    resource_samples = list_worker_resource_samples(connection, worker.id, limit=120)
    return {
        "row": {
            "worker": _worker_record_payload(worker),
            "status": row["status"],
            "token": row["token"],
            "session": row["session"],
            "machine": row["machine"],
            "work": row["work"],
        },
        "worker": _worker_record_payload(worker),
        "settings": {
            "core_limit": worker.core_limit,
            "effective_cores": (
                worker.capacity.threads if worker.capacity is not None else None
            ),
            "effective_memory_mb": (
                worker.capacity.hash_mb if worker.capacity is not None else None
            ),
            "tournament_scope": worker.tournament_scope,
            "tournament_ids": tournament_ids,
            "tournaments": tournaments,
            "event_ids": event_ids,
            "events": events,
            "event_claim": None if event_claim is None else dict(event_claim),
        },
        "resources": {
            "latest": (
                _worker_resource_sample_payload(resource_samples[-1])
                if resource_samples
                else None
            ),
            "samples": [
                _worker_resource_sample_payload(sample)
                for sample in resource_samples
            ],
            "allocations": _worker_allocations_payload(connection, worker.id),
        },
        "worker_launch_command": _worker_launch_command(worker, worker_server_url)
        if worker_server_url is not None
        else None,
        "failures": [
            {
                "id": failure.id,
                "worker_id": failure.worker_id,
                "worker_label": failure.worker_label,
                "machine_id": failure.machine_id,
                "assignment_id": failure.assignment_id,
                "game_id": failure.game_id,
                "engine_id": failure.engine_id,
                "engine_name": failure.engine_name,
                "stage": failure.stage,
                "error": failure.error,
                "occurred_at": failure.occurred_at,
            }
            for failure in row.get("failures", ())
        ],
    }


def _state_view(status: str, label: str, detail: str) -> dict[str, str]:
    return {"status": status, "label": label, "detail": detail}


def _worker_token_view(worker) -> dict[str, str]:
    if worker.token_expires_at is None:
        if worker.status == "revoked":
            return _state_view("revoked", "Revoked", "Token removed")
        if worker.status == "minted":
            return _state_view("pending", "Not generated", "Generate a token to register")
        return _state_view("consumed", "Consumed", "Registration complete")

    expires_at = _parse_utc_datetime(worker.token_expires_at)
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return _state_view("expired", "Expired", f"Expired {worker.token_expires_at}")

    return _state_view("minted", "Minted", f"Expires {worker.token_expires_at}")


def _worker_session_view(worker) -> dict[str, str]:
    if worker.session_id:
        return _state_view("active", "Issued", _short_secret(worker.session_id))
    if worker.status == "minted":
        return _state_view("pending", "None", "Waiting for token use")
    return _state_view("inactive", "None", "No reconnect session")


def _worker_launch_command(worker, worker_server_url: str) -> str | None:
    if worker.session_id:
        command = (
            f"cope worker --server-url {_command_arg(worker_server_url)} "
            f"--session-id {_command_arg(worker.session_id)}"
        )
        if worker.machine_id:
            command = f"{command} --machine-id {_command_arg(worker.machine_id)}"
        return command

    return None


def _command_arg(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _request_worker_server_url(
    request: Request,
    connection: sqlite3.Connection,
) -> str:
    configured = getattr(request.app.state, "worker_server_url", None)
    if configured:
        return _publicize_configured_worker_url(str(configured), request)

    endpoint = get_service_endpoint(connection, "worker-server")
    port = endpoint.port if endpoint is not None else default_worker_port()
    path = endpoint.path if endpoint is not None else DEFAULT_WORKER_PATH
    scheme = "wss" if _request_is_secure(request) else "ws"
    host = request.url.hostname or "localhost"
    return urlunsplit((scheme, _url_authority(host, port), path, "", ""))


def _request_benchmarker_server_url(
    request: Request,
    connection: sqlite3.Connection,
) -> str:
    configured = getattr(request.app.state, "benchmarker_server_url", None)
    if configured:
        return _publicize_configured_websocket_url(
            str(configured),
            request,
            default_path=DEFAULT_BENCHMARKER_PATH,
        )

    endpoint = get_service_endpoint(connection, "benchmark-server")
    port = endpoint.port if endpoint is not None else default_benchmark_server_port()
    path = endpoint.path if endpoint is not None else DEFAULT_BENCHMARKER_PATH
    scheme = "wss" if _request_is_secure(request) else "ws"
    host = request.url.hostname or "localhost"
    return urlunsplit((scheme, _url_authority(host, port), path, "", ""))


def _publicize_configured_worker_url(url: str, request: Request) -> str:
    return _publicize_configured_websocket_url(
        url,
        request,
        default_path=DEFAULT_WORKER_PATH,
    )


def _publicize_configured_websocket_url(
    url: str,
    request: Request,
    *,
    default_path: str,
) -> str:
    parsed = urlsplit(url)
    configured_host = (parsed.hostname or "").lower()
    if configured_host not in WILDCARD_HOSTS | {"127.0.0.1", "::1", "localhost"}:
        return url
    host = request.url.hostname or parsed.hostname or "localhost"
    port = parsed.port
    authority = _url_authority(host, port) if port is not None else _url_host_only(host)
    scheme = "wss" if _request_is_secure(request) and parsed.scheme == "ws" else parsed.scheme
    return urlunsplit((scheme, authority, parsed.path or default_path, "", ""))


def _url_authority(host: str, port: int) -> str:
    return f"{_url_host_only(host)}:{port}"


def _url_host_only(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _worker_effective_status(worker) -> str:
    if worker.status in CONNECTED_WORKER_STATUSES and not _worker_seen_recently(worker):
        return "stale"
    return worker.status


def _worker_seen_recently(worker) -> bool:
    if worker.last_seen is None:
        return False
    last_seen = _parse_utc_datetime(worker.last_seen)
    if last_seen is None:
        return False
    age = datetime.now(UTC) - last_seen
    return 0 <= age.total_seconds() <= WORKER_RECENT_SECONDS


def _worker_machine_view(worker, effective_status: str) -> dict[str, str]:
    seen_detail = f"Last worker event {worker.last_seen or 'unknown'}"
    if effective_status in CONNECTED_WORKER_STATUSES:
        machine = worker.machine_id[:12] if worker.machine_id else "unknown"
        return _state_view(effective_status, machine, seen_detail)
    states = {
        "stale": ("No active connection", seen_detail),
        "offline": ("Offline", f"Disconnected {worker.last_seen or 'unknown'}"),
        "minted": ("Not registered", "No machine yet"),
        "revoked": ("Revoked", "Cannot reconnect"),
    }
    label, detail = states.get(effective_status, (effective_status.title(), worker.last_seen or ""))
    return _state_view(effective_status, label, detail)


def _worker_activity_view(
    activity,
    engines: dict[int, str],
) -> dict[str, Any] | None:
    if activity is None:
        return None

    status = activity.assignment_status
    verb = activity.progress_stage or ("Playing" if status == "live" else "Assigned")
    white = engines.get(activity.white_engine_id, f"Engine {activity.white_engine_id}")
    black = engines.get(activity.black_engine_id, f"Engine {activity.black_engine_id}")
    active_count = activity.active_assignment_count
    return _activity_view(
        status,
        verb,
        activity.progress_detail or (
            f"{active_count} games active"
            if active_count > 1
            else f"Game #{activity.game_id} in round {activity.round}"
        ),
        f"{activity.tournament_name}: {white} vs {black}",
        href=f"/admin/tournaments/{activity.tournament_id}",
        meta=(
            f"Latest game #{activity.game_id} · {activity.plies} plies recorded"
            if active_count > 1
            else f"{activity.plies} plies recorded"
        ),
    )


def _worker_tool_activity(job) -> dict[str, Any] | None:
    if job is None:
        return None
    option_name = str(job.input.get("option_name") or "UCI option")
    return _activity_view(
        "busy",
        "Running tool",
        f"Inspecting {job.completed_items} of {job.total_items} engines",
        f"Who Has This: {option_name}",
        href=f"/admin/tools/who-has-this?job={job.id}",
        meta=f"Attempt {job.attempt}",
    )


def _worker_idle_activity(worker, effective_status: str) -> dict[str, Any]:
    if effective_status == "minted" and worker.token_expires_at is None:
        return _activity_view(
            "pending",
            "Needs token",
            "Awaiting token generation",
            "Generate a one-time token before starting the worker process.",
        )

    states = {
        "minted": ("pending", "Awaiting registration", "Token has not been used", "No worker process has connected with this token.", False),
        "ready": ("ready", "Idle", "Waiting for an eligible game", "The worker server is waiting for stream wake events or the next fallback scan.", False),
        "connected": ("connected", "Connected", "Preparing to accept work", "The machine is connected but has not started a game.", False),
        "downloading": ("downloading", "Downloading", "Preparing engine binary", "The machine is securely downloading and verifying an engine version.", False),
        "stale": ("stale", "Stale", "No active machine connection", "The worker has not reported a recent connection event.", True),
        "busy": ("busy", "Busy", "Marked busy with no active assignment", "This can indicate a stale worker state after an interruption.", True),
        "offline": ("offline", "Offline", "Worker process is not connected", "The reconnect session remains issued unless the worker is revoked.", bool(worker.session_id)),
        "revoked": ("revoked", "Revoked", "Worker cannot reconnect", "Token and session credentials have been removed.", False),
    }
    if effective_status in states:
        status, label, summary, detail, abnormal = states[effective_status]
        return _activity_view(status, label, summary, detail, abnormal=abnormal)
    return _activity_view(
        effective_status,
        effective_status.title(),
        "No active assignment",
        "",
    )


def _activity_view(
    status: str,
    label: str,
    summary: str,
    detail: str,
    *,
    href: str = "",
    meta: str = "",
    abnormal: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "label": label,
        "summary": summary,
        "detail": detail,
        "meta": meta,
        "href": href,
        "abnormal": abnormal,
    }


def _parse_utc_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _short_secret(value: str) -> str:
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def _friendly_error(exc: Exception) -> str:
    message = str(exc)
    if "UNIQUE constraint failed" in message:
        return "That name is already in use."
    return message


def _create_chat_message_from_form(
    connection: sqlite3.Connection,
    form: dict[str, list[str]],
    *,
    tournament_id: int,
) -> dict[str, Any] | None:
    settings = get_chat_settings(connection)
    if not settings.enabled:
        raise HTTPException(status_code=403, detail="Chat is disabled.")

    display_name = form_value(form, "display_name")[:40].strip()
    if not display_name:
        if settings.allow_anonymous_names:
            display_name = "Anonymous"
        else:
            raise HTTPException(status_code=422, detail="A display name is required.")
    if display_name.casefold() == "system":
        raise HTTPException(status_code=422, detail="System is a reserved display name.")
    text = form_value(form, "text").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Enter a message.")
    if len(text) > settings.max_message_length:
        raise HTTPException(
            status_code=422,
            detail=f"Messages can be at most {settings.max_message_length} characters.",
        )

    try:
        parsed_command = parse_chat_command(text)
    except ChatCommandError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if parsed_command is not None:
        try:
            result = DEFAULT_COMMAND_REGISTRY.dispatch(
                ChatCommandContext(
                    connection=connection,
                    tournament_id=tournament_id,
                    display_name=display_name,
                ),
                text,
            )
        except ChatCommandError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if result.broadcast_text is None:
            return None
        message_id = create_chat_message(
            connection,
            tournament_id=tournament_id,
            display_name="System",
            text=result.broadcast_text,
        )
        message = get_chat_message(connection, message_id)
        connection.commit()
        return None if message is None else _chat_message_payload(message)

    message_id = create_chat_message(
        connection,
        tournament_id=tournament_id,
        display_name=display_name,
        text=text,
    )
    message = get_chat_message(connection, message_id)
    connection.commit()
    if message is None:
        raise RuntimeError("chat message disappeared after creation")
    return _chat_message_payload(message)


def _create_event_chat_message_from_form(
    connection: sqlite3.Connection,
    form: dict[str, list[str]],
    *,
    event_id: int,
    settings: EventChatSettingsRecord,
) -> dict[str, Any]:
    if not settings.enabled:
        raise HTTPException(status_code=403, detail="Chat is disabled.")

    display_name = form_value(form, "display_name")[:40].strip()
    if not display_name:
        if settings.allow_anonymous_names:
            display_name = "Anonymous"
        else:
            raise HTTPException(status_code=422, detail="A display name is required.")
    if display_name.casefold() == "system":
        raise HTTPException(status_code=422, detail="System is a reserved display name.")
    text = form_value(form, "text").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Enter a message.")
    if len(text) > settings.max_message_length:
        raise HTTPException(
            status_code=422,
            detail=f"Messages can be at most {settings.max_message_length} characters.",
        )
    if settings.slowmode_seconds > 0:
        previous = connection.execute(
            """
            SELECT at FROM chat_messages
            WHERE event_id = ? AND LOWER(display_name) = LOWER(?)
            ORDER BY id DESC LIMIT 1
            """,
            (event_id, display_name),
        ).fetchone()
        previous_at = None if previous is None else _parse_utc_datetime(previous["at"])
        if previous_at is not None:
            elapsed = (datetime.now(UTC) - previous_at).total_seconds()
            remaining = math.ceil(settings.slowmode_seconds - elapsed)
            if remaining > 0:
                raise HTTPException(
                    status_code=429,
                    detail=f"Slow mode is active. Try again in {remaining} seconds.",
                )

    message_id = create_chat_message(
        connection,
        event_id=event_id,
        display_name=display_name,
        text=text,
    )
    message = get_chat_message(connection, message_id)
    connection.commit()
    if message is None:
        raise RuntimeError("chat message disappeared after creation")
    return _chat_message_payload(message)


def _chat_message_payload(message: ChatMessageRecord) -> dict[str, Any]:
    return {
        "id": message.id,
        "tournament_id": message.tournament_id,
        "event_id": message.event_id,
        "display_name": message.display_name,
        "text": message.text,
        "at": message.at,
    }


def _require_public_chat_tournament(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> TournamentRecord:
    tournament = get_tournament(connection, tournament_id)
    if tournament is None or tournament.status == "draft":
        raise HTTPException(status_code=404, detail="Tournament not found.")
    return tournament


def _publish_chat_message(
    request: Request,
    tournament_id: int,
    message: dict[str, Any],
) -> None:
    request.app.state.stream_hub.publish(
        f"tournament.{tournament_id}",
        "chat.message",
        {"tournament_id": tournament_id, "message": message},
        source="web",
    )


def _publish_event_chat_message(
    request: Request,
    event_id: int,
    message: dict[str, Any],
) -> None:
    request.app.state.stream_hub.publish(
        f"event.{event_id}",
        "chat.message",
        {"event_id": event_id, "message": message},
        source="web",
    )


def _event_chat_settings_payload(settings: Any) -> dict[str, Any]:
    return {
        "enabled": settings.enabled,
        "slowmode_seconds": settings.slowmode_seconds,
        "max_message_length": settings.max_message_length,
        "allow_anonymous_names": settings.allow_anonymous_names,
        "retention_days": settings.retention_days,
    }


def _publish_chat_settings_change(
    request: Request,
    connection: sqlite3.Connection,
    settings: ChatSettingsRecord,
) -> None:
    payload = _event_chat_settings_payload(settings)
    for tournament in list_tournaments(connection):
        if tournament.status == "draft":
            continue
        request.app.state.stream_hub.publish(
            f"tournament.{tournament.id}",
            "chat.settings",
            {"tournament_id": tournament.id, "settings": payload},
            source="web",
        )
    for event in list_events(connection, public_only=True):
        event_settings = get_event_chat_settings(
            connection,
            event.id,
            defaults=settings,
        )
        request.app.state.stream_hub.publish(
            f"event.{event.id}",
            "chat.settings",
            {
                "event_id": event.id,
                "settings": _event_chat_settings_payload(event_settings),
            },
            source="web",
        )


def _publish_chat_deletion(
    request: Request,
    *,
    tournament_id: int | None,
    event_id: int | None,
    message_id: int,
) -> None:
    if tournament_id is not None:
        request.app.state.stream_hub.publish(
            f"tournament.{tournament_id}",
            "chat.deleted",
            {"tournament_id": tournament_id, "message_id": message_id},
            source="web",
        )
    if event_id is not None:
        request.app.state.stream_hub.publish(
            f"event.{event_id}",
            "chat.deleted",
            {"event_id": event_id, "message_id": message_id},
            source="web",
        )


def _publish_event_change(request: Request, event_id: int) -> None:
    request.app.state.stream_hub.publish(
        f"event.{event_id}",
        "event.changed",
        {"event_id": event_id},
        source="web",
    )


def _engine_names(connection: sqlite3.Connection) -> dict[int, str]:
    return {
        engine.engine_id: _engine_display_name(engine.name, engine.version)
        for engine in list_engines(connection)
    }


def _engine_display_name(name: str, version: str | None) -> str:
    return " ".join(part for part in (name.strip(), (version or "").strip()) if part)


def _tournament_names(connection: sqlite3.Connection) -> dict[int, str]:
    return {tournament.id: tournament.name for tournament in list_tournaments(connection)}


def _event_names(connection: sqlite3.Connection) -> dict[int, dict[str, str]]:
    return {
        event.id: {"title": event.title, "slug": event.slug}
        for event in list_events(connection)
    }


def _selected_viewer_game(request: Request, games: tuple[GameRecord, ...]) -> GameRecord | None:
    raw_game_id = request.query_params.get("game_id")
    if raw_game_id is not None:
        try:
            game_id = int(raw_game_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="game not found") from None
        for game in games:
            if game.id == game_id:
                return game
        raise HTTPException(status_code=404, detail="game not found")

    return _tournament_viewer_game(games)


def _home_tournament_cards(
    connection: sqlite3.Connection,
    engines: dict[int, str],
) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    estimator = TournamentEstimator(connection)
    for tournament in list_tournaments(connection):
        if tournament.status not in {"running", "paused"}:
            continue
        active_games = list_active_games(
            connection,
            tournament_id=tournament.id,
            limit=1,
        )
        game = next((game for game in active_games if game.status == "live"), None)
        if tournament.status == "paused" and game is None:
            continue
        moves = list_moves(connection, game.id) if game is not None else ()
        cards.append(
            {
                "tournament": _tournament_summary(
                    connection,
                    tournament,
                    engines,
                    estimator=estimator,
                ),
                "preview": None
                if game is None
                else {
                    "game": game,
                    "moves": moves,
                    "opening": _opening_view(connection, game.opening_id),
                    "last_move": moves[-1] if moves else None,
                    "white_name": engines.get(
                        game.white_engine_id,
                        f"Engine {game.white_engine_id}",
                    ),
                    "black_name": engines.get(
                        game.black_engine_id,
                        f"Engine {game.black_engine_id}",
                    ),
                },
            }
        )
    return cards


def _upcoming_rows(
    connection: sqlite3.Connection,
    engines: dict[int, str],
    *,
    limit: int,
) -> list[dict[str, str | None]]:
    pending_games = list_upcoming_games(connection, limit=max(limit * 16, limit))
    tournaments = {tournament.id: tournament for tournament in list_tournaments(connection)}
    rows: list[dict[str, str | None]] = []
    scheduled_tournament_ids: set[int] = set()
    for game in pending_games:
        tournament = tournaments.get(game.tournament_id)
        if tournament is not None and tournament.status == "scheduled":
            if tournament.id in scheduled_tournament_ids:
                continue
            scheduled_tournament_ids.add(tournament.id)
        rows.append({
            "href": f"/tournaments/{game.tournament_id}?game_id={game.id}",
            "tournament": tournament.name if tournament is not None else f"Tournament {game.tournament_id}",
            "round": str(game.round),
            "white": engines.get(game.white_engine_id, f"Engine {game.white_engine_id}"),
            "black": engines.get(game.black_engine_id, f"Engine {game.black_engine_id}"),
            "status": "scheduled" if tournament is not None and tournament.status == "scheduled" else game.status,
            "scheduled_start_at": tournament.scheduled_start_at if tournament is not None else None,
        })
        if len(rows) >= limit:
            break
    return rows


def _tournament_viewer_game(games: tuple[GameRecord, ...]) -> GameRecord | None:
    for status in ("live", "assigned"):
        for game in games:
            if game.status == status:
                return game
    return None


def _game_payload(
    game: GameRecord,
    engines: dict[int, str],
    *,
    live: bool = False,
) -> dict[str, Any]:
    payload = {
        "id": game.id,
        "tournament_id": game.tournament_id,
        "round": game.round,
        "pair_index": game.pair_index,
        "game_number": game.game_number,
        "opening_id": game.opening_id,
        "status": game.status,
        "result": game.result,
        "white_name": engines.get(game.white_engine_id, f"Engine {game.white_engine_id}"),
        "black_name": engines.get(game.black_engine_id, f"Engine {game.black_engine_id}"),
    }
    if live:
        payload.update(
            {
                "termination": game.termination,
                "white_engine_id": game.white_engine_id,
                "black_engine_id": game.black_engine_id,
                "started_at": game.started_at,
            }
        )
    return payload


def _move_payload(move: MoveRecord, root_fen: str | None) -> dict[str, Any]:
    score_bound = move.score_bound or _uci_score_bound(move.info_line)
    seldepth = move.seldepth if move.seldepth is not None else _uci_info_int(move.info_line, "seldepth")
    hashfull = move.hashfull if move.hashfull is not None else _uci_info_int(move.info_line, "hashfull")
    return {
        "ply": move.ply,
        "uci": move.uci,
        "san": move.san,
        "is_book": move.is_book,
        "eval_cp": move.eval_cp,
        "eval_mate": move.eval_mate,
        "score_bound": score_bound,
        "depth": move.depth,
        "seldepth": seldepth,
        "nodes": move.nodes,
        "nps": move.nps,
        "hashfull": hashfull,
        "pv": move.pv,
        "pv_san": pv_to_san(move.pv, root_fen),
        "info_line": move.info_line,
        "time_ms": move.time_ms,
        "clock_after_ms": move.clock_after_ms,
        "engine_version_id": move.engine_version_id,
    }

def _tournament_live_payload(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    live: dict[Any, Any] | None = None,
    *,
    selected_game_id: int | None = None,
) -> dict[str, Any]:
    engines = _engine_names(connection)
    active_games = list_active_games(
        connection,
        tournament_id=tournament.id,
        limit=500,
    )
    viewer_game = get_game(connection, selected_game_id) if selected_game_id is not None else None
    if viewer_game is not None and viewer_game.tournament_id != tournament.id:
        viewer_game = None
    if viewer_game is None:
        viewer_game = _tournament_viewer_game(active_games)
    viewer_moves = list_moves(connection, viewer_game.id) if viewer_game else ()
    opening = _opening_view(connection, viewer_game.opening_id) if viewer_game else None
    engine_data = _engine_data(viewer_game, viewer_moves, opening)
    clocks = _clock_data(viewer_moves)
    clock_state = _persisted_clock_state(viewer_game, viewer_moves)
    game_live = _live_for_game(live, viewer_game.id if viewer_game else None)
    if game_live is not None and viewer_game is not None:
        engine_data = _merge_engine_data(engine_data, game_live.get("engine_data"))
        clocks = _merge_clock_data(clocks, game_live.get("clocks"))
        if isinstance(game_live.get("clock_state"), dict):
            clock_state = dict(game_live["clock_state"])
    return {
        "tournament": {
            "id": tournament.id,
            "status": tournament.status,
            "current_round": tournament.current_round,
        },
        "game": _game_payload(viewer_game, engines, live=True) if viewer_game else None,
        "opening": opening or {"name": "Start position", "fen": "startpos"},
        "moves": _move_payloads(viewer_moves, opening),
        "engine_data": engine_data,
        "clocks": clocks,
        "clock_state": clock_state,
        "standings": _standings(connection, tournament, engines),
        "active_games": [_game_payload(game, engines, live=True) for game in active_games],
    }


def _live_for_game(
    live: dict[Any, Any] | None,
    game_id: int | None,
) -> dict[str, Any] | None:
    if live is None or game_id is None:
        return None
    if live.get("game_id") == game_id:
        return live
    candidate = live.get(game_id)
    if candidate is None:
        candidate = live.get(str(game_id))
    return candidate if isinstance(candidate, dict) else None


def _persisted_clock_state(
    game: GameRecord | None,
    moves: tuple[MoveRecord, ...],
) -> dict[str, Any] | None:
    if game is None:
        return None
    clocks_ms: dict[str, int | None] = {"white": None, "black": None}
    for move in moves:
        side = "white" if move.ply % 2 == 1 else "black"
        clocks_ms[side] = move.clock_after_ms
    next_side = "black" if moves and moves[-1].ply % 2 == 1 else "white"
    return {
        "game_id": game.id,
        "clocks_ms": clocks_ms,
        "active_side": next_side,
        "running": False,
        "observed_at": None,
        "sent_at": None,
    }


def _merge_engine_data(
    engine_data: dict[str, dict[str, str]],
    live_data: Any,
) -> dict[str, dict[str, str]]:
    if not isinstance(live_data, dict):
        return engine_data
    merged = {
        "white": dict(engine_data["white"]),
        "black": dict(engine_data["black"]),
    }
    if isinstance(live_data.get("kibitzer"), dict):
        merged["kibitzer"] = {}
    for side in ("white", "black", "kibitzer"):
        if isinstance(live_data.get(side), dict):
            merged.setdefault(side, {})
            merged[side].update(
                {
                    key: value
                    for key, value in live_data[side].items()
                    if key in {
                        "depth",
                        "seldepth",
                        "nps",
                        "nodes",
                        "hashfull",
                        "eval",
                        "pv",
                        "pv_san",
                        "info",
                        "root_fen",
                        "engine_id",
                        "eval_cp",
                        "eval_mate",
                    }
                }
            )
    return merged


def _merge_clock_data(
    clocks: dict[str, str],
    live_clocks: Any,
) -> dict[str, str]:
    if not isinstance(live_clocks, dict):
        return clocks
    merged = dict(clocks)
    for side in ("white", "black"):
        if side in live_clocks:
            merged[side] = _clock_label(live_clocks[side])
    return merged


def _clock_data(moves: tuple[MoveRecord, ...]) -> dict[str, str]:
    clocks = {"white": "--:--", "black": "--:--"}
    for move in moves:
        side = "white" if move.ply % 2 == 1 else "black"
        clocks[side] = _clock_label(move.clock_after_ms)
    return clocks


def _clock_label(value: Any) -> str:
    if value is None:
        return "--:--"
    try:
        milliseconds = max(0, int(value))
    except (TypeError, ValueError):
        return "--:--"
    total_seconds, remainder = divmod(milliseconds, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{remainder // 100}"

def _move_root_fens(
    moves: tuple[MoveRecord, ...],
    opening: dict[str, Any] | None,
) -> list[str | None]:
    start_fen = (opening or {}).get("fen") or "startpos"
    try:
        board: chess.Board | None = (
            chess.Board() if start_fen == "startpos" else chess.Board(start_fen)
        )
    except ValueError:
        board = None

    fens: list[str | None] = []
    for move in moves:
        fens.append(board.fen() if board else None)
        if board is None:
            continue
        try:
            board.push_uci(move.uci)
        except ValueError:
            board = None
    return fens


def _move_payloads(
    moves: tuple[MoveRecord, ...],
    opening: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    root_fens = _move_root_fens(moves, opening)
    return [_move_payload(move, root_fens[index]) for index, move in enumerate(moves)]

def _engine_data(
    game: GameRecord | None,
    moves: tuple[MoveRecord, ...],
    opening: dict[str, Any] | None = None,      # new
) -> dict[str, dict[str, str]]:
    if game is None:
        return {
            "white": _engine_data_for_move(None),
            "black": _engine_data_for_move(None),
        }

    root_fens = _move_root_fens(moves, opening)

    def for_side(side: str) -> dict[str, str]:
        index = _latest_move_index_for_side(moves, side)
        if index < 0:
            return _engine_data_for_move(None)
        return _engine_data_for_move(moves[index], root_fens[index])

    return {"white": for_side("white"), "black": for_side("black")}


def _latest_move_index_for_side(moves: tuple[MoveRecord, ...], side: str) -> int:
    white = side == "white"
    for index in range(len(moves) - 1, -1, -1):
        if (moves[index].ply % 2 == 1) == white:
            return index
    return -1

def _engine_data_for_move(move: MoveRecord | None, root_fen: str | None = None) -> dict[str, Any]:
    if move is None:
        return {
            "depth": "-",
            "seldepth": "-",
            "nps": "-",
            "nodes": "-",
            "hashfull": "-",
            "eval": "-",
            "info": "not recorded",
            "pv": "not recorded",
            "pv_san": "not recorded",
        }

    nps = f"{move.nps:,}" if move.nps is not None else "-"
    if move.nps is None and move.nodes is not None and move.time_ms > 0:
        nps = f"{int(move.nodes / (move.time_ms / 1000)):,}"
    seldepth = move.seldepth if move.seldepth is not None else _uci_info_int(move.info_line, "seldepth")
    hashfull = move.hashfull if move.hashfull is not None else _uci_info_int(move.info_line, "hashfull")

    return {
        "engine_id": move.engine_version_id,
        "depth": str(move.depth) if move.depth is not None else "-",
        "seldepth": str(seldepth) if seldepth is not None else "-",
        "nps": nps,
        "nodes": f"{move.nodes:,}" if move.nodes is not None else "-",
        "hashfull": str(hashfull) if hashfull is not None else "-",
        "eval": _eval_label(move),
        "info": move.info_line or move.pv or "not recorded",
        "pv": move.pv or "not recorded",
        "pv_san": pv_to_san(move.pv, root_fen) if move.pv else "not recorded",
    }


def _eval_label(move: MoveRecord) -> str:
    bound = move.score_bound or _uci_score_bound(move.info_line)
    prefix = {"lowerbound": "≥", "upperbound": "≤"}.get(bound, "")
    if move.eval_mate is not None:
        return f"{prefix}#{move.eval_mate}"
    if move.eval_cp is not None:
        return f"{prefix}{move.eval_cp / 100:+.2f}"
    return "-"


def _uci_info_int(line: str | None, key: str) -> int | None:
    if not line:
        return None
    parts = line.split()
    try:
        return int(parts[parts.index(key) + 1])
    except (ValueError, IndexError):
        return None


def _uci_score_bound(line: str | None) -> str | None:
    if not line:
        return None
    parts = line.split()
    try:
        bound = parts[parts.index("score") + 3]
    except (ValueError, IndexError):
        return None
    return bound if bound in {"lowerbound", "upperbound"} else None


def _opening_view(connection: sqlite3.Connection, opening_id: int | None) -> dict[str, Any] | None:
    opening = get_opening_position(connection, opening_id)
    if opening is None:
        return None
    return {
        "name": opening.name,
        "fen": opening.start_fen,
        "book_moves": list(opening.moves),
        "final_fen": opening.fen,
    }


def _tournament_index_stats(tournaments: list[dict[str, Any]]) -> dict[str, int]:
    total_games = sum(item["summary"]["total"] for item in tournaments)
    finished_games = sum(item["summary"]["finished"] for item in tournaments)
    active_statuses = {"scheduled", "running", "paused"}
    return {
        "total": len(tournaments),
        "active": sum(1 for item in tournaments if item["record"].status in active_statuses),
        "live_games": sum(item["summary"]["live"] for item in tournaments),
        "completion_percent": round(finished_games / total_games * 100) if total_games else 0,
    }


def _standings(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    engines: dict[int, str],
) -> list[dict[str, Any]]:
    games = tuple(
        (
            int(row["white_engine_id"]),
            int(row["black_engine_id"]),
            row["result"],
        )
        for row in connection.execute(
            """
            SELECT white_engine_id, black_engine_id, result
            FROM games
            WHERE tournament_id = ? AND result IS NOT NULL
            """,
            (tournament.id,),
        )
    )
    points: dict[int, float] = {engine_id: 0.0 for engine_id in tournament.config.participants}
    played: dict[int, int] = {engine_id: 0 for engine_id in tournament.config.participants}
    wins: dict[int, int] = {engine_id: 0 for engine_id in tournament.config.participants}
    draws: dict[int, int] = {engine_id: 0 for engine_id in tournament.config.participants}
    losses: dict[int, int] = {engine_id: 0 for engine_id in tournament.config.participants}
    for white_engine_id, black_engine_id, result in games:
        for engine_id in (white_engine_id, black_engine_id):
            points.setdefault(engine_id, 0.0)
            played.setdefault(engine_id, 0)
            wins.setdefault(engine_id, 0)
            draws.setdefault(engine_id, 0)
            losses.setdefault(engine_id, 0)
            played[engine_id] += 1
        if result == "1-0":
            points[white_engine_id] += 1
            wins[white_engine_id] += 1
            losses[black_engine_id] += 1
        elif result == "0-1":
            points[black_engine_id] += 1
            wins[black_engine_id] += 1
            losses[white_engine_id] += 1
        else:
            points[white_engine_id] += 0.5
            points[black_engine_id] += 0.5
            draws[white_engine_id] += 1
            draws[black_engine_id] += 1

    matches = list_tournament_matches(connection, tournament.id)
    bye_points: dict[int, float] = {}
    if tournament.config.format == TournamentFormat.SWISS:
        for match in matches:
            if match.status == "bye":
                points[match.engine1_id] += 1.0
                bye_points[match.engine1_id] = bye_points.get(match.engine1_id, 0.0) + 1.0

    buchholz = {engine_id: 0.0 for engine_id in points}
    if tournament.config.format == TournamentFormat.SWISS:
        for white_engine_id, black_engine_id, _result in games:
            buchholz[white_engine_id] += points[black_engine_id]
            buchholz[black_engine_id] += points[white_engine_id]

    stage = {engine_id: 0 for engine_id in points}
    if tournament.config.format == TournamentFormat.KNOCKOUT:
        for match in matches:
            stage[match.engine1_id] = max(stage[match.engine1_id], match.round)
            if match.engine2_id is not None:
                stage[match.engine2_id] = max(stage[match.engine2_id], match.round)
            if match.winner_engine_id is not None:
                stage[match.winner_engine_id] = max(stage[match.winner_engine_id], match.round + 1)

    seed = {
        engine_id: index
        for index, engine_id in enumerate(tournament.config.participants)
    }
    rows = [
        {
            "engine_id": engine_id,
            "name": engines.get(engine_id, f"Engine {engine_id}"),
            "points": points[engine_id],
            "played": played[engine_id],
            "score_percent": _score_percent(
                points[engine_id],
                played[engine_id] + bye_points.get(engine_id, 0.0),
            ),
            "wins": wins[engine_id],
            "draws": draws[engine_id],
            "losses": losses[engine_id],
            "buchholz": buchholz[engine_id],
            "bye_points": bye_points.get(engine_id, 0.0),
            "stage": stage[engine_id],
        }
        for engine_id in points
    ]
    if tournament.config.format == TournamentFormat.KNOCKOUT:
        rows.sort(
            key=lambda row: (
                -row["score_percent"],
                -row["stage"],
                -row["points"],
                seed[row["engine_id"]],
            )
        )
    elif tournament.config.format == TournamentFormat.SWISS:
        rows.sort(
            key=lambda row: (
                -row["score_percent"],
                -row["points"],
                -row["buchholz"],
                seed[row["engine_id"]],
            )
        )
    else:
        rows.sort(key=lambda row: (-row["score_percent"], -row["points"], row["name"]))
    return rows


def _score_percent(points: float, scoring_opportunities: float) -> float:
    return points / scoring_opportunities * 100 if scoring_opportunities else 0.0


def _tournament_rating_summaries(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> list[dict[str, Any]]:
    commits = connection.execute(
        """
        SELECT rating_commit.rating_list_id, rating_list.name
        FROM tournament_rating_list_commits rating_commit
        JOIN rating_lists rating_list ON rating_list.id = rating_commit.rating_list_id
        WHERE rating_commit.tournament_id = ? AND rating_commit.status = 'applied'
        ORDER BY rating_list.name, rating_commit.rating_list_id
        """,
        (tournament_id,),
    ).fetchall()
    summaries: list[dict[str, Any]] = []
    for commit in commits:
        history = connection.execute(
            """
            SELECT rating_history.id, rating_history.engine_id,
                   rating_history.elo_before, rating_history.elo,
                   rating_history.elo_change, rating_history.score,
                   opponent.elo_before AS opponent_elo
            FROM rating_list_history rating_history
            JOIN rating_list_history opponent
              ON opponent.game_id = rating_history.game_id
             AND opponent.rating_list_id = rating_history.rating_list_id
             AND opponent.engine_id = rating_history.opponent_engine_id
            WHERE rating_history.tournament_id = ?
              AND rating_history.rating_list_id = ?
            ORDER BY rating_history.id
            """,
            (tournament_id, commit["rating_list_id"]),
        ).fetchall()
        by_engine: dict[int, dict[str, Any]] = {}
        for item in history:
            engine_id = int(item["engine_id"])
            values = by_engine.setdefault(
                engine_id,
                {
                    "engine_id": engine_id,
                    "elo_before": float(item["elo_before"]),
                    "elo_after": float(item["elo"]),
                    "elo_change": 0.0,
                    "score": 0.0,
                    "games": 0,
                    "wins": 0,
                    "draws": 0,
                    "losses": 0,
                    "opponent_elos": [],
                },
            )
            score = float(item["score"])
            values["elo_after"] = float(item["elo"])
            values["elo_change"] += float(item["elo_change"])
            values["score"] += score
            values["games"] += 1
            values["opponent_elos"].append(float(item["opponent_elo"]))
            if score == 1.0:
                values["wins"] += 1
            elif score == 0.5:
                values["draws"] += 1
            else:
                values["losses"] += 1

        rows: list[dict[str, Any]] = []
        for values in by_engine.values():
            opponent_elos = values.pop("opponent_elos")
            average_opponent_elo = sum(opponent_elos) / len(opponent_elos)
            score_fraction = values["score"] / values["games"]
            if score_fraction <= 0:
                performance_difference = -800.0
            elif score_fraction >= 1:
                performance_difference = 800.0
            else:
                performance_difference = max(
                    -800.0,
                    min(
                        800.0,
                        400.0 * math.log10(score_fraction / (1.0 - score_fraction)),
                    ),
                )
            values["elo_before"] = round(values["elo_before"], 2)
            values["elo_after"] = round(values["elo_after"], 2)
            values["elo_change"] = round(values["elo_change"], 2)
            values["score"] = round(values["score"], 2)
            values["average_opponent_elo"] = round(average_opponent_elo, 2)
            values["performance_elo"] = round(
                average_opponent_elo + performance_difference,
                2,
            )
            rows.append(values)

        initial_elos = [float(row["elo_before"]) for row in rows]
        summaries.append(
            {
                "rating_list_id": commit["rating_list_id"],
                "rating_list_name": commit["name"],
                "average_competitor_elo": (
                    round(sum(initial_elos) / len(initial_elos), 2)
                    if initial_elos
                    else None
                ),
                "rows": rows,
            }
        )
    return summaries


def _tournament_summary(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
    engines: dict[int, str],
    *,
    estimator: TournamentEstimator | None = None,
) -> dict[str, Any]:
    games = list_games(connection, tournament.id)
    summary = _summarize_games(games)
    estimate = (estimator or TournamentEstimator(connection)).estimate(tournament, games)
    participant_names = [
        engines.get(engine_id, f"Engine {engine_id}")
        for engine_id in tournament.config.participants
    ]
    total_games = summary["total"]
    finished_games = summary["finished"]
    return {
        "record": tournament,
        "summary": summary,
        "participant_names": participant_names,
        "participant_preview": participant_names[:6],
        "participant_overflow": max(0, len(participant_names) - 6),
        "participant_count": len(participant_names),
        "progress_percent": round(finished_games / total_games * 100) if total_games else 0,
        "time_control": _time_control_label(tournament.config.time_control),
        "format": tournament.config.format.value.replace("_", " ").title(),
        "estimate": estimate.to_dict(),
    }


def _summarize_games(games: tuple[GameRecord, ...]) -> dict[str, int]:
    summary = {
        "total": len(games),
        "pairs": len({_game_pair_key(game) for game in games}),
        "pending": 0,
        "assigned": 0,
        "live": 0,
        "finished": 0,
        "abandoned": 0,
    }
    for game in games:
        summary[game.status] = summary.get(game.status, 0) + 1
    return summary


def _tournament_game_summary(
    connection: sqlite3.Connection,
    tournament_id: int,
) -> dict[str, int]:
    summary = {
        "total": 0,
        "pairs": 0,
        "pending": 0,
        "assigned": 0,
        "live": 0,
        "finished": 0,
        "abandoned": 0,
    }
    rows = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM games
        WHERE tournament_id = ?
        GROUP BY status
        """,
        (tournament_id,),
    )
    for row in rows:
        count = int(row["count"])
        summary[row["status"]] = count
        summary["total"] += count
    pair_row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM (
          SELECT 1
          FROM games
          WHERE tournament_id = ?
          GROUP BY
            LEAST(white_engine_id, black_engine_id),
            GREATEST(white_engine_id, black_engine_id),
            opening_id,
            match_id,
            (game_number - 1) / 2,
            tiebreak_kind
        ) game_pairs
        """,
        (tournament_id,),
    ).fetchone()
    if pair_row is not None:
        summary["pairs"] = int(pair_row["count"])
    return summary


def _game_pair_key(game: GameRecord) -> tuple[int, int, int | None, int | None, int, str | None]:
    first_engine_id, second_engine_id = sorted(
        (game.white_engine_id, game.black_engine_id)
    )
    return (
        first_engine_id,
        second_engine_id,
        game.opening_id,
        game.match_id,
        (game.game_number - 1) // 2,
        game.tiebreak_kind,
    )


def _settings_view(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
) -> list[tuple[str, str]]:
    """Human-readable label/value pairs describing a tournament's settings."""
    config = tournament.config
    engines = _engine_names(connection)

    rows: list[tuple[str, str]] = [
        ("Format", config.format.value.replace("_", " ").title()),
        ("Time control", _time_control_label(config.time_control)),
    ]

    options = config.format_options
    option_labels = {
        "cycles": "Cycles",
        "rounds": "Rounds",
        "tiebreak": "Tiebreak",
        "hero_engine_id": "Gauntlet hero",
    }
    for field, value in options.model_dump(mode="json").items():
        label = option_labels.get(field, field.replace("_", " ").title())
        if field == "hero_engine_id":
            value = engines.get(value, f"Engine {value}")
        elif isinstance(value, bool):
            value = "Yes" if value else "No"
        rows.append((label, str(value)))

    rows.extend(
        [
            ("Concurrent games", str(config.concurrency)),
            ("Threads per engine", str(config.engine_threads)),
            ("Hash per engine", f"{config.engine_hash_mb}MB"),
            ("Worker hash required", f"{config.engine_hash_mb * 2}MB"),
            ("Rated", "Yes" if config.rated else "No"),
            ("Lag compensation", f"{config.lag_compensation_ms}ms"),
        ]
    )

    for option_name, value in sorted(
        config.uci_options.items(), key=lambda item: item[0].lower()
    ):
        if isinstance(value, bool):
            value = "Yes" if value else "No"
        rows.append((f"Tournament UCI: {option_name}", str(value)))

    if config.opening_suite_id:
        suite = get_opening_suite(connection, config.opening_suite_id)
        rows.append(("Opening suite", suite.name if suite else f"Suite {config.opening_suite_id}"))
    else:
        rows.append(("Opening suite", "None"))

    adjudication = config.adjudication
    if adjudication.draw:
        rows.append(
            (
                "Draw agreement",
                f"after move {adjudication.draw.min_fullmove}, "
                f"within +/-{adjudication.draw.max_abs_cp}cp "
                f"for {adjudication.draw.consecutive_plies} plies",
            )
        )
    if adjudication.resign:
        rows.append(
            (
                "Win adjudication",
                f"beyond +/-{adjudication.resign.min_abs_cp}cp "
                f"for {adjudication.resign.consecutive_plies} plies",
            )
        )
    if adjudication.max_moves:
        rows.append(("Maximum moves", str(adjudication.max_moves)))

    return rows


def _engine_hardware_view(
    connection: sqlite3.Connection,
    tournament: TournamentRecord,
) -> list[dict[str, str]]:
    engine_records = {engine.id: engine for engine in list_engine_records(connection)}
    active_hardware = active_engine_hardware_profiles(connection, tournament.id)
    rows: list[dict[str, str]] = []

    for engine_id in tournament.config.participants:
        engine = engine_records.get(engine_id)
        rows.append(
            {
                "engine_id": str(engine_id),
                "name": (
                    _engine_display_name(engine.name, engine.version)
                    if engine is not None
                    else f"Engine {engine_id}"
                ),
                "hash": f"{tournament.config.engine_hash_mb}MB",
                "threads": str(tournament.config.engine_threads),
                "hardware": _hardware_profiles_label(active_hardware.get(engine_id, ())),
            }
        )

    return rows


def _hardware_profiles_label(profiles: tuple[HardwareInfo, ...]) -> str:
    if not profiles:
        return "No active hardware"
    return " | ".join(_hardware_profile_label(profile) for profile in profiles)


def _hardware_profile_label(profile: HardwareInfo) -> str:
    return (
        f"{profile.cpu_model}, "
        f"{profile.physical_cores}P/{profile.logical_cores}T, "
        f"{profile.ram_gb}GB RAM"
    )


def _time_control_label(time_control: Any) -> str:
    category = time_control.category
    if category == "increment":
        return (
            f"{_milliseconds(time_control.initial_ms)}"
            f" + {_milliseconds(time_control.increment_ms)}"
        )
    if category == "movetime":
        return f"{_milliseconds(time_control.move_time_ms)} per move"
    if category == "movestogo":
        return f"{_milliseconds(time_control.initial_ms)} / {time_control.moves_to_go}"
    if category == "movenodes":
        return f"{time_control.nodes:,} nodes"
    return str(category)


def _milliseconds(value: int) -> str:
    if value >= 60_000 and value % 60_000 == 0:
        return f"{value // 60_000}m"
    if value >= 1_000 and value % 1_000 == 0:
        return f"{value // 1_000}s"
    return f"{value}ms"
