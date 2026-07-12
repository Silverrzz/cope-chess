from __future__ import annotations

import unittest

from cope.core.models import WorkerPoolSlotHello, WorkerSessionHello
from cope.worker.client import (
    WorkerClientConfig,
    _WorkerConnectionState,
    _build_hello,
    _connection_config,
)


class WorkerClientCredentialTests(unittest.TestCase):
    def test_pool_slot_uses_bootstrap_credential_before_connecting(self) -> None:
        config = WorkerClientConfig(
            server_url="wss://cope.invalid/worker",
            app_version="test",
            pool_slot_token="pool-slot-token",
            machine_id="machine-1234",
        )

        connection_config = _connection_config(
            config,
            _WorkerConnectionState(session_id=None),
        )

        self.assertIsInstance(_build_hello(connection_config), WorkerPoolSlotHello)

    def test_pool_slot_uses_indefinite_session_after_connecting(self) -> None:
        config = WorkerClientConfig(
            server_url="wss://cope.invalid/worker",
            app_version="test",
            pool_slot_token="pool-slot-token",
            machine_id="machine-1234",
        )

        connection_config = _connection_config(
            config,
            _WorkerConnectionState(session_id="durable-session"),
        )

        hello = _build_hello(connection_config)
        self.assertIsInstance(hello, WorkerSessionHello)
        self.assertEqual(hello.session_id, "durable-session")
        self.assertIsNone(connection_config.pool_slot_token)


if __name__ == "__main__":
    unittest.main()
