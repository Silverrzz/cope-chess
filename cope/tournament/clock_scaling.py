from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from cope.core.benchmark import benchmark_hardware_key, parse_benchmark_nps
from cope.core.models import IncrementTimeControl, MoveTimeControl, MovesToGoTimeControl


@dataclass(frozen=True)
class SideClock:
    reference_nps: int
    worker_nps: int
    control: IncrementTimeControl | MoveTimeControl | MovesToGoTimeControl


@dataclass(frozen=True)
class GameClockCalibration:
    reference_key: str
    worker_key: str
    white: SideClock
    black: SideClock


def _scaled_ms(milliseconds: int, scale: Fraction) -> int:
    return (2 * milliseconds * scale.numerator + scale.denominator) // (2 * scale.denominator)


def _shared_scale(white_reference: int, white_worker: int, black_reference: int, black_worker: int) -> Fraction:
    return (Fraction(white_reference, white_worker) + Fraction(black_reference, black_worker)) / 2


def shared_clock_scale(calibration) -> Fraction:
    return _shared_scale(
        calibration["white_reference_nps"], calibration["white_worker_nps"],
        calibration["black_reference_nps"], calibration["black_worker_nps"],
    )


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


def _pin_verified_profile(connection, tournament, key, rows, expected_count):
    if len(rows) != expected_count:
        return False
    for row in rows:
        if row["elapsed_ms"] <= 0 or row["nps"] <= 0:
            return False
        if row["output"] and parse_benchmark_nps(row["output"], summary_only=True) != row["nps"]:
            return False
    connection.executemany(
        """INSERT INTO tournament_clock_profiles
               (tournament_id, hardware_key, version_id, build_hash, artifact_sha256, nps)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT DO NOTHING""",
        [(tournament.id, key, row["version_id"], row["build_hash"], row["artifact_sha256"], row["nps"]) for row in rows],
    )
    return True


def pin_clock_profile(connection, tournament, key, engines):
    if any(engine is None or engine.distribution != "managed" or engine.artifact is None for engine in engines.values()):
        return None
    pinned = connection.execute(
        """SELECT version_id, build_hash, artifact_sha256, nps FROM tournament_clock_profiles
           WHERE tournament_id = ? AND hardware_key = ? AND version_id = ANY(?)""",
        (tournament.id, key, list(engines)),
    ).fetchall()
    missing = dict(engines)
    for row in pinned:
        engine = engines[row["version_id"]]
        if row["nps"] <= 0 or (row["build_hash"], row["artifact_sha256"]) != (
            engine.build_hash, engine.artifact.sha256,
        ):
            return None
        missing.pop(row["version_id"])
    if not missing:
        return _profile(connection, tournament, key, engines)
    rows = connection.execute(
        """SELECT build.id AS version_id, build.build_hash, build.artifact_sha256,
                  benchmark.nps, benchmark.output, benchmark.elapsed_ms
           FROM unnest(?::bigint[], ?::text[], ?::text[]) AS build(id, build_hash, artifact_sha256)
           JOIN engine_benchmarks benchmark
             ON benchmark.build_hash = build.build_hash AND benchmark.artifact_sha256 = build.artifact_sha256
           WHERE benchmark.hardware_key = ?""",
        (list(missing), [engine.build_hash for engine in missing.values()],
         [engine.artifact.sha256 for engine in missing.values()], key),
    ).fetchall()
    if not _pin_verified_profile(connection, tournament, key, rows, len(missing)):
        return None
    return _profile(connection, tournament, key, engines)


def clock_reference_nps(connection, tournament, engines):
    key = tournament.config.clock_reference_hardware_key
    return None if key is None else _profile(connection, tournament, key, engines)


def calibrate_game(connection, tournament, game, worker, engines, hardware_scores) -> GameClockCalibration | None:
    reference_key = tournament.config.clock_reference_hardware_key
    if reference_key is None or worker.machine_id is None or worker.hw is None:
        return None
    worker_key = benchmark_hardware_key(worker.machine_id, worker.hw)
    reference = clock_reference_nps(connection, tournament, engines)
    if reference is None:
        return None
    base = tournament.config.time_control
    scores = []
    for engine_id in (game.white_engine_id, game.black_engine_id):
        score = hardware_scores.get(engine_id)
        reference_nps = reference[engine_id]
        if score is None or score.benchmark_nps != reference_nps or score.worker_nps <= 0:
            return None
        scores.append((reference_nps, score.worker_nps))
    (white_reference, white_worker), (black_reference, black_worker) = scores
    scale = _shared_scale(white_reference, white_worker, black_reference, black_worker)
    initial = _scaled_ms(getattr(base, "initial_ms", getattr(base, "move_time_ms", 0)), scale)
    base_increment = getattr(base, "increment_ms", 0)
    increment = _scaled_ms(base_increment, scale)
    if initial <= 0 or (base_increment and increment <= 0):
        return None
    if isinstance(base, MoveTimeControl):
        control = MoveTimeControl(move_time_ms=initial)
    elif isinstance(base, MovesToGoTimeControl):
        control = MovesToGoTimeControl(initial_ms=initial, moves_to_go=base.moves_to_go)
    else:
        control = IncrementTimeControl(initial_ms=initial, increment_ms=increment)
    white = SideClock(white_reference, white_worker, control)
    black = SideClock(black_reference, black_worker, control)
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
            getattr(white.control, "initial_ms", getattr(white.control, "move_time_ms", 0)),
            getattr(black.control, "initial_ms", getattr(black.control, "move_time_ms", 0)),
            getattr(white.control, "increment_ms", 0), getattr(black.control, "increment_ms", 0),
        ),
    )


def game_calibration(connection, game_id, assignment_key):
    return connection.execute(
        "SELECT * FROM game_clock_calibrations WHERE game_id = ? AND assignment_key = ?",
        (game_id, assignment_key),
    ).fetchone()


def applied_clock(calibration, side, base=None):
    if isinstance(base, MoveTimeControl):
        return MoveTimeControl(move_time_ms=calibration[f"{side}_initial_ms"])
    if isinstance(base, MovesToGoTimeControl):
        return MovesToGoTimeControl(initial_ms=calibration[f"{side}_initial_ms"], moves_to_go=base.moves_to_go)
    return IncrementTimeControl(
        initial_ms=calibration[f"{side}_initial_ms"],
        increment_ms=calibration[f"{side}_increment_ms"],
    )


def resumption_clock_scale(previous_scale, current_scale: Fraction) -> Fraction:
    if not isinstance(previous_scale, list) or len(previous_scale) != 2 or any(
        not isinstance(value, int) or value <= 0 for value in previous_scale
    ):
        raise RuntimeError("Paused hardware-scaled game is missing its shared clock calibration.")
    return current_scale / Fraction(*previous_scale)


def restore_scaled_clock(clock_state, scale: Fraction):
    field = "remaining_time" if clock_state.get("remaining_time") is not None else "remaining_move_time"
    remaining = clock_state.get(field)
    if not isinstance(remaining, int) or remaining <= 0:
        raise RuntimeError("Paused hardware-scaled game has an invalid clock.")
    return {
        **clock_state,
        field: max(1, _scaled_ms(remaining, scale)),
    }
