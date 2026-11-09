from __future__ import annotations

from dataclasses import dataclass

from cope.core.benchmark import benchmark_hardware_key
from cope.core.models import IncrementTimeControl


@dataclass(frozen=True)
class SideClock:
    reference_nps: int
    worker_nps: int
    control: IncrementTimeControl


@dataclass(frozen=True)
class GameClockCalibration:
    reference_key: str
    worker_key: str
    white: SideClock
    black: SideClock


def _scaled_ms(milliseconds: int, reference_nps: int, worker_nps: int) -> int:
    return (2 * milliseconds * reference_nps + worker_nps) // (2 * worker_nps)


def _profile(connection, tournament, key, engines):
    engine_ids = sorted(engines)
    rows = connection.execute(
        """SELECT version_id, build_hash, artifact_sha256, nps FROM tournament_clock_profiles
           WHERE tournament_id = ? AND hardware_key = ? AND version_id = ANY(?)""",
        (tournament.id, key, engine_ids),
    ).fetchall()
    if len(rows) != len(engine_ids):
        return None
    for row in rows:
        engine = engines[row["version_id"]]
        if engine.artifact is None or (row["build_hash"], row["artifact_sha256"]) != (
            engine.build_hash, engine.artifact.sha256,
        ):
            return None
    return {row["version_id"]: row["nps"] for row in rows}


def pin_clock_profile(connection, tournament, key, engines):
    if any(engine is None or engine.distribution != "managed" or engine.artifact is None for engine in engines.values()):
        return None
    connection.execute(
        """WITH available AS (
             SELECT benchmark.hardware_key, build.id, build.build_hash, build.artifact_sha256, benchmark.nps
             FROM unnest(?::bigint[], ?::text[], ?::text[]) AS build(id, build_hash, artifact_sha256)
             JOIN engine_benchmarks benchmark
               ON benchmark.build_hash = build.build_hash AND benchmark.artifact_sha256 = build.artifact_sha256
             WHERE benchmark.hardware_key = ?
           )
           INSERT INTO tournament_clock_profiles
               (tournament_id, hardware_key, version_id, build_hash, artifact_sha256, nps)
           SELECT ?, hardware_key, id, build_hash, artifact_sha256, nps
           FROM available WHERE (SELECT count(*) FROM available) = ?
           ON CONFLICT DO NOTHING""",
        (list(engines), [engine.build_hash for engine in engines.values()],
         [engine.artifact.sha256 for engine in engines.values()], key, tournament.id, len(engines)),
    )
    return _profile(connection, tournament, key, engines)


def calibrate_game(connection, tournament, game, worker, engines) -> GameClockCalibration | None:
    reference_key = tournament.config.clock_reference_hardware_key
    if reference_key is None or worker.machine_id is None or worker.hw is None:
        return None
    worker_key = benchmark_hardware_key(worker.machine_id, worker.hw)
    reference = _profile(connection, tournament, reference_key, engines)
    if reference is None:
        return None
    profile = _profile(connection, tournament, worker_key, engines)
    if profile is None and worker_key != reference_key:
        connection.execute(
            """WITH available AS (
                 SELECT reference.version_id, reference.build_hash, reference.artifact_sha256, benchmark.nps
                 FROM tournament_clock_profiles reference
                 JOIN engine_benchmarks benchmark
                   ON benchmark.build_hash = reference.build_hash
                  AND benchmark.artifact_sha256 = reference.artifact_sha256
                 WHERE reference.tournament_id = ? AND reference.hardware_key = ? AND benchmark.hardware_key = ?
               )
               INSERT INTO tournament_clock_profiles
                   (tournament_id, hardware_key, version_id, build_hash, artifact_sha256, nps)
               SELECT ?, ?, version_id, build_hash, artifact_sha256, nps
               FROM available WHERE (SELECT count(*) FROM available) = ?
               ON CONFLICT DO NOTHING""",
            (tournament.id, reference_key, worker_key, tournament.id, worker_key, len(tournament.config.participants)),
        )
        profile = _profile(connection, tournament, worker_key, engines)
    if profile is None:
        return None
    base = tournament.config.time_control
    sides = []
    for engine_id in (game.white_engine_id, game.black_engine_id):
        reference_nps = reference[engine_id]
        worker_nps = profile[engine_id]
        if reference_nps > 4 * worker_nps or worker_nps > 4 * reference_nps:
            return None
        initial = _scaled_ms(base.initial_ms, reference_nps, worker_nps)
        increment = _scaled_ms(base.increment_ms, reference_nps, worker_nps)
        if initial <= 0 or (base.increment_ms and increment <= 0):
            return None
        sides.append(SideClock(reference_nps, worker_nps, IncrementTimeControl(initial_ms=initial, increment_ms=increment)))
    white, black = sides
    return GameClockCalibration(reference_key, worker_key, white, black)


def save_game_calibration(connection, game_id, assignment_key, calibration: GameClockCalibration):
    white = calibration.white
    black = calibration.black
    connection.execute(
        """INSERT INTO game_clock_calibrations
               (game_id, assignment_key, reference_hardware_key, worker_hardware_key,
                white_reference_nps, black_reference_nps, white_worker_nps, black_worker_nps,
                white_initial_ms, black_initial_ms, white_increment_ms, black_increment_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            game_id, assignment_key, calibration.reference_key, calibration.worker_key,
            white.reference_nps, black.reference_nps, white.worker_nps, black.worker_nps,
            white.control.initial_ms, black.control.initial_ms, white.control.increment_ms, black.control.increment_ms,
        ),
    )


def game_calibration(connection, game_id, assignment_key):
    return connection.execute(
        "SELECT * FROM game_clock_calibrations WHERE game_id = ? AND assignment_key = ?",
        (game_id, assignment_key),
    ).fetchone()


def applied_clock(calibration, side):
    return IncrementTimeControl(
        initial_ms=calibration[f"{side}_initial_ms"],
        increment_ms=calibration[f"{side}_increment_ms"],
    )


def restore_scaled_clock(clock_state, previous_worker_nps, current_worker_nps):
    if not isinstance(previous_worker_nps, int) or previous_worker_nps <= 0:
        raise RuntimeError("Paused hardware-scaled game is missing its clock calibration.")
    remaining = clock_state.get("remaining_time")
    if not isinstance(remaining, int) or remaining <= 0:
        raise RuntimeError("Paused hardware-scaled game has an invalid clock.")
    return {
        **clock_state,
        "remaining_time": max(1, _scaled_ms(remaining, previous_worker_nps, current_worker_nps)),
    }
