from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from cope.core.models import EngineSpec
from cope.worker.uci_engine import EnginePreparationError, UciEngineProcess


def engine_spec() -> EngineSpec:
    return EngineSpec(
        engine_id=1,
        name="Test Engine",
        git_url="https://example.invalid/engine.git",
        commit="a" * 40,
        build_cmd="make",
        binary_path="bin/engine",
    )


class MachineEngineCacheTests(unittest.TestCase):
    def test_concurrent_slots_build_exact_engine_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            clone_count = 0
            count_lock = threading.Lock()

            def fake_run(command, *, cwd, shell=False):
                nonlocal clone_count
                if isinstance(command, list) and command[:2] == ["git", "clone"]:
                    with count_lock:
                        clone_count += 1
                    checkout = Path(command[-1])
                    (checkout / "bin").mkdir(parents=True)
                    (checkout / "bin" / "engine").write_text("engine", encoding="utf-8")
                    time.sleep(0.05)

            with (
                patch.dict(os.environ, {"COPE_WORKER_ENGINE_DIR": temporary_root}),
                patch("cope.worker.uci_engine._run_checked", side_effect=fake_run),
            ):
                engines = [UciEngineProcess(engine_spec()) for _ in range(32)]
                with ThreadPoolExecutor(max_workers=32) as executor:
                    list(executor.map(lambda engine: engine.prepare(), engines))

                self.assertEqual(clone_count, 1)
                self.assertEqual(len({engine._source_dir for engine in engines}), 1)
                self.assertTrue(engines[0]._binary_path.is_file())

    def test_concurrent_slots_share_recent_build_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            attempt_count = 0
            count_lock = threading.Lock()

            def failed_run(command, *, cwd, shell=False):
                nonlocal attempt_count
                if isinstance(command, list) and command[:2] == ["git", "clone"]:
                    with count_lock:
                        attempt_count += 1
                    time.sleep(0.05)
                    raise RuntimeError("expected build failure")

            def prepare_and_capture(engine: UciEngineProcess) -> str:
                try:
                    engine.prepare()
                except EnginePreparationError as error:
                    return str(error)
                self.fail("engine preparation unexpectedly succeeded")

            with (
                patch.dict(os.environ, {"COPE_WORKER_ENGINE_DIR": temporary_root}),
                patch("cope.worker.uci_engine._run_checked", side_effect=failed_run),
            ):
                engines = [UciEngineProcess(engine_spec()) for _ in range(32)]
                with ThreadPoolExecutor(max_workers=32) as executor:
                    errors = list(executor.map(prepare_and_capture, engines))

                self.assertEqual(attempt_count, 1)
                self.assertTrue(all("expected build failure" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
