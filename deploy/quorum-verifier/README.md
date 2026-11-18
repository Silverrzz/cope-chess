# COPE Quorum verifier

The verifier archives completed ballots and checks the supplied vote proofs and
tally. The browser interface shows reports, evidence, the first observed digest,
and conflicts. For ballots published with a pinned witness policy, it also verifies
that the final tally covers the complete history sealed by those witnesses.
These checks do not establish roster authorization. Legacy ballots have only
proof and tally checks. The ordinary COPE website still serves its own voting code;
it does not have the trust boundary of an independently distributed voting app.

The public archive starts at `/`. Existing JSON clients can keep using
`/`, `/ballots/<uuid>` and `/ballots/<uuid>/evidence`. Use `/api/status` for
an explicit JSON status URL, and append `?format=json` to report URLs opened in
a browser. The witness key and archived evidence live separately from the software.

## Local administration

Start a reviewed checkout with Python 3.11 or newer, Node.js 24 or newer, and npm.
OpenSSL 3 is needed for signed updates. Set the token file to an absolute path:

```sh
umask 077
openssl rand -hex 32 > /path/to/private/admin-token
export COPE_VERIFIER_ADMIN_TOKEN_FILE=/path/to/private/admin-token
python3 install.py --targets verifier --verifier-origin https://cope-chess.live
```

Open **http://127.0.0.1:8788/admin**. Use a separate token from COPE’s platform
admin token. Sessions are held in memory and expire after an hour or on restart.
For remote admin, configure `COPE_VERIFIER_ADMIN_ORIGIN` as the exact HTTPS
origin and set `COPE_VERIFIER_ADMIN_PROXIES` to the comma-separated source IPs
of the HTTPS ingress. The ingress must overwrite `X-Forwarded-Proto` and
forward the configured host. Restrict the verifier's upstream port to the
trusted ingress network and use an encrypted upstream if that network is
shared. The default bind is loopback.

## Trusting and publishing releases

An operator enables discovery by pointing `COPE_VERIFIER_RELEASE_POLICY` at an
absolute path to a locally provisioned JSON file. The policy is read at
supervisor startup. No release policy is downloaded from COPE or the mirror.

```json
{
  "manifest_url": "https://releases.example.org/quorum/stable/manifest.json",
  "channel": "stable",
  "threshold": 2,
  "minimum_sequence": 1,
  "keys": {
    "operator-one": "<64 lowercase hex digits: raw Ed25519 public key>",
    "operator-two": "<64 lowercase hex digits: raw Ed25519 public key>"
  }
}
```

Use the agreed threshold and authenticate the independent operators' keys out
of band. The example requires both operators. Each operator can generate an
offline release key with `openssl genpkey -algorithm ED25519 -out release-key.pem`.
To extract the raw public key, run
`openssl pkey -in release-key.pem -pubout -outform DER | tail -c 32 | xxd -p -c 32`.
Do not reuse verifier witness keys. Set the initial sequence floor to the first
sequence you are willing to consider; assign every future release a higher
sequence. Once a release starts successfully, the persisted active manifest
prevents installing a release with a lower or equal sequence.

Publishers build from a reviewed Git commit. The artifact includes the verifier
and its locked production dependencies. The release build runs npm without
lifecycle scripts; verifiers do not run npm or Git at update time.

```sh
python3 frontend/quorum-release.py build --channel stable --sequence 42 dist/quorum-42
python3 frontend/quorum-release.py inspect dist/quorum-42/manifest.unsigned.json

# Run independently on each signer's machine after reviewing the commit and artifact.
python3 frontend/quorum-release.py sign --operator operator-one --key /private/release-key.pem \
  dist/quorum-42/manifest.unsigned.json dist/quorum-42/artifacts/<sha256>.zip operator-one.json

# Collect detached signature files; private keys never go to the mirror.
python3 frontend/quorum-release.py assemble dist/quorum-42/manifest.unsigned.json \
  dist/quorum-42/manifest.json operator-one.json operator-two.json
```

Upload `artifacts/<sha256>.zip` first, then publish `manifest.json` at the
configured HTTPS URL. Do not redirect either endpoint. The signatures cover
the exact decoded manifest bytes, including the channel, sequence, revision,
expiry, minimum Node major version and artifact size and hash. The updater
polls about once a minute and stages a release only after checking all of
these. Nothing executes until an administrator reviews its details and clicks
**Approve and restart** in this verifier’s own UI. The supervisor checks the
approval and artifact again before switching. It waits for the candidate to
listen before allowing it to scan or sign. A candidate that fails at startup
restores the previous process. A failure later in an accepted release requires
a new, higher sequence release; use the last known good code if necessary.
Routine verifier check-ins to COPE are telemetry only.

Keep the supervisor, local policy files, and base image under operator control.
Updating those components requires a manual, independently authenticated install.
For planned key rotation, publish a transition release that satisfies both
policies, install it under the old policy, then provision the new policy
locally. Boot checks the active artifact and its signatures against the current
pins; it fails closed if the active copy is missing or no longer trusted. For
recovery, restore the exact active artifact and authenticated policy from a
protected backup. Preserve the active release record to retain rollback
protection. Approved verifier code runs with access to the archive and witness
key. Protect local files and treat signing and approving such code as trust
decisions.

## Witnessed vote history

For new ballots, configure the web service with `COPE_QUORUM_WITNESS_POLICY`
(pointing to a locally provisioned JSON file) or set
`COPE_QUORUM_WITNESS_POLICY_JSON` in the web service’s Compose environment.
On each independent verifier, point `COPE_VERIFIER_WITNESS_POLICY` at the
independently authenticated policy file for the same COPE origin. Register and
enable exactly those keys in COPE’s witness setup. Each published ballot freezes
the policy and its assigned keys. When the platform first publishes a witnessed
ballot, it pins the authority; subsequent publication requires the same policy
or an approved successor. Existing and unconfigured legacy ballots are explicitly
proof-only. Browsers can import a policy obtained outside the COPE site.

A policy contains `operators` (a mapping of operator IDs to distinct raw
Ed25519 public keys), a `threshold`, and optional `max_faulty`. The supported
range is one to 64 distinct operators. For `n` operators, threshold `t` and
fault allowance `f`, two quorums must share at least one honest witness:
`2*t - n > f`. One of four fails even with no faulty operators. Five of
seven tolerates two and can still reach quorum when both withhold signatures.
If `max_faulty` is omitted, the largest allowance consistent with safety
is used. To also make progress with all of those operators offline, choose
`t <= n - f`.
Keys assigned to different IDs must actually be independently controlled.

The platform exposes the full ordered vector of vote-proof hashes while voting
is open. Each witness keeps a durable copy of its last signed prefix and can
only sign extensions of that prefix. Votes and individual choices are not
revealed by that endpoint. A receipt records the exact proof and an open
checkpoint signed by a quorum. The hosted UI initially shows submitted or
pending and only marks a vote recorded after checking a receipt against the
locally pinned policy. Exported receipts reveal the anonymous vote’s choice
to whoever receives them; keep them private unless you intend to share them.

A witnessed ballot closes at its published deadline, even at full turnout.
Witnesses use their own clocks. They sign an opening checkpoint only while the
ballot is open. After closing, a witness seals the final history only if it
extends everything that witness already signed and a quorum attested to that
exact head during voting. The result verifier checks both quorums, recomputes
the ordered hashes from the complete final list, and verifies the proofs and
tally. This stays provisional if COPE withholds history, witnesses disagree
or are offline, or the final submissions do not reach a quorum before the
deadline. Submit early enough to receive a receipt; the platform cannot prove
that an unacknowledged request was ever recorded. This protocol does not
attempt to recover votes that the host refused to record.

Keep `vote-history/`, `policy-rounds/`, `policy-decisions/`, and
`witness-policy-head` with the witness key. Never run multiple copies of the same signing
identity or let it resume signing from stale state: doing so can let it
endorse incompatible histories. If signing state is lost, restore it from
an independently confirmed current backup or rotate the affected key under
the current policy before continuing. Peer copies let you recover decisions,
but may lack that key's most recent votes or promises to leave a round.
Synchronize the independent witnesses’ clocks and review the configured fault
model before using real ballots.

Verify an exported receipt or final evidence file without the website:

```sh
node frontend/quorum-audit.mjs evidence.json trusted-policy.json https://cope-chess.live
```

### Change a policy

The current threshold approves its successor. If the current requirement is
three of four, three of those four distinct operators must approve the exact
change, even if it introduces a different threshold or changes the membership
entirely. A split vote leaves the current policy in effect. The approval
messages bind the COPE origin, current policy head, round, and proposal.
Each verifier validates the proposed policy before signing. COPE and the
verifiers recheck each successor when reading the signed chain; even a
unanimous approval cannot install an invalid threshold.

On COPE’s **Admin → Quorum → Verifiers** page, select the exact proposed
membership and download the suggested policy. With a configured policy,
newly registered verifiers are staged without changing ballot publication.
The suggestion tolerates as many faulty verifiers as possible while still
allowing the others to reach quorum. Review the keys and use the downloaded
file as `proposed.json` in the approval commands below.

Operators exchange signed records and run the policy tool locally. Start
at round 0 with the reviewed proposal. Each example uses a three-of-four
policy; run the first three commands on the participating verifiers with
their own archive path and a separate output file for each operator.

```sh
origin=https://cope-chess.live
node frontend/quorum-policy.mjs prepare "$origin" current.json 0 /path/to/archive proposed.json > prepare-1.json
node frontend/quorum-policy.mjs commit "$origin" current.json 0 /path/to/archive proposed.json prepare-{1,2,3}.json > commit-1.json
node frontend/quorum-policy.mjs store "$origin" current.json 0 /path/to/archive proposed.json commit-{1,2,3}.json > stored-1.json
node frontend/quorum-policy.mjs assemble "$origin" current.json 0 proposed.json approved.json stored-{1,2,3}.json
```

If the round stalls, gather signed retry records from the current quorum and
give them to each operator when preparing the next round:

```sh
node frontend/quorum-policy.mjs retry "$origin" current.json 1 /path/to/archive > retry-1.json
node frontend/quorum-policy.mjs prepare "$origin" current.json 1 /path/to/archive proposed.json retry-{1,2,3}.json > prepare-1.json
```

Use the new round number throughout subsequent commands. Retry records lock
their signers out of older rounds. A 2–2 split in round 0 can retry with
either proposal or a new one. If the retry records include evidence that
an earlier proposal could already have a commit quorum, the new round must
carry the latest such proposal forward. If you find the earlier commit
quorum, you can also store and publish that decision directly.

Each `store` signer independently checks the commit quorum and saves it
before releasing a receipt. Fetch stored copies over HTTPS at
`/api/policy-decisions/<head>/<round>` on participating verifiers; omit the
round to list available rounds. `store` also accepts one peer's stored
record in place of separate commit files and verifies its commits locally.
`assemble` requires a quorum of commits and a quorum of storage receipts.
It does not change the policy on the platform or verifiers.

Finish active and scheduled ballots before running `store`. A storing
verifier stops starting new ballots under the old policy and can finish
ones it already witnessed. To activate the successor, distribute the signed
bundle and replace the policy on the platform and verifiers, then restart
the verifiers and update the platform's assigned witnesses. Existing pins
only accept a bundle that extends the chain they already trust. Historical
evidence can still be audited against the policy under which it was signed.
The signing key never leaves its verifier.

If a crash leaves a `.policy-<head>.lock` directory, stop the verifier,
check that no policy command is running, and remove only the empty lock
directory. Keep all round and decision records. Update all policy participants
together; the one-step approval format from earlier development revisions
does not count under this protocol.

## Pomni deployment

The Compose wrapper follows production **https://cope-chess.live** and exposes
**https://quorum.nocturn9x.space** via Heimdall and Pyra. Use a reviewed checkout
for the first installation of the new supervisor. Set `COPE_REVISION` to its
full commit ID and `COPE_VERIFIER_CONFIG_DIR` to a private directory readable by
the container's node user (UID 1000 in the base image). To enable administration
and updates, set the corresponding Compose environment values to
`/verifier-config/admin-token` and `/verifier-config/release-policy.json`.
The config directory is mounted read-only. Supply an empty directory and leave
the token and policy settings unset to run the public archive alone.

```sh
export COPE_REVISION="$(git rev-parse HEAD)"
docker compose -f deploy/quorum-verifier/compose.yaml build
docker compose -f deploy/quorum-verifier/compose.yaml up -d --wait --wait-timeout 120
```

The maintained [Nginx configuration](nginx.conf) allows POST only to the three
admin write endpoints and forwards the HTTPS scheme. Install it on the active
ingress configuration source; validate and reload Nginx and check standby
replication. If migrating from the old updater, don't restore its deployment
job handler. The new supervisor uses an independent state folder and ignores
the old updater's `active.json`.

| Named volume | Mount | Preserve |
| --- | --- | --- |
| `cope-quorum-verifier_production_archive` | `/state` | Origin binding, original witness key, evidence and reports |
| `cope-quorum-verifier_production_runtime` | `/app/.cope-worker` | Active signed manifest in `installer/quorum-verifier/v1/approved-v1.json` and cached artifact |

Back up both volumes before replacing the bootstrap. Never start two copies
with the same witness key, and do not delete volumes during a routine restart.
Compare the public key, archive count and latest scan at `/api/status` before
and after deployment. The health check measures the last successful directory
scan, not whether every ballot or check-in succeeded.

Further protocol work from the review needs a ratified genesis and governed
membership transitions, and a packaged voter client distributed through authenticated channels. This
verifier release process supplies no authority over those decisions.
