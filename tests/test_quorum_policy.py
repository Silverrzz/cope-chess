import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cope.domains.quorum import Witness
from cope.domains.quorum_elections import canonical, digest
from cope.domains.quorum_history import parse_policy_chain, recommended_policy, verify_policy_chain


class WitnessPolicyTests(unittest.TestCase):
    def test_ballot_approval_requires_the_current_roster_and_a_fresh_ballot_id(self):
        origin = "https://cope.test"
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes_raw().hex()
        policy = {"threshold": 1, "operators": {"test": public}}
        bundle = {"initial": policy, "changes": []}
        roster = "a" * 64
        record = {"id": "d222cd5e-91c1-4ed7-9313-33a200002a11", "manifest": "b" * 64, "roster": roster}

        def append(approval):
            _, heads = verify_policy_chain(bundle, origin)
            proposal = digest(["cope-quorum-governance/1", digest(policy), approval])
            change = {"policy": policy, "round": 0, "approval": approval}
            for field, phase in (("commits", "commit"), ("stored", "store"), ("ready", "ready")):
                message = canonical([f"cope-quorum-policy-{phase}/1", origin, heads[-1], 0, proposal])
                change[field] = {public: key.sign(message).hex()}
            return {**bundle, "changes": [*bundle["changes"], change]}

        with self.assertRaisesRegex(ValueError, "non-current"):
            parse_policy_chain(append({"ballot": record}), origin)
        bundle = append({"roster": roster})
        epoch = verify_policy_chain(bundle, origin)[1][-1]
        bundle = append({"ballot": record})
        _, _, active_roster, ballots, _ = parse_policy_chain(bundle, origin)
        self.assertEqual(active_roster, roster)
        self.assertEqual(ballots[record["id"]], {**record, "epoch": epoch})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_policy_chain(append({"ballot": {**record, "manifest": "c" * 64}}), origin)

    def test_successor_must_be_safe_even_with_unanimous_approval(self):
        origin = "https://cope.test"
        keys = [Ed25519PrivateKey.generate() for _ in range(7)]
        operators = {f"witness-{i}": key.public_key().public_bytes_raw().hex() for i, key in enumerate(keys)}
        current = {"threshold": 3, "max_faulty": 1, "operators": dict(list(operators.items())[:4])}
        _, (head,) = verify_policy_chain(current, origin)

        def approved(policy):
            certificates = {}
            for field, phase in (("commits", "commit"), ("stored", "store"), ("ready", "ready")):
                message = canonical([f"cope-quorum-policy-{phase}/1", origin, head, 0, digest(policy)])
                selected = keys[:policy["threshold"] if phase == "ready" else 4]
                certificates[field] = {
                    operators[f"witness-{i}"]: key.sign(message).hex() for i, key in enumerate(selected)
                }
            return {"initial": current, "changes": [{"policy": policy, "round": 0, **certificates}]}

        unsafe = {**current, "threshold": 1, "max_faulty": 0}
        with self.assertRaisesRegex(ValueError, "honest overlap"):
            verify_policy_chain(approved(unsafe), origin)

        safe = {"threshold": 5, "max_faulty": 2, "operators": operators}
        not_ready = approved(safe)
        not_ready["changes"][0]["ready"].pop(operators["witness-4"])
        with self.assertRaisesRegex(ValueError, "threshold"):
            verify_policy_chain(not_ready, origin)
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
