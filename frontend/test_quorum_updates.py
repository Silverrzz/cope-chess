"""Run with: python -m unittest discover -s frontend -p 'test_quorum_updates.py'."""
import base64
import hashlib
import json
import stat
import subprocess
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from quorum_updates import FORMAT, REQUIRED, ReleaseError, load_policy, unpack, verify_manifest


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.public_keys = {}
        for name in ("one", "two", "outsider"):
            key = self.root / (name + ".pem")
            subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(key)], check=True)
            der = subprocess.check_output(["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"])
            self.public_keys[name] = der[-32:].hex()
        self.policy = {"channel": "stable", "threshold": 2, "minimum_sequence": 1,
                       "manifest_url": "https://example.test/manifest.json",
                       "keys": {name: self.public_keys[name] for name in ("one", "two")}}
        self.now = int(time.time())
        self.manifest = {"format": FORMAT, "channel": "stable", "sequence": 1, "commit": "c" * 40,
                         "published_at": self.now - 1, "expires_at": self.now + 60, "min_node_major": 24,
                         "artifact": {"size": 5, "sha256": "a" * 64}}

    def signed(self, *names, **values):
        payload = json.dumps(self.manifest | values).encode()
        statement = self.root / "payload"
        statement.write_bytes(payload)
        signatures = {name: subprocess.check_output(
            ["openssl", "pkeyutl", "-sign", "-inkey", str(self.root / (name + ".pem")),
             "-rawin", "-in", str(statement)]).hex() for name in names}
        return {"payload": base64.b64encode(payload).decode(), "signatures": signatures}

    def test_requires_distinct_pinned_signers_and_signed_payload(self):
        for envelope in (self.signed(), self.signed("one"), self.signed("one", "outsider")):
            with self.assertRaisesRegex(ReleaseError, "required signatures"):
                verify_manifest(envelope, self.policy, 24, now=self.now)
        envelope = self.signed("one", "two")
        self.assertEqual(verify_manifest(envelope, self.policy, 24, now=self.now)["signers"], ["one", "two"])
        envelope["payload"] = self.signed(sequence=2)["payload"]
        with self.assertRaisesRegex(ReleaseError, "required signatures"):
            verify_manifest(envelope, self.policy, 24, now=self.now)
        policy = self.root / "policy.json"
        policy.write_text(json.dumps(self.policy | {"keys": {"one": self.public_keys["one"], "two": self.public_keys["one"]}}))
        with self.assertRaisesRegex(ReleaseError, "distinct"):
            load_policy(policy)

    def test_expired_or_incompatible_manifest_is_not_approvable(self):
        for values, message in (({"expires_at": self.now}, "expired"), ({"channel": "other"}, "channel"),
                                ({"min_node_major": 25}, "incompatible")):
            with self.assertRaisesRegex(ReleaseError, message):
                verify_manifest(self.signed("one", "two", **values), self.policy, 24, now=self.now)
        active = self.signed("one", "two", expires_at=self.now)
        self.assertEqual(verify_manifest(active, self.policy, 24, fresh=False)["sequence"], 1)

    def test_older_sequence_is_rejected_before_downloading(self):
        import runpy
        discover = runpy.run_path(str(Path(__file__).with_name("quorum-supervisor.py")))["discover"]
        with patch.dict(discover.__globals__, read_feed=lambda _policy: self.signed("one", "two", sequence=2)):
            with self.assertRaisesRegex(ReleaseError, "allowed sequence"):
                discover(self.root, self.policy, 24, {"digest": "a" * 64, "sequence": 3})

    def test_artifact_integrity_and_safe_layout(self):
        archive = self.root / "release.zip"
        for path, mode in (("node_modules/group.js", stat.S_IFREG), ("../outside", stat.S_IFREG),
                           ("node_modules/group.js", stat.S_IFLNK), ("node_modules/file:stream", stat.S_IFREG)):
            with self.subTest(path=path, mode=mode):
                with zipfile.ZipFile(archive, "w") as zipped:
                    item = zipfile.ZipInfo(path)
                    item.external_attr = (mode | 0o644) << 16
                    zipped.writestr(item, b"module")
                    for filename in sorted(REQUIRED):
                        zipped.writestr(filename, b"source")
                content = archive.read_bytes()
                metadata = {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
                with tempfile.TemporaryDirectory(dir=self.root) as output:
                    with self.assertRaisesRegex(ReleaseError, "signed size and SHA-256"):
                        unpack(archive, Path(output), metadata | {"sha256": "0" * 64})
                    if path == "node_modules/group.js" and mode == stat.S_IFREG:
                        unpack(archive, Path(output), metadata)
                        self.assertEqual((Path(output) / path).read_bytes(), b"module")
                    else:
                        with self.assertRaisesRegex(ReleaseError, "unsafe"):
                            unpack(archive, Path(output), metadata)
        self.assertFalse((self.root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
