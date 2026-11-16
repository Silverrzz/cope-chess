from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
from math import prod
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

P = int(
    "ffffffffffffffffadf85458a2bb4a9aafdc5620273d3cf1"
    "d8b9c583ce2d3695a9e13641146433fbcc939dce249b3ef9"
    "7d2fe363630c75d8f681b202aec4617ad3df1ed5d5fd6561"
    "2433f51f5f066ed0856365553ded1af3b557135e7f57c935"
    "984f0c70e0e68b77e2a689daf3efe8721df158a136ade735"
    "30acca4f483a797abc0ab182b324fb61d108a94bb2c8e3fb"
    "b96adab760d7f4681d4f42a3de394df4ae56ede76372bb19"
    "0b07a7c8ee0a6d709e02fce1cdf7e2ecc03404cd28342f61"
    "9172fe9ce98583ff8e4f1232eef28183c3fe3b1b4c6fad73"
    "3bb5fcbc2ec22005c58ef1837d1683b2c6f34a26c1b2effa"
    "886b423861285c97ffffffffffffffff",
    16,
)
Q = (P - 1) // 2
G = 2
PROTOCOL = "cope-secret-ballot-v1"
MAX_VOTERS = 200
MAX_QUESTIONS = 4
MAX_OPTIONS = 6
MIN_VOTERS = 3


def encode(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def number(value: int) -> str:
    return format(value, "x")


def parse_number(value: str, limit: int) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9a-f][0-9a-f]{0,511}", value):
        raise ValueError("Invalid cryptographic encoding.")
    result = int(value, 16)
    if result >= limit:
        raise ValueError("Cryptographic value is out of range.")
    return result


def element(value: str, *, nonidentity: bool = False) -> int:
    result = parse_number(value, P)
    if result == 0 or nonidentity and result == 1 or pow(result, Q, P) != 1:
        raise ValueError("Invalid group element.")
    return result


def secret_scalar() -> int:
    return secrets.randbelow(Q - 1) + 1


def challenge(context, *elements: int) -> int:
    return int(digest([PROTOCOL, context, *map(number, elements)]), 16)


def proof_commitments(base: int, public: int, factor: int, c: int, z: int) -> tuple[int, int]:
    return pow(G, z, P) * pow(public, -c, P) % P, pow(base, z, P) * pow(factor, -c, P) % P


def prove_equal(context, base: int, public: int, factor: int, secret: int) -> list[str]:
    nonce = secret_scalar()
    c = challenge(context, base, public, factor, pow(G, nonce, P), pow(base, nonce, P))
    return [number(c), number((nonce + c * secret) % Q)]


def verify_equal(context, base: int, public: int, factor: int, proof) -> None:
    c, z = parse_number(proof[0], 1 << 256), parse_number(proof[1], Q)
    commitments = proof_commitments(base, public, factor, c, z)
    if c != challenge(context, base, public, factor, *commitments):
        raise ValueError("A zero-knowledge proof failed verification.")


def trustee_key(slug: str, secret: int) -> dict:
    public = pow(G, secret, P)
    return {"public": number(public), "proof": prove_equal(["trustee", slug], G, public, public, secret)}


def election_key(slug: str, trustees: list[dict]) -> int:
    if len(trustees) != 2 or len({entry["public"] for entry in trustees}) != 2:
        raise ValueError("Use two distinct trustee keys.")
    public_keys = []
    for entry in trustees:
        public = element(entry["public"], nonidentity=True)
        verify_equal(["trustee", slug], G, public, public, entry["proof"])
        public_keys.append(public)
    combined = prod(public_keys) % P
    if combined == 1:
        raise ValueError("The trustee keys cancel each other out.")
    return combined


def proof_for_selection(context, key: int, alpha: int, beta: int, bit: int, secret: int) -> list[str]:
    other = 1 - bit
    challenges = [0, 0]
    responses = [0, 0]
    commitments = [(0, 0), (0, 0)]
    nonce = secret_scalar()
    challenges[other] = secrets.randbelow(Q)
    responses[other] = secrets.randbelow(Q)
    commitments[other] = proof_commitments(key, alpha, beta * pow(G, -other, P) % P,
                                           challenges[other], responses[other])
    commitments[bit] = pow(G, nonce, P), pow(key, nonce, P)
    total = challenge(context, key, alpha, beta, *commitments[0], *commitments[1])
    challenges[bit] = (total - challenges[other]) % Q
    responses[bit] = (nonce + challenges[bit] * secret) % Q
    return list(map(number, [*challenges, *responses]))


def verify_selection(context, key: int, alpha: int, beta: int, proof) -> None:
    c0, c1, z0, z1 = (parse_number(value, Q) for value in proof)
    first = proof_commitments(key, alpha, beta, c0, z0)
    second = proof_commitments(key, alpha, beta * pow(G, -1, P) % P, c1, z1)
    if (c0 + c1) % Q != challenge(context, key, alpha, beta, *first, *second):
        raise ValueError("A selection is missing a valid zero-knowledge proof.")


def ballot_message(ballot: dict) -> bytes:
    return encode([PROTOCOL, "ballot", ballot["manifest"], ballot["credential"], ballot["questions"]])


def receipt_message(manifest_id: str, fingerprint: str) -> bytes:
    return encode([PROTOCOL, "accepted", manifest_id, fingerprint])


def verify_signature(public: str, signature: str, message: bytes) -> None:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(bytes.fromhex(signature), message)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("A ballot or receipt signature failed verification.") from exc


def verify_manifest(manifest: dict) -> str:
    roster = manifest["roster"]
    if (manifest["protocol"] != PROTOCOL or roster != sorted(set(roster))
            or not MIN_VOTERS <= manifest["minimum"] <= len(roster) <= MAX_VOTERS
            or any(not re.fullmatch(r"[a-f0-9]{64}", public) for public in roster)
            or not re.fullmatch(r"[a-f0-9]{64}", manifest["receipt_key"])
            or not 1 <= len(manifest["questions"]) <= MAX_QUESTIONS
            or any(not 2 <= len(question["options"]) <= MAX_OPTIONS for question in manifest["questions"])):
        raise ValueError("Invalid ballot manifest.")
    if number(election_key(manifest["slug"], manifest["trustees"])) != manifest["key"]:
        raise ValueError("The ballot encryption key does not match the trustees.")
    return digest(manifest)


def make_ballot(manifest: dict, private: Ed25519PrivateKey, choices: list[int]) -> tuple[dict, list]:
    manifest_id = verify_manifest(manifest)
    credential = private.public_key().public_bytes_raw().hex()
    if credential not in manifest["roster"]:
        raise ValueError("This voter proof was not registered for the form.")
    if len(choices) != len(manifest["questions"]):
        raise ValueError("Choose one option for each question.")
    key = element(manifest["key"], nonidentity=True)
    questions = []
    openings = []
    for index, (question, choice) in enumerate(zip(manifest["questions"], choices, strict=True)):
        if not 0 <= choice < len(question["options"]):
            raise ValueError("Choose a listed option for each question.")
        context = ["selection", manifest_id, credential, index]
        cells = []
        secrets_for_question = []
        alpha, beta = 1, 1
        for option in range(len(question["options"])):
            secret = secret_scalar()
            bit = int(option == choice)
            a, b = pow(G, secret, P), pow(key, secret, P) * G ** bit % P
            cells.append([number(a), number(b), proof_for_selection([*context, option], key, a, b, bit, secret)])
            secrets_for_question.append(secret)
            alpha, beta = alpha * a % P, beta * b % P
        proof = prove_equal([*context, "exactly-one"], key, alpha, beta * pow(G, -1, P) % P,
                            sum(secrets_for_question) % Q)
        questions.append({"cells": cells, "proof": proof})
        openings.append([choice, list(map(number, secrets_for_question))])
    ballot = {"manifest": manifest_id, "credential": credential, "questions": questions}
    ballot["signature"] = private.sign(ballot_message(ballot)).hex()
    return ballot, openings


def verify_ballot_signature(manifest: dict, manifest_id: str, ballot: dict) -> None:
    if ballot["manifest"] != manifest_id or ballot["credential"] not in manifest["roster"]:
        raise ValueError("This voter proof was not registered for this version of the form.")
    verify_signature(ballot["credential"], ballot["signature"], ballot_message(ballot))


def verify_encrypted_choices(manifest: dict, manifest_id: str, ballot: dict) -> None:
    questions = ballot["questions"]
    if len(questions) != len(manifest["questions"]):
        raise ValueError("The ballot does not match the published questions.")
    key = parse_number(manifest["key"], P)
    for index, (question, definition) in enumerate(zip(questions, manifest["questions"], strict=True)):
        cells = question["cells"]
        if len(cells) != len(definition["options"]):
            raise ValueError("The ballot does not match the published options.")
        context = ["selection", manifest_id, ballot["credential"], index]
        encrypted = []
        for option, (a, b, proof) in enumerate(cells):
            alpha, beta = element(a, nonidentity=True), element(b)
            verify_selection([*context, option], key, alpha, beta, proof)
            encrypted.append((alpha, beta))
        alpha = prod(a for a, _ in encrypted) % P
        beta = prod(b for _, b in encrypted) % P
        verify_equal([*context, "exactly-one"], key, alpha, beta * pow(G, -1, P) % P, question["proof"])



def check_receipt(manifest: dict, manifest_id: str, fingerprint: str, signature: str) -> None:
    verify_signature(manifest["receipt_key"], signature, receipt_message(manifest_id, fingerprint))


def board_digest(manifest_id: str, fingerprints: list[str]) -> str:
    return digest([PROTOCOL, "closed-board", manifest_id, sorted(fingerprints)])


def aggregate(manifest: dict, ballots: list[dict]) -> list[list[tuple[int, int]]]:
    sums = [[(1, 1) for _ in question["options"]] for question in manifest["questions"]]
    for entry in ballots:
        for row, answer in zip(sums, entry["ballot"]["questions"], strict=True):
            for index, (a, b, _) in enumerate(answer["cells"]):
                alpha, beta = row[index]
                row[index] = alpha * int(a, 16) % P, beta * int(b, 16) % P
    return sums


def check_board(board: dict, manifest_id: str, fingerprint: str) -> None:
    manifest = board["manifest"]
    if verify_manifest(manifest) != manifest_id or board["board_digest"] != fingerprint:
        raise ValueError("The manifest or closed board does not match your expected fingerprint.")
    seen = set()
    fingerprints = []
    for entry in board["ballots"]:
        ballot = entry["ballot"]
        if ballot["credential"] in seen:
            raise ValueError("The board contains duplicate voter proofs.")
        seen.add(ballot["credential"])
        verify_ballot_signature(manifest, manifest_id, ballot)
        verify_encrypted_choices(manifest, manifest_id, ballot)
        receipt = digest(ballot)
        check_receipt(manifest, manifest_id, receipt, entry["signature"])
        fingerprints.append(receipt)
    if len(seen) != board["ballot_count"] or board_digest(manifest_id, fingerprints) != fingerprint:
        raise ValueError("The closed board is incomplete or has been changed.")


def make_share(board: dict, secret: int) -> dict:
    manifest = board["manifest"]
    trustee = number(pow(G, secret, P))
    if trustee not in {entry["public"] for entry in manifest["trustees"]}:
        raise ValueError("This trustee key does not belong to this form.")
    if not manifest["minimum"] <= len(board["ballots"]):
        raise ValueError("Too few ballots to release a tally.")
    fingerprint = board["board_digest"]
    factors = []
    for row, question in enumerate(aggregate(manifest, board["ballots"])):
        factors.append([])
        for col, (alpha, _) in enumerate(question):
            factor = pow(alpha, secret, P)
            proof = prove_equal(["tally", fingerprint, row, col], alpha, int(trustee, 16), factor, secret)
            factors[-1].append([number(factor), proof])
    return {"trustee": trustee, "board_digest": fingerprint, "factors": factors}


def check_share(board: dict, share: dict, sums: list[list[tuple[int, int]]]) -> None:
    if share["board_digest"] != board["board_digest"] or share["trustee"] not in {
        entry["public"] for entry in board["manifest"]["trustees"]
    }:
        raise ValueError("This tally share belongs to a different trustee or closed board.")
    factors = share["factors"]
    if len(sums) != len(factors) or any(len(row) != len(values) for row, values in zip(sums, factors, strict=True)):
        raise ValueError("This tally share does not match the form.")
    trustee = int(share["trustee"], 16)
    for row, question in enumerate(sums):
        for col, (alpha, _) in enumerate(question):
            factor, proof = factors[row][col]
            verify_equal(["tally", board["board_digest"], row, col], alpha, trustee, element(factor), proof)


def tally(board: dict, shares: list[dict], sums: list[list[tuple[int, int]]]) -> list[list[int]]:
    manifest = board["manifest"]
    count = len(board["ballots"])
    if count < manifest["minimum"] or len(shares) != 2 or {
        share["trustee"] for share in shares
    } != {entry["public"] for entry in manifest["trustees"]}:
        raise ValueError("Tallying requires the publication minimum and both trustee shares.")
    powers = {pow(G, count, P): count for count in range(count + 1)}
    results = []
    for row, question in enumerate(sums):
        counts = []
        for col, (_, beta) in enumerate(question):
            factor = prod(int(share["factors"][row][col][0], 16) for share in shares) % P
            value = beta * pow(factor, -1, P) % P
            if value not in powers:
                raise ValueError("A tally count lies outside the size of the electorate.")
            counts.append(powers[value])
        if sum(counts) != count:
            raise ValueError("A question's totals do not match the number of accepted ballots.")
        results.append(counts)
    return results


def check_opening(manifest: dict, ballot: dict, openings: list) -> list[str]:
    key = parse_number(manifest["key"], P)
    labels = []
    for definition, question, (choice, secrets_for_question) in zip(
        manifest["questions"], ballot["questions"], openings, strict=True
    ):
        if type(choice) is not int or not 0 <= choice < len(definition["options"]):
            raise ValueError("Invalid choice disclosed.")
        for option, ((a, b, _), encoded) in enumerate(zip(question["cells"], secrets_for_question, strict=True)):
            secret = parse_number(encoded, Q)
            if int(a, 16) != pow(G, secret, P) or int(b, 16) != pow(key, secret, P) * G ** (option == choice) % P:
                raise ValueError("The disclosed choices do not open this ballot.")
        labels.append(definition["options"][choice])
    return labels


def read(path: str) -> dict:
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def write(path: str, value: dict) -> None:
    with open(path, "x", encoding="utf-8", opener=lambda filename, flags: os.open(filename, flags, 0o600)) as stream:
        json.dump(value, stream, ensure_ascii=False)


def verify_archive(board: dict, manifest_id: str, fingerprint: str, receipt: dict | None) -> None:
    check_board(board, manifest_id, fingerprint)
    shares = board["shares"]
    if shares or board["results"] is not None:
        sums = aggregate(board["manifest"], board["ballots"])
        for share in shares:
            check_share(board, share, sums)
        if board["results"] is not None:
            if tally(board, shares, sums) != board["results"]:
                raise ValueError("Published results do not match the verified tally.")
            print("Verified tally:", board["results"])
    if receipt is None:
        return
    if receipt["manifest"] != manifest_id:
        raise ValueError("This receipt belongs to a different manifest.")
    check_receipt(board["manifest"], manifest_id, receipt["fingerprint"], receipt["signature"])
    entry = next((item for item in board["ballots"] if digest(item["ballot"]) == receipt["fingerprint"]), None)
    if entry is None:
        raise ValueError("The signed acceptance receipt is missing from the published board.")
    print("Verified receipt inclusion:", receipt["fingerprint"])
    if "openings" in receipt:
        print("Verified disclosed choices:", check_opening(board["manifest"], entry["ballot"], receipt["openings"]))
        print("Enrollment code:", entry["ballot"]["credential"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and verify Cope secret ballot files offline.")
    commands = parser.add_subparsers(dest="command", required=True)
    trustee = commands.add_parser("trustee", help="Generate a private key and a public registration file.")
    trustee.add_argument("slug")
    trustee.add_argument("private_file")
    trustee.add_argument("public_file")
    voter = commands.add_parser("voter", help="Create a private voter proof and print its public enrollment code.")
    voter.add_argument("slug")
    voter.add_argument("private_file")
    seal = commands.add_parser("seal", help="Create an encrypted ballot and a private opening locally.")
    seal.add_argument("manifest_file")
    seal.add_argument("expected_manifest")
    seal.add_argument("private_file")
    seal.add_argument("ballot_file")
    seal.add_argument("opening_file")
    share = commands.add_parser("share", help="Verify the closed board and create a tally share.")
    verify = commands.add_parser("verify", help="Verify a closed board, its tally and an optional receipt.")
    for command in (share, verify):
        command.add_argument("board_file")
        command.add_argument("expected_manifest")
        command.add_argument("expected_board")
    share.add_argument("private_file")
    share.add_argument("share_file")
    verify.add_argument("--receipt")
    args = parser.parse_args()

    try:
        if args.command == "trustee":
            secret = secret_scalar()
            write(args.private_file, {"slug": args.slug, "secret": number(secret)})
            write(args.public_file, trustee_key(args.slug, secret))
        elif args.command == "voter":
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes_raw().hex()
            private = "302e020100300506032b657004220420" + key.private_bytes_raw().hex()
            write(args.private_file, {"type": "cope-voter-proof-v1", "slug": args.slug, "public": public, "private": private})
            print(public)
        elif args.command == "seal":
            from cryptography.hazmat.primitives.serialization import load_der_private_key

            manifest = read(args.manifest_file)
            if verify_manifest(manifest) != args.expected_manifest:
                raise ValueError("The manifest does not match your expected fingerprint.")
            proof = read(args.private_file)
            if proof["slug"] != manifest["slug"]:
                raise ValueError("The voter proof belongs to a different form.")
            key = load_der_private_key(bytes.fromhex(proof["private"]), None)
            answers = []
            for question in manifest["questions"]:
                print(question["title"])
                for index, option in enumerate(question["options"], 1):
                    print(f"  {index}. {option}")
                answers.append(int(input("Option number: ")) - 1)
            ballot, openings = make_ballot(manifest, key, answers)
            write(args.ballot_file, ballot)
            write(args.opening_file, {"manifest": args.expected_manifest, "fingerprint": digest(ballot), "openings": openings})
        elif args.command == "share":
            board = read(args.board_file)
            check_board(board, args.expected_manifest, args.expected_board)
            key = read(args.private_file)
            if key["slug"] != board["manifest"]["slug"]:
                raise ValueError("The trustee key belongs to a different form.")
            write(args.share_file, make_share(board, parse_number(key["secret"], Q)))
        else:
            verify_archive(read(args.board_file), args.expected_manifest, args.expected_board,
                           read(args.receipt) if args.receipt else None)
            print("Verified board:", args.expected_board)
    except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
