from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, model_validator

from cope.core.models import TournamentConfig
from cope.variants import VariantId, create_board, get_variant
from cope.db.repo import validate_rating_list_variant, validate_tournament_variant
from cope.db import (
    create_dockerfile_pull_job, create_engine, create_engine_version, create_tournament,
    engine_build_is_benchmarked, get_common_benchmark_reference, get_engine,
    get_engine_version_record, get_git_host, list_engine_records, list_rating_rows,
    schedule_tournament,
)
from cope.engine_dockerfiles import engine_build_hash

from .operations import Plan, Step, audit, dump
from .registry import Input, Waiting, action
from .repository import Recipe


class Preset(Input):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    rating_list_id: int = Field(gt=0)
    category: Literal["bullet", "rapid", "classical"]
    variant: VariantId = "standard"
    config: TournamentConfig
    opponent_count: int = Field(default=9, ge=1, le=499)

    @model_validator(mode="after")
    def classical_resources(self):
        if self.category == "classical" and self.config.engine_threads != 8:
            raise ValueError("Classical intake presets must use eight engine threads")
        if self.config.variant != self.variant:
            raise ValueError("Preset and tournament variants must match")
        if self.config.format.value != "gauntlet":
            raise ValueError("Intake presets must use the gauntlet tournament format")
        if self.config.time_control.category != "increment":
            raise ValueError("Intake presets require a clock time control with increment")
        return self


def save_preset(connection, principal, payload: Preset):
    principal.require("presets:write")
    if connection.execute("SELECT id FROM rating_lists WHERE id = ?", (payload.rating_list_id,)).fetchone() is None:
        raise ValueError("Rating list does not exist")
    if payload.config.opening_suite_id is not None:
        row = connection.execute("SELECT id FROM opening_suites WHERE id = ?", (payload.config.opening_suite_id,)).fetchone()
        if row is None:
            raise ValueError("Opening suite does not exist")
    validate_rating_list_variant(connection, payload.rating_list_id, payload.config)
    validate_tournament_variant(connection, payload.config)
    if get_variant(payload.variant).requires_openings and payload.config.opening_suite_id is None:
        raise ValueError(f"{get_variant(payload.variant).name} presets require an explicit starting-position suite")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (f"control-preset:{payload.key}",))
    revision = connection.execute("SELECT COALESCE(MAX(revision), 0) + 1 AS revision FROM control_presets WHERE key = ?", (payload.key,)).fetchone()["revision"]
    connection.execute(
        "INSERT INTO control_presets(key, revision, name, rating_list_id, category, variant, config, opponent_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (payload.key, revision, payload.name, payload.rating_list_id, payload.category, payload.variant, payload.config.model_dump_json(), payload.opponent_count),
    )
    audit(connection, principal.actor, "preset.created", {"key": payload.key, "revision": revision})
    return {"key": payload.key, "revision": revision}


def list_presets(connection):
    return [{**dict(row), "config": json.loads(row["config"])} for row in connection.execute(
        "SELECT DISTINCT ON (key) * FROM control_presets ORDER BY key, revision DESC"
    )]


class IntakeEngine(Input):
    queue_id: int = Field(gt=0)
    git_host_id: int = Field(gt=0)
    repository_full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$", max_length=300)
    source_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    recipe: Recipe
    author: str = Field(default="", max_length=120)
    uci_options: dict[str, str | int | bool] = Field(default_factory=dict)
    dfrc: Literal["supported", "unsupported", "unknown"] = "unknown"
    variant_capabilities: dict[VariantId, Literal["supported", "unsupported", "unknown"]] = Field(default_factory=dict)
    capability_evidence: str = Field(default="", max_length=2000)
    top20: bool | None = None
    estimated_elo: float | None = Field(default=None, ge=-10000, le=10000, allow_inf_nan=False)
    strength_evidence: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_evidence(self):
        if (self.dfrc != "unknown" or any(value != "unknown" for value in self.variant_capabilities.values())) and not self.capability_evidence:
            raise ValueError("A variant capability assessment requires evidence")
        if (self.top20 is not None or self.estimated_elo is not None) and not self.strength_evidence:
            raise ValueError("A strength assessment requires evidence")
        if any(not name or name.lower() in {"threads", "hash", "uci_chess960", "uci_variant", "duck960"} for name in self.uci_options):
            raise ValueError("Resource and variant options are controlled by the tournament")
        return self


class IntakeRequest(Input):
    title: str = Field(default="Engine intake", min_length=1, max_length=200)
    queue_revision: int = Field(ge=0)
    repository_base_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    engines: list[IntakeEngine] = Field(min_length=1, max_length=100)
    preset_keys: list[str] = Field(min_length=1, max_length=20)
    top20_rating_list_id: int | None = Field(default=None, gt=0)


def queue_snapshot(connection):
    revision = connection.execute("SELECT revision FROM control_queue_state WHERE id = 1").fetchone()["revision"]
    acknowledged = [dict(row) for row in connection.execute("SELECT * FROM engine_acknowledged_queue ORDER BY position, id")]
    waiting = [
        {**dict(row), "config": json.loads(row["config"]) if row["config"] else None}
        for row in connection.execute(
            """SELECT queue.*, engine.name AS engine_name, version.version AS engine_version, rating.name AS rating_list_name, tournament.status AS tournament_status
               FROM engine_waiting_test_queue queue JOIN engine_versions version ON version.id = queue.engine_version_id
               JOIN engines engine ON engine.id = version.engine_id JOIN rating_lists rating ON rating.id = queue.rating_list_id
               LEFT JOIN tournaments tournament ON tournament.id = queue.tournament_id
               ORDER BY queue.priority_group, queue.duration_ms, queue.submitted_at NULLS FIRST, queue.position, queue.id"""
        )
    ]
    return {"revision": revision, "acknowledged": acknowledged, "waiting_for_test": waiting}


def compile_intake(connection, payload: IntakeRequest):
    snapshot = queue_snapshot(connection)
    if snapshot["revision"] != payload.queue_revision:
        raise ValueError("Engine queue changed; reload it before planning")
    if len({engine.queue_id for engine in payload.engines}) != len(payload.engines):
        raise ValueError("Submission IDs must be unique")
    if len({engine.recipe.path.casefold() for engine in payload.engines}) != len(payload.engines):
        raise ValueError("Each engine requires a distinct recipe path")
    presets = {item["key"]: item for item in list_presets(connection)}
    if len(set(payload.preset_keys)) != len(payload.preset_keys) or any(key not in presets for key in payload.preset_keys):
        raise ValueError("Choose distinct existing tournament presets")
    chosen = [presets[key] for key in payload.preset_keys]
    categories = {item["category"] for item in chosen if item["variant"] == "standard"}
    if categories and not {"bullet", "rapid"}.issubset(categories):
        raise ValueError("Select standard bullet and rapid presets")
    if "classical" in categories and payload.top20_rating_list_id is None:
        raise ValueError("Choose the rating list used to assess top-20 eligibility")
    if payload.top20_rating_list_id and connection.execute("SELECT id FROM rating_lists WHERE id = ?", (payload.top20_rating_list_id,)).fetchone() is None:
        raise ValueError("Top-20 reference list does not exist")
    rows = {row["id"]: row for row in snapshot["acknowledged"]}
    submissions = []
    for item in payload.engines:
        row = rows.get(item.queue_id)
        if row is None:
            raise ValueError(f"Recognised submission {item.queue_id} does not exist")
        if len(row["name"]) > 80 or len(row["version"]) > 80:
            raise ValueError("Engine names and versions must be at most 80 characters")
        host = get_git_host(connection, item.git_host_id)
        if host is None or not host.enabled:
            raise ValueError("Engine Git host is unavailable")
        if any(part in {".", ".."} for part in item.repository_full_name.split("/")):
            raise ValueError("Invalid repository name")
        from cope.web.engine_sources import canonical_repository_url

        expected = engine_build_hash(canonical_repository_url(host, item.repository_full_name), item.source_ref, item.recipe.content)
        existing = connection.execute(
            """SELECT version.build_hash, version.uci_options FROM engine_versions version
               JOIN engines engine ON engine.id = version.engine_id WHERE engine.name = ? AND version.version = ?""",
            (row["name"], row["version"]),
        ).fetchone()
        if existing and (existing["build_hash"] != expected or json.loads(existing["uci_options"]) != item.uci_options):
            raise ValueError(f"{row['name']} {row['version']} already exists with a different build or options; choose a distinct version")
        reused = connection.execute("SELECT id FROM engine_versions WHERE dockerfile_path = ? AND dockerfile <> ? LIMIT 1", (item.recipe.path, item.recipe.content)).fetchone()
        if reused:
            raise ValueError(f"{item.recipe.path} is already used by another build; choose a distinct Dockerfile filename")
        submissions.append({"id": row["id"], "name": row["name"], "version": row["version"], "submitted_at": row["submitted_at"], "position": row["position"]})
    steps = [Step(key="reserve", action="intake.reserve", input={"revision": payload.queue_revision, "submissions": submissions})]
    steps.append(Step(key="publish", action="recipes.publish", input={"recipes": [item.recipe.model_dump() for item in payload.engines], "base_commit": payload.repository_base_commit}, depends_on=["reserve"]))
    steps.append(Step(key="pull", action="recipes.pull", input={"publish_step": "publish"}, depends_on=["publish"]))
    steps.append(Step(key="installed", action="recipes.await_pull", input={"pull_step": "pull", "publish_step": "publish"}, depends_on=["pull", "publish"]))
    for index, engine in enumerate(payload.engines):
        key = f"register-{index}"
        steps.append(Step(key=key, action="intake.register", input={"engine": engine.model_dump(), "submission": submissions[index]}, depends_on=["installed"]))
        steps.append(Step(key=f"queue-{index}", action="intake.queue", input={"registration_step": key, "engine": engine.model_dump(), "presets": chosen, "top20_rating_list_id": payload.top20_rating_list_id}, depends_on=[key]))
        steps.append(Step(key=f"materialise-{index}", action="intake.materialise", input={"queue_step": f"queue-{index}"}, depends_on=[f"queue-{index}"]))
    return Plan(title=payload.title, steps=steps)


class Reserve(Input):
    revision: int
    submissions: list[dict]


@action("intake.reserve", "Reserve recognised engine submissions", "intake:write", Reserve)
def reserve(context, payload):
    connection = context.connection
    revision = connection.execute("SELECT revision FROM control_queue_state WHERE id = 1 FOR UPDATE").fetchone()["revision"]
    if revision != payload.revision:
        raise ValueError("Engine queue changed after planning; prepare a new plan")
    for item in payload.submissions:
        row = connection.execute("SELECT * FROM engine_acknowledged_queue WHERE id = ? FOR UPDATE", (item["id"],)).fetchone()
        if row is None or row["name"] != item["name"] or row["version"] != item["version"]:
            raise ValueError("Recognised submission changed after planning")
        if row["intake_operation_id"] and row["intake_operation_id"] != context.operation["id"]:
            previous = connection.execute("SELECT status FROM control_operations WHERE id = ?", (row["intake_operation_id"],)).fetchone()
            if previous and previous["status"] not in {"cancelled", "succeeded", "failed"}:
                raise ValueError("Submission is reserved by another intake operation")
        connection.execute("UPDATE engine_acknowledged_queue SET intake_operation_id = ? WHERE id = ?", (context.operation["id"], item["id"]))
    return {"reserved": [item["id"] for item in payload.submissions]}


class Pull(Input):
    publish_step: str


@action("recipes.pull", "Install the published Dockerfile commit through the updater", "recipes:pull", Pull)
def pull_recipes(context, payload):
    commit = context.result(payload.publish_step)["commit"]
    from cope.control.repository import repository_settings
    from cope.db import get_cope_build_settings

    url, _ = repository_settings(context.connection)
    configured = get_cope_build_settings(context.connection, default_repository_url=os.environ.get("COPE_UPDATE_REPOSITORY_URL", "")).repository_url
    if configured.rstrip("/").removesuffix(".git") != url.rstrip("/").removesuffix(".git"):
        raise ValueError("Control recipe repository must match the updater repository")
    row = context.connection.execute("SELECT id FROM dockerfile_pull_jobs WHERE target_commit = ? AND status = 'succeeded' ORDER BY id DESC LIMIT 1", (commit,)).fetchone()
    if row:
        latest = context.connection.execute("SELECT target_commit, status FROM dockerfile_pull_jobs ORDER BY id DESC LIMIT 1").fetchone()
        if latest and latest["status"] == "succeeded" and latest["target_commit"] == commit:
            return {"job_id": row["id"], "commit": commit}
    try:
        job = create_dockerfile_pull_job(context.connection, requested_ref=commit)
    except ValueError as error:
        raise Waiting(str(error)) from error
    return {"job_id": job, "commit": commit}


class AwaitPull(Pull):
    pull_step: str


@action("recipes.await_pull", "Wait for the updater to install the exact Dockerfile revision", "recipes:pull", AwaitPull)
def await_pull(context, payload):
    result = context.result(payload.pull_step)
    row = context.connection.execute("SELECT * FROM dockerfile_pull_jobs WHERE id = ?", (result["job_id"],)).fetchone()
    if row is None:
        raise ValueError("Dockerfile pull job disappeared")
    if row["status"] == "failed":
        raise ValueError(f"Dockerfile pull failed: {row['error']}; retry the pull in a new operation")
    if row["status"] != "succeeded":
        raise Waiting(f"Waiting for Dockerfile pull {row['id']}: {row['status']}")
    if row["target_commit"] != context.result(payload.publish_step)["commit"]:
        raise ValueError("Updater installed a different commit")
    return {"job_id": row["id"], "commit": row["target_commit"]}


class Register(Input):
    engine: IntakeEngine
    submission: dict


@action("intake.register", "Register a pinned engine version and request benchmarks", "engines:write", Register)
def register_engine(context, payload):
    from cope.web.engine_sources import canonical_repository_url

    connection = context.connection
    item, submission = payload.engine, payload.submission
    reservation = connection.execute("SELECT * FROM engine_acknowledged_queue WHERE id = ? FOR UPDATE", (submission["id"],)).fetchone()
    if reservation is None or reservation["intake_operation_id"] != context.operation["id"] or reservation["name"] != submission["name"] or reservation["version"] != submission["version"]:
        raise ValueError("Submission is not reserved by this operation or its identity changed")
    from cope.engine_dockerfiles import read_engine_dockerfile

    if read_engine_dockerfile(item.recipe.path) != item.recipe.content:
        raise ValueError("Installed Dockerfile no longer matches this intake plan")
    host = get_git_host(connection, item.git_host_id)
    if host is None or not host.enabled:
        raise ValueError("Engine Git host is unavailable")
    repository_url = canonical_repository_url(host, item.repository_full_name)
    content = item.recipe.content
    expected = engine_build_hash(repository_url, item.source_ref, content)
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (f"intake-engine:{submission['name']}",))
    family = connection.execute("SELECT id FROM engines WHERE name = ?", (submission["name"],)).fetchone()
    family_id = family["id"] if family else create_engine(connection, name=submission["name"], author=item.author, active=True)
    existing = connection.execute("SELECT id, build_hash, uci_options FROM engine_versions WHERE engine_id = ? AND version = ?", (family_id, submission["version"])).fetchone()
    if existing:
        if existing["build_hash"] != expected or json.loads(existing["uci_options"]) != item.uci_options:
            raise ValueError("Existing engine version has a different source, recipe or options; register a distinct version")
        version_id = existing["id"]
    else:
        version_id = create_engine_version(
            connection, engine_id=family_id, version=submission["version"], git_host_id=host.id,
            repository_url=repository_url, repository_full_name=item.repository_full_name,
            source_ref=item.source_ref, source_kind="commit", dockerfile_path=item.recipe.path,
            dockerfile=content, uci_options=item.uci_options, active=True,
        )
    connection.execute(
        """INSERT INTO control_engine_capabilities(build_hash, dfrc, evidence, actor) VALUES (?, ?, ?, ?)
           ON CONFLICT(build_hash) DO UPDATE SET dfrc = EXCLUDED.dfrc, evidence = EXCLUDED.evidence, actor = EXCLUDED.actor, recorded_at = clock_timestamp()""",
        (expected, item.variant_capabilities.get("dfrc", item.dfrc), item.capability_evidence, context.actor),
    )
    for variant, status in {"dfrc": item.dfrc, **item.variant_capabilities}.items():
        connection.execute("INSERT INTO engine_variant_capabilities(build_hash, variant, status, evidence, actor) VALUES (?, ?, ?, ?, ?) ON CONFLICT(build_hash, variant) DO UPDATE SET status = EXCLUDED.status, evidence = EXCLUDED.evidence, actor = EXCLUDED.actor, recorded_at = clock_timestamp()", (expected, variant, status, item.capability_evidence, context.actor))
    connection.execute("UPDATE engine_acknowledged_queue SET engine_version_id = ? WHERE id = ?", (version_id, submission["id"]))
    from cope.db import schedule_benchmark_jobs

    engine = get_engine(connection, version_id)
    for hardware in connection.execute("SELECT DISTINCT hardware_key FROM benchmarkers WHERE hardware_key IS NOT NULL AND status != 'revoked'"):
        schedule_benchmark_jobs(connection, hardware_key=hardware["hardware_key"], engines=(engine,))
    return {"engine_version_id": version_id, "build_hash": expected, "submission": submission}


class QueuePlans(Input):
    registration_step: str
    engine: IntakeEngine
    presets: list[dict]
    top20_rating_list_id: int | None = None


@action("intake.queue", "Create ordered, versioned testing plans", "queue:write", QueuePlans)
def queue_plans(context, payload):
    registration = context.result(payload.registration_step)
    connection = context.connection
    connection.execute("SELECT revision FROM control_queue_state WHERE id = 1 FOR UPDATE")
    submission = registration["submission"]
    plans = []
    for preset in payload.presets:
        config = preset["config"]
        clock = config["time_control"]
        duration = 2 * (clock["initial_ms"] + 80 * clock["increment_ms"])
        priority = 1 if preset["variant"] == "dfrc" else 0
        plan = {"preset": preset, "assessment": {"top20": payload.engine.top20, "estimated_elo": payload.engine.estimated_elo,
                "evidence": payload.engine.strength_evidence, "reference_list_id": payload.top20_rating_list_id},
                "dfrc": payload.engine.dfrc, "capability_evidence": payload.engine.capability_evidence,
                "ordering_policy": {"revision": 1, "nominal_moves": 80, "dfrc_last": True, "oldest_first": True}}
        row = connection.execute(
            """INSERT INTO engine_waiting_test_queue(engine_version_id, rating_list_id, position, submitted_at,
               preset_key, preset_revision, operation_id, priority_group, duration_ms, build_hash, config)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(engine_version_id, rating_list_id, preset_key) DO NOTHING RETURNING id""",
            (registration["engine_version_id"], preset["rating_list_id"], submission["position"], submission["submitted_at"],
             preset["key"], preset["revision"], context.operation["id"], priority, duration, registration["build_hash"], dump(plan)),
        ).fetchone()
        if row is None:
            row = connection.execute("SELECT * FROM engine_waiting_test_queue WHERE engine_version_id = ? AND rating_list_id = ? AND preset_key = ?", (registration["engine_version_id"], preset["rating_list_id"], preset["key"])).fetchone()
            if row["build_hash"] != registration["build_hash"] or row["config"] != dump(plan):
                raise ValueError("An existing testing plan has different settings; resolve it before creating another intake")
        plans.append(row["id"])
    connection.execute("UPDATE control_queue_state SET revision = revision + 1 WHERE id = 1")
    return {"queue_ids": plans, "engine_version_id": registration["engine_version_id"]}


class Materialise(Input):
    queue_step: str


def _strength(connection, hero_id, assessment):
    if assessment["top20"] is not None:
        return assessment["top20"], {"basis": "operator", "evidence": assessment["evidence"]}
    rows = list_rating_rows(connection, assessment["reference_list_id"]) if assessment["reference_list_id"] else ()
    rated = [row for row in rows if row.games_played > 0]
    hero = next((row for row in rated if row.engine.engine_id == hero_id), None)
    estimate = assessment["estimated_elo"] if assessment["estimated_elo"] is not None else hero.elo if hero else None
    if estimate is None:
        return None, {"basis": "awaiting_rating", "reference_list_id": assessment["reference_list_id"]}
    records = {record.id: record for record in list_engine_records(connection)}
    latest = {}
    for row in rated:
        record = records.get(row.engine.engine_id)
        if record is None or not record.active or not record.engine_active:
            continue
        previous = latest.get(record.engine_id)
        if previous is None or records[previous.engine.engine_id].id < record.id:
            latest[record.engine_id] = row
    hero_family = records[hero_id].engine_id
    stronger = {family for family, row in latest.items() if row.elo > estimate and family != hero_family}
    snapshot = [{"engine_version_id": row.engine.engine_id, "elo": row.elo, "games": row.games_played}
                for row in sorted(latest.values(), key=lambda row: (-row.elo, row.engine.engine_id))]
    return len(stronger) < 20, {"basis": "rating" if hero else "estimate", "elo": estimate, "predicted_rank": len(stronger) + 1,
                               "reference_list_id": assessment["reference_list_id"], "evidence": assessment["evidence"], "reference_snapshot": snapshot}


def _materialise_one(context, queue_id):
    connection = context.connection
    queue = connection.execute("SELECT * FROM engine_waiting_test_queue WHERE id = ? FOR UPDATE", (queue_id,)).fetchone()
    if queue is None:
        return {"queue_id": queue_id, "status": "cancelled", "reason": "Testing plan was removed"}
    if queue["tournament_id"]:
        tournament = connection.execute("SELECT status FROM tournaments WHERE id = ?", (queue["tournament_id"],)).fetchone()
        return {"queue_id": queue_id, "tournament_id": queue["tournament_id"], "status": tournament["status"] if tournament else "cancelled"}
    if queue["status"] in {"ready", "scheduled", "running", "paused", "finished", "aborted", "cancelled"}:
        return {"queue_id": queue_id, "status": "cancelled", "reason": "Previously created tournament was removed or this plan was cancelled"}
    plan = json.loads(queue["config"])
    preset = plan["preset"]
    hero_id = queue["engine_version_id"]
    hero = get_engine_version_record(connection, hero_id)
    if hero is None or not hero.active or not hero.engine_active:
        return {"queue_id": queue_id, "status": "waiting", "reason": "Engine version is inactive or unavailable"}
    if hero.build_hash != queue["build_hash"]:
        raise ValueError("Engine build changed after queueing; create a new version and intake plan")
    assessment = None
    if preset["category"] == "classical":
        eligible, assessment = _strength(connection, hero_id, plan["assessment"])
        if eligible is None:
            return {"queue_id": queue_id, "status": "waiting", "reason": "Awaiting top-20 strength evidence on the reference rating list"}
        if not eligible:
            return {"queue_id": queue_id, "status": "skipped", "reason": "Does not qualify for top-20 classical", "assessment": assessment}
    validate_rating_list_variant(connection, preset["rating_list_id"], TournamentConfig.model_validate(preset["config"]))
    if preset["variant"] != "standard":
        capability = connection.execute("SELECT status FROM engine_variant_capabilities WHERE build_hash = ? AND variant = ?", (hero.build_hash, preset["variant"])).fetchone()
        status = capability["status"] if capability else "unknown"
        if status != "supported":
            return {"queue_id": queue_id, "status": "skipped" if status == "unsupported" else "waiting", "reason": f"{preset['variant']} capability is {status}"}
    if not engine_build_is_benchmarked(connection, engine_version_id=hero_id, build_hash=hero.build_hash):
        jobs = list(connection.execute("SELECT status, error FROM benchmark_jobs WHERE build_hash = ?", (hero.build_hash,)))
        if jobs and all(job["status"] == "failed" for job in jobs):
            raise ValueError(f"Engine preparation failed: {jobs[0]['error']}; repair or reschedule its benchmark and resume")
        return {"queue_id": queue_id, "status": "waiting", "reason": "Awaiting engine artifact and benchmark"}
    records = {item.id: item for item in list_engine_records(connection)}
    candidates = []
    hero_rating = next((row.elo for row in list_rating_rows(connection, preset["rating_list_id"]) if row.engine.engine_id == hero_id), None)
    estimate = plan["assessment"]["estimated_elo"]
    target = hero_rating if hero_rating is not None else estimate
    for row in list_rating_rows(connection, preset["rating_list_id"]):
        record = records.get(row.engine.engine_id)
        if record is None or record.id == hero_id or record.engine_id == hero.engine_id or not record.active or not record.engine_active:
            continue
        if not engine_build_is_benchmarked(connection, engine_version_id=record.id, build_hash=record.build_hash):
            continue
        if preset["variant"] != "standard":
            capability = connection.execute("SELECT status FROM engine_variant_capabilities WHERE build_hash = ? AND variant = ?", (record.build_hash, preset["variant"])).fetchone()
            if capability is None or capability["status"] != "supported":
                continue
        candidates.append(row)
    candidates.sort(key=lambda row: (abs(row.elo - target) if target is not None else -row.elo, row.games_played, row.engine.engine_id))
    selected = []
    families = set()
    for candidate in candidates:
        family_id = records[candidate.engine.engine_id].engine_id
        if family_id not in families:
            selected.append(candidate)
            families.add(family_id)
        if len(selected) == preset["opponent_count"]:
            break
    if len(selected) < preset["opponent_count"]:
        return {"queue_id": queue_id, "status": "waiting", "reason": f"Only {len(selected)} compatible opponents; preset requires {preset['opponent_count']}"}
    participants = [hero_id, *(row.engine.engine_id for row in selected)]
    engines = tuple(get_engine(connection, engine_id) for engine_id in participants)
    if get_common_benchmark_reference(connection, engines) is None:
        return {"queue_id": queue_id, "status": "waiting", "reason": "Participants need a shared benchmark hardware reference"}
    config_data = {**preset["config"], "participants": participants,
                   "format_options": {**preset["config"]["format_options"], "hero_engine_id": hero_id}}
    config = TournamentConfig.model_validate(config_data)
    if config.opening_suite_id is not None:
        from cope.db import list_suite_openings
        import chess

        openings = list_suite_openings(connection, config.opening_suite_id)
        if not openings:
            raise ValueError("Tournament opening suite is empty")
        for opening in openings:
            board = create_board(opening.start_fen, variant=config.variant)
            if not board.is_valid():
                raise ValueError("Opening suite contains a position invalid for the selected variant")
    tournament_id = create_tournament(connection, f"{hero.name} {hero.version} · {preset['name']}"[:160], config)
    for engine in engines:
        connection.execute(
            "INSERT INTO control_tournament_builds(tournament_id, engine_version_id, build_hash, engine_spec) VALUES (?, ?, ?, ?)",
            (tournament_id, engine.engine_id, engine.build_hash, engine.model_dump_json()),
        )
    schedule_tournament(connection, tournament_id, datetime.now(UTC).isoformat())
    connection.execute("UPDATE engine_waiting_test_queue SET tournament_id = ? WHERE id = ?", (tournament_id, queue_id))
    return {"queue_id": queue_id, "tournament_id": tournament_id, "status": "ready", "assessment": assessment}


@action("intake.materialise", "Create and schedule ready testing tournaments", "tournaments:write", Materialise)
def materialise(context, payload):
    previous = context.result(payload.queue_step)
    results = []
    for queue_id in previous["queue_ids"]:
        context.connection.execute("SAVEPOINT materialise_plan")
        try:
            result = _materialise_one(context, queue_id)
        except ValueError as error:
            context.connection.execute("ROLLBACK TO SAVEPOINT materialise_plan")
            result = {"queue_id": queue_id, "status": "failed", "reason": str(error)}
        finally:
            context.connection.execute("RELEASE SAVEPOINT materialise_plan")
        context.connection.execute("UPDATE engine_waiting_test_queue SET status = ?, reason = ? WHERE id = ?", (result["status"], result.get("reason", ""), queue_id))
        results.append(result)
    waiting = any(item["status"] == "waiting" for item in results)
    result = {"plans": results, "waiting": waiting}
    if waiting:
        result["_retry_after"] = 30
    elif any(item["status"] == "failed" for item in results):
        result["_failed"] = "Some testing plans failed; inspect their reasons, repair the prerequisites and resume"
    return result


class Capability(Input):
    engine_version_id: int = Field(gt=0)
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dfrc: Literal["supported", "unsupported", "unknown"]
    evidence: str = Field(min_length=1, max_length=2000)


@action("engines.assess_capability", "Record DFRC capability evidence for an exact engine build", "engines:write", Capability)
def assess_capability(context, payload):
    engine = get_engine(context.connection, payload.engine_version_id)
    if engine is None or engine.build_hash != payload.build_hash:
        raise ValueError("Engine build no longer matches the capability assessment")
    context.connection.execute(
        """INSERT INTO control_engine_capabilities(build_hash, dfrc, evidence, actor) VALUES (?, ?, ?, ?)
           ON CONFLICT(build_hash) DO UPDATE SET dfrc = EXCLUDED.dfrc, evidence = EXCLUDED.evidence, actor = EXCLUDED.actor, recorded_at = clock_timestamp()""",
        (payload.build_hash, payload.dfrc, payload.evidence, context.actor),
    )
    context.connection.execute("INSERT INTO engine_variant_capabilities(build_hash, variant, status, evidence, actor) VALUES (?, ?, ?, ?, ?) ON CONFLICT(build_hash, variant) DO UPDATE SET status = EXCLUDED.status, evidence = EXCLUDED.evidence, actor = EXCLUDED.actor, recorded_at = clock_timestamp()", (payload.build_hash, "dfrc", payload.dfrc, payload.evidence, context.actor))
    return payload.model_dump()


class VariantCapability(Input):
    engine_version_id: int = Field(gt=0)
    build_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant: VariantId
    status: Literal["supported", "unsupported", "unknown"]
    evidence: str = Field(min_length=1, max_length=2000)


@action("engines.assess_variant", "Record variant capability evidence for an exact engine build", "engines:write", VariantCapability)
def assess_variant(context, payload):
    engine = get_engine(context.connection, payload.engine_version_id)
    if engine is None or engine.build_hash != payload.build_hash:
        raise ValueError("Engine build no longer matches the capability assessment")
    context.connection.execute("INSERT INTO engine_variant_capabilities(build_hash, variant, status, evidence, actor) VALUES (?, ?, ?, ?, ?) ON CONFLICT(build_hash, variant) DO UPDATE SET status = EXCLUDED.status, evidence = EXCLUDED.evidence, actor = EXCLUDED.actor, recorded_at = clock_timestamp()", (payload.build_hash, payload.variant, payload.status, payload.evidence, context.actor))
    if payload.variant == "dfrc":
        context.connection.execute("INSERT INTO control_engine_capabilities(build_hash, dfrc, evidence, actor) VALUES (?, ?, ?, ?) ON CONFLICT(build_hash) DO UPDATE SET dfrc = EXCLUDED.dfrc, evidence = EXCLUDED.evidence, actor = EXCLUDED.actor, recorded_at = clock_timestamp()", (payload.build_hash, payload.status, payload.evidence, context.actor))
    return payload.model_dump()


class DfrcSuite(Input):
    name: str = Field(min_length=1, max_length=120)
    positions: int = Field(default=100, ge=1, le=10000)
    seed: int = Field(ge=0, le=2147483647)


@action("openings.generate_dfrc", "Create a reproducible double Fischer random opening suite", "openings:write", DfrcSuite)
def generate_dfrc_suite(context, payload, variant: str = "dfrc"):
    import chess
    import random
    from cope.core.models import OpeningLine
    from cope.db import create_opening_suite, replace_suite_openings

    rng = random.Random(payload.seed)
    positions = []
    for index in range(payload.positions):
        white, black = rng.randrange(960), rng.randrange(960)
        board = chess.Board.from_chess960_pos(white)
        other = chess.Board.from_chess960_pos(black)
        for square in chess.SQUARES_180:
            if chess.square_rank(square) == 7:
                board.set_piece_at(square, other.piece_at(square))
        board.castling_rights = (board.rooks & board.occupied_co[chess.WHITE] & chess.BB_RANK_1) | (board.rooks & board.occupied_co[chess.BLACK] & chess.BB_RANK_8)
        fen = board.fen(shredder=True)
        positions.append(OpeningLine(name=f"{get_variant(variant).name} {white}/{black} #{index + 1}", start_fen=fen, fen=fen))
    suite_id = create_opening_suite(context.connection, name=payload.name, description=f"{get_variant(variant).name} seed {payload.seed}; {payload.positions} independently sampled starting positions", variant=variant)
    replace_suite_openings(context.connection, suite_id, positions)
    return {"opening_suite_id": suite_id, "seed": payload.seed, "positions": payload.positions}


@action("openings.generate_duck960", "Create a reproducible Duck960 opening suite", "openings:write", DfrcSuite)
def generate_duck960_suite(context, payload):
    return generate_dfrc_suite(context, payload, variant="duck960")


class WorkerDrain(Input):
    worker_id: int = Field(gt=0)
    draining: bool


@action("workers.set_drain", "Drain or resume a worker without interrupting active games", "workers:write", WorkerDrain)
def set_worker_drain(context, payload):
    row = context.connection.execute("SELECT id FROM workers WHERE id = ?", (payload.worker_id,)).fetchone()
    if row is None:
        raise ValueError("Worker does not exist")
    context.connection.execute(
        """INSERT INTO control_worker_settings(worker_id, draining, changed_by) VALUES (?, ?, ?)
           ON CONFLICT(worker_id) DO UPDATE SET draining = EXCLUDED.draining, changed_by = EXCLUDED.changed_by, changed_at = clock_timestamp()""",
        (payload.worker_id, payload.draining, context.actor),
    )
    return {"worker_id": payload.worker_id, "draining": payload.draining, "active_games": "continue"}


class RemovePlans(Input):
    queue_ids: list[int] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def positive_ids(self):
        if any(value < 1 for value in self.queue_ids) or len(set(self.queue_ids)) != len(self.queue_ids):
            raise ValueError("Supply distinct positive testing plan IDs")
        return self


@action("queue.remove_plans", "Remove pending or terminal testing plans", "queue:write", RemovePlans)
def remove_plans(context, payload):
    context.connection.execute("SELECT revision FROM control_queue_state WHERE id = 1 FOR UPDATE")
    for queue_id in payload.queue_ids:
        row = context.connection.execute(
            "SELECT tournament_id FROM engine_waiting_test_queue WHERE id = ? FOR UPDATE",
            (queue_id,),
        ).fetchone()
        tournament = context.connection.execute("SELECT status FROM tournaments WHERE id = ?", (row["tournament_id"],)).fetchone() if row and row["tournament_id"] else None
        if tournament and tournament["status"] not in {"finished", "aborted"}:
            raise ValueError("Abort or finish the linked tournament before removing its testing plan")
        context.connection.execute("DELETE FROM engine_waiting_test_queue WHERE id = ?", (queue_id,))
    context.connection.execute("UPDATE control_queue_state SET revision = revision + 1 WHERE id = 1")
    return {"removed_queue_ids": payload.queue_ids}
