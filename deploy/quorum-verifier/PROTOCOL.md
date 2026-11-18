# COPE Quorum protocol

COPE Quorum lets voters prove that they belong to an approved electorate without putting their account or credential commitment in the vote. An independent verifier quorum approves the electorate and exact ballot, accepts and retains anonymous votes, and witnesses the history that COPE uses to compute the tally. An auditor can verify a final result from exported evidence and a separately trusted witness policy.

COPE still runs enrollment, publishes ballots, orders the votes, and collects checkpoints. It can delay or withhold a result. Within the configured fault limit, it cannot produce a valid final seal that forks a previously witnessed history or omits a vote for which the voter obtained a retention quorum. Evidence without a final seal remains provisional.

## Participants and trust

| Participant | Role and trust |
| --- | --- |
| COPE and its administrators | Check Discord eligibility and register public commitments; propose rosters and ballots; publish the ordered history, proofs and tally. They do not choose which keys an independent auditor trusts. |
| Independent witness operators | Control distinct Ed25519 keys, review governance proposals, and keep durable signing and intake state. Their pinned policy determines which keys count and how many are required. |
| Voters | Keep a Semaphore credential and its encrypted backup. They choose a trusted independent verifier site and submit the same anonymous proof directly to assigned verifiers, without a COPE login. |
| Auditors | Bring the expected COPE HTTPS origin and a recent, independently authenticated witness policy bundle. They can verify evidence without trusting the site that distributes it. |
| Verifier release signers and local administrators | Separately authorize server software under locally provisioned release pins and explicit local approval. The witness policy instead approves the browser voting release. |

### Quorum intersection

A witness policy names between one and 64 operators, each with a distinct raw Ed25519 public key. Let `n` be the number of operators, `t` the signature threshold, and `f` the maximum number of faulty operators, including those that equivocate. Two sets of `t` signers overlap in at least `2t - n` keys. The policy rejects `2t - n <= f`: the overlap must contain at least one honest signer. If `max_faulty` is omitted, the policy uses the largest value allowed by that inequality. To still form a quorum when all `f` faulty operators withhold signatures, also choose `t <= n - f`. This second condition does not guarantee that the network, COPE, or the remaining operators will make progress.

| Policy | What follows |
| --- | --- |
| One of four | Rejected even at `f = 0`; two quorums may be disjoint. |
| Three of four, `f = 1` | The overlap contains at least two signers; three remain if one withholds. |
| Five of seven, `f = 2` | The overlap contains at least three signers; five remain if two withhold. |
| Four of four, `f = 1` | Satisfies intersection safety, but any one operator can prevent a quorum. |

Distinct keys alone do not establish independence. Operators must protect their key and archive together, including policy journals, stored decisions, checkpoint journals and retained proofs. Running two copies of a signing identity, or restoring the key with stale state, can make that key equivocate. A signer that loses current state needs a confirmed current backup or replacement through the current policy before it continues signing. Synchronize the clocks used to enforce ballot deadlines.

## Statements and policy decisions

The protocol fingerprints JSON with SHA-256 over UTF-8 compact JSON with sorted object keys and unchanged array order. Digests and raw Ed25519 public keys use lowercase hex. Witnesses sign the UTF-8 compact JSON array for a statement directly with Ed25519; each array starts with a versioned domain tag. A certificate counts distinct authorized keys. The table uses `O` for the COPE origin, `H` for the current policy head, `R` for the round, `D` for the proposal digest, `B` for the ballot ID, `M` for the manifest fingerprint, and `V` for the full vote proof fingerprint.

| Statement | JSON array |
| --- | --- |
| Policy prepare, commit, ready, store | `["cope-quorum-policy-<phase>/1", O, H, R, D]`, with the literal phase substituted |
| Policy retry | `["cope-quorum-policy-retry/1", O, H, R, P]`, where `P` is null or `[earlier_round, earlier_proposal_digest]`; a reported prepare certificate accompanies the signature |
| Intake | `["cope-quorum-vote-intake/1", O, B, M, V]` |
| Retention | `["cope-quorum-vote-retained/1", O, B, M, V]` |
| Checkpoint | `["cope-quorum-history-checkpoint/1", O, B, M, phase, size, digest]`, where the phase is `"open"` or `"closed"` |
| Archive report | `["cope-quorum-witness-result/1", O, B, evidence_digest]`, an attestation to one ballot evidence packet |

The initial head is the fingerprint of `["cope-quorum-witness-policy-anchor/1", O, initial_policy_digest]`. For a membership change, `D` is the fingerprint of the complete proposed policy, including the threshold and fault allowance when present. A governance approval leaves the policy unchanged and sets `D` to the fingerprint of `["cope-quorum-governance/1", current_policy_digest, approval]`. The approval object is exactly one of `{"roster": roster_digest}`, `{"ballot": {"id": B, "manifest": M, "roster": roster_digest}}`, or `{"voter_release": release_digest}`. A successor head is the fingerprint of `["cope-quorum-witness-policy-change/2", O, H, current_policy_digest, D]`. The same decision at the same head gets the same successor head regardless of the round or which quorum signs.

### Decide and activate

1. **Prepare.** Starting at round zero, operators in the current policy inspect the exact proposal. Each signer persists its choice before returning a prepare signature and does not prepare a second proposal in that round.
2. **Commit.** A current operator checks a prepare quorum for the same head, round and digest, and persists that certificate before releasing a commit signature. The current threshold authorizes the proposal even if the successor uses different keys or a different threshold.
3. **Retry if needed.** A current quorum signs entry into a higher round. Each signer persists its promise to stop preparing or committing in earlier rounds and reports its latest persisted prepare certificate, if any. The new prepare round must carry forward the digest of the highest valid prepare certificate reported. Without one it may choose a new proposal. For example, two pairs of operators can leave a three-of-four round split 2–2 if none formed a prepare quorum. A hidden commit quorum cannot safely be bypassed: it overlaps the retry quorum in an honest signer who recorded the prepare certificate. An already formed commit quorum can still be taken through readiness and storage.
4. **Ready and store.** A quorum of the proposed operators, pinned to the current head, verifies the outgoing commit quorum and signs readiness. For governance, those are the same operator set and threshold as before. A quorum of current operators then checks both quorums and stores the decision before issuing storage receipts. Stored records include the commits and readiness signatures, and peers can fetch and verify a stored copy to help assemble the decision.
5. **Distribute and pin.** The approved bundle contains the outgoing commits and storage receipts and the incoming readiness signatures. Consumers recheck the policy rules and all three certificates at each link; prepare and retry records are local signing safeguards, not required in the published bundle. Assembly does not activate anything. Operators distribute the approved bundle and install it on COPE and the verifiers. COPE pins on governed publication, verifiers pin installed heads, and auditors can import a pin. A replacement bundle must contain the previously pinned head.

Each head is an epoch. An approved ballot uses the head immediately **before** its approval and the witness policy at that epoch. Later roster, browser release, and membership decisions advance the head without rewriting that ballot or changing the keys used to verify its history. Before storing a membership change, finish active and scheduled ballots under the old policy. A storing operator stops starting new ballots under the outgoing policy; it can finish ballots for which it already has a journal. For a ballot no witness has started, a storage quorum blocks an opening quorum at the outgoing head under the fault assumptions. The current bundle format supports up to 128 changes.

## From enrollment to an approved ballot

COPE checks the configured Discord role at enrollment, and the voter proves possession of a locally generated Semaphore credential. COPE records the account and its public commitment; the voter keeps the secret and its encrypted backup. When moving existing elections to the governed protocol, operators review and approve the existing public commitments as a roster. Voters keep their credentials and backups; migration does not ask them to disclose or regenerate secrets. The roster contains the ordered commitment list and its recomputable Semaphore group root. It contains no account names. The implementation supports up to 1,024 commitments.

A roster, an exact ballot, and a browser voting release are separate governance proposals:

- A **roster** approval binds the fingerprint of the public roster. Operators check its bytes and root and must also decide whether its membership is legitimate. The cryptographic root cannot show whether the listed commitments actually belong to eligible people.
- A **ballot** approval binds the unique ballot ID, exact manifest fingerprint, and current approved roster fingerprint. The manifest includes the question, rationale, options or decision rule, turnout requirement, opening and closing times, and assigned witness keys and addresses. Operators check the manifest and roster and require at least a quorum of distinct HTTPS verifier origins. COPE publishes only the matching draft and roster. Changing a field after approval changes the fingerprint; reusing a decided ballot ID is not permitted. Credential replacement affects future rosters when approved, and does not give the new credential access to an old roster.
- A **browser voting release** approval binds a release manifest fingerprint. It identifies the exact sizes and SHA-256 digests of the bundled `vote.js` and the depth-10 Semaphore WASM and proving key. The independent verifier serves only the release named by its approved chain, after checking all three files against that manifest.

The voter opens a top-level `/vote/<ballot-id>` page on an independently trusted verifier hostname. No app installation or COPE session is needed. The verifier serves the assets at URLs containing the release digest, and the browser checks the JavaScript with Subresource Integrity from the bootstrap page. The voting app checks the served chain, release ID, exact authorized ballot and roster before offering the form. It decrypts the backup in the browser and generates the proof there. These checks depend on the bootstrap: the site supplies both the integrity value and the policy bundle, and a malicious bootstrap could present a counterfeit form or steal an unlocked credential. The voter must authenticate the entry point independently of COPE.

## Intake, ordering and settlement

Let `T` be the signed voting deadline and `S = T + 120 seconds` the settlement boundary. A depth-10 Semaphore proof binds an option index to the approved roster root and the ballot scope (the fingerprint of `["cope-quorum-ballot/1", manifest]`, interpreted as a decimal integer). The per-scope nullifier allows duplicate votes to be detected without identifying the roster commitment used. The browser sends an identical proof on each attempt; generating a new proof, even for the same choice, changes the fingerprint.

```mermaid
sequenceDiagram
    participant V as Voter browser
    participant W as Assigned verifiers
    participant C as COPE
    participant A as Auditor
    V->>W: Same proof before voting closes
    W-->>V: Signed intake responses
    V->>W: Proof plus intake quorum
    W-->>V: Signed retention responses
    W->>C: Forward certified proofs through settlement
    C-->>W: Ordered hashes
    W->>C: Open checkpoints
    C-->>W: Final proofs, certificates and open quorum
    W->>C: Closed checkpoints after settlement
    C-->>A: Exportable evidence; also archived by verifiers
```

1. **Intake, before `T`.** The browser sends the proof to the assigned verifier origins without COPE credentials. Each verifier checks the exact authorized ballot and roster, the voting window and the proof. It stores its response under the ballot nullifier before returning a signature over the full proof hash. It rejects a different proof for the same ballot nullifier. An honest overlap therefore rules out two intake certificates for different proofs with that nullifier. The browser verifies the returned signatures against the assigned keys and assembles an intake certificate.
2. **Retention, before `S`.** The browser sends the proof plus the intake certificate back to the verifiers. Each verifies both, stores the certified proof before returning its retention signature, and tries to forward it to COPE. The browser reports retention only when it has verified both quorums. A retry can recover an already saved response for the same proof; it does not reopen intake for a new proof.
3. **Order and settle.** COPE verifies intake certificates against the frozen keys and appends one proof per nullifier to its ordered history. It accepts certified relays until `S`; the ordinary direct vote endpoint cannot add votes to a governed ballot. Before `T` the public history shows hashes, without anonymous choices or full proofs. From `T` onward the verifier peer endpoints may disclose retained certified proofs. During the settlement window, verifiers can fetch and validate those copies from peers and retry forwarding proofs. This recovery path does not elect an order or produce a tally independently of COPE.

### Open and closed checkpoints

For the ordered hash list `L`, the history digest is the fingerprint of `["cope-quorum-vote-history/1", L]`. From opening until `S`, a verifier can sign an **open** checkpoint for the exact supplied prefix. It persists its history and only signs extensions that retain the same manifest and hashes in the same positions. COPE presents the latest open prefix with quorum signatures, which may be shorter than the current list. Any two open quorums for the same authorized ballot have an honest signer in common, so their signed lists must be prefix-compatible.

At or after `S`, a verifier can **close** only a candidate extending its own journal and already carrying an open quorum for the *entire* candidate list. A verifier that is behind can catch up from that open quorum without making a new open signature. It can resend a previously stored open attestation if COPE did not record it. Before sealing, it checks the exact ordered proof hashes, each intake certificate, every proof, duplicate nullifiers, and the recomputed result. It refuses to seal if the candidate omits a certified proof it retains. It persists its seal before publishing it and does not sign another closed history.

A closed quorum therefore cannot omit an exact proof held by a retention quorum: their signer sets must overlap in at least one honest retainer that would refuse the seal. An intake quorum alone does not establish that enough verifiers persisted a copy *with* the certificate. A closed quorum also cannot conflict with a previously witnessed open prefix or another closed quorum under the same assumptions. If an eligible seal cannot be formed, the protocol leaves the result provisional.

## What an auditor can check

Use an independently authenticated bundle and the expected COPE origin. The verifier archive preserves checked evidence and distinguishes a first observed packet and later conflicts. A report signature on a packet is separate from the open and closed checkpoint certificates.

| Evidence | Verified claim |
| --- | --- |
| Retention receipt (`cope-quorum-relayed-vote/1`) | The exact proof passes verification for the approved ballot and has both an intake quorum and a retention quorum. It does not assign a position in the order or itself prove final inclusion. Once final evidence exists, compare its ordered hashes with the verified receipt's proof hash. |
| Recorded receipt (`cope-quorum-recorded-vote/1`) | The exact proof is in a quorum-signed open prefix for the approved ballot. A final history checked against this receipt must preserve that whole prefix. |
| Final evidence (`cope-quorum-final-evidence/1`) | The approved ballot and roster match the pinned chain. The evidence is settled, every ordered proof has an intake quorum, and both checkpoint quorums cover the whole ordered list. The verifier checks every proof and distinct nullifier, recounts options and turnout, and recomputes the outcome under the manifest rule. |

The [offline auditor](../../frontend/quorum-audit.mjs) checks these receipt and evidence files. A lone proof or a legacy tally can be cryptographically valid without establishing authorized membership, completeness, or agreement on a final history. Legacy ballots, including published ballots that predate a configured witness policy, are never retroactively upgraded by archive report signatures. Treat any proof-only view of a governed tally the same way until the pinned approvals and checkpoint certificates have been checked.

## Server software authority and rollback

Installing the verifier worker is a separate trust decision from approving the voting app or casting witness votes. The operator provisions the verifier supervisor, trusted admin service, and release policy locally. That policy pins an HTTPS mirror, channel, distinct release signing keys, threshold, and minimum sequence. Use release keys separate from witness keys. The COPE election API and verifier check-ins have no update authority, and the mirror cannot authorize an update by serving it.

Release signers sign the exact decoded manifest bytes. The signed payload binds the channel, source revision, sequence, publication and expiry times, minimum Node major version, and artifact size and SHA-256 digest. The supervisor checks these fields, signatures, downloaded bytes and unpacked layout before offering the release. Code runs only after a local administrator approves the exact offer on the dedicated trusted HTTPS administration origin. The privileged supervisor protects that approval channel, release metadata, and token from the unprivileged worker. It rechecks the offer before switching, records the new approval sequence, and uses a readiness and activation handshake before the worker serves requests or signs.

If the approved worker cannot start or exits, the supervisor can return to the previous approved version. The highest accepted sequence persists across rollback, so the same or an older offer cannot be approved again. Cached signed releases are still checked against the current local pins; expiry is checked for offers, not for an already approved installation. The trusted supervisor, admin bootstrap, local policy and base installation require manual updates from independently authenticated material. Approved worker code still has access to the witness signing archive. A rollback replaces the software; it does not undo anything that code did to the archive or reconstruct lost state.

## Limits of the guarantee

- **Availability.** COPE can withhold a ballot, an order, proofs, or checkpoint publication. The witnesses can recover retained proofs but do not finalize without COPE. An operator outage, a missed deadline, or an unavailable quorum can also prevent a seal.
- **Trust on first use.** Install a recent policy bundle authenticated by independent operators. Someone who trusts only genesis or an old pinned head may accept a forged successor if enough retired keys are later compromised. A newer authenticated head prevents accepting a branch that diverged before it. Protect and retire old keys once their ballots are finished, and preserve current signing state with each active key.
- **Privacy and eligibility.** Semaphore hides the connection between a valid vote proof and a roster commitment; the option itself is visible to the verifiers that receive it and later in evidence. Verifiers or their ingress can observe IP addresses and timing. COPE knows the enrollment account-to-commitment mapping, and an observer may correlate traffic or timing. Receipts reveal the anonymous choice and can be shared, so the protocol does not provide receipt-free or coercion-resistant voting. A trusted loader, safe handling of the backup, and careful review of the roster and proving artifacts all matter.

See the [verifier operator guide](README.md) for deployment, policy exchange, voter-release packaging, and the offline verification command.
