import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cope.domains.quorum import Witness
from cope.domains.quorum_elections import canonical, digest
from cope.domains.quorum_history import recommended_policy, verify_policy_chain


class WitnessPolicyTests(unittest.TestCase):
    def test_successor_must_be_safe_even_with_unanimous_approval(self):
        origin = "https://cope.test"
        keys = [Ed25519PrivateKey.generate() for _ in range(7)]
        operators = {f"witness-{i}": key.public_key().public_bytes_raw().hex() for i, key in enumerate(keys)}
        current = {"threshold": 3, "max_faulty": 1, "operators": dict(list(operators.items())[:4])}
        _, (head,) = verify_policy_chain(current, origin)

        def approved(policy):
            certificates = {}
            for field, phase in (("commits", "commit"), ("stored", "store")):
                message = canonical([f"cope-quorum-policy-{phase}/1", origin, head, 0, digest(policy)])
                certificates[field] = {
                    operators[f"witness-{i}"]: key.sign(message).hex() for i, key in enumerate(keys[:4])
                }
            return {"initial": current, "changes": [{"policy": policy, "round": 0, **certificates}]}

        unsafe = {**current, "threshold": 1, "max_faulty": 0}
        with self.assertRaisesRegex(ValueError, "honest overlap"):
            verify_policy_chain(approved(unsafe), origin)

        safe = {"threshold": 5, "max_faulty": 2, "operators": operators}
        actual, heads = verify_policy_chain(approved(safe), origin)
        self.assertEqual(actual, safe)
        self.assertEqual(len(heads), 2)

    def test_recommendations_include_staged_nodes_and_keep_existing_operator_ids(self):
        keys = [f"{i:064x}" for i in range(1, 9)]
        current = {"threshold": 3, "max_faulty": 1, "operators": {f"node-{i}": key for i, key in enumerate(keys[:4], 1)}}
        witnesses = [Witness(public_key=key, name="Node 1", enabled=i < 4) for i, key in enumerate(keys)]

        for size, threshold, fault in [(5, 4, 1), (7, 5, 2)]:
            with self.subTest(size=size):
                proposal = recommended_policy(witnesses, keys[:size], current)
                self.assertEqual((proposal["threshold"], proposal["max_faulty"]), (threshold, fault))
                self.assertEqual(set(proposal["operators"].values()), set(keys[:size]))
                for operator, key in current["operators"].items():
                    self.assertEqual(proposal["operators"][operator], key)
                self.assertEqual(recommended_policy(witnesses[::-1], keys[:size][::-1], current), proposal)

        with self.assertRaisesRegex(ValueError, "no longer registered"):
            recommended_policy(witnesses, [keys[0], "f" * 64], current)
