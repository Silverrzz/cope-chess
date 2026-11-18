# COPE Quorum verifier

The verifier archives completed ballots and checks the supplied vote proofs and
tally. The browser interface shows reports, evidence, the first observed digest,
and conflicts. For ballots published with a pinned witness policy, it also verifies
that the final tally covers the complete history sealed by those witnesses.
The pinned chain also authorizes the public roster and the exact ballot. Legacy
ballots have only proof and tally checks. Governed voting takes place on
independent verifier origins using an app release approved by the quorum.

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

Signed updates require a Linux supervisor with separate supervisor and worker
accounts. Use the Compose deployment below, or install the bootstrap and Node
in root-owned paths and provide the worker archive owner’s UID and GID as
`COPE_VERIFIER_WORKER_UID` and `COPE_VERIFIER_WORKER_GID`. Set a dedicated HTTPS
admin origin (different from the public verifier hostname) and route it to port
8789. Only the trusted admin service on that origin can read the token and
approve a release. Its token file must be root-owned with mode 0600. Ordinary
public reports continue on port 8788.

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
sequence. Once the administrator’s approval is persisted, that sequence is
the floor even if the release crashes and the supervisor restores the previous
approved version.

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
listen before allowing it to scan or sign. If the candidate fails during
startup or exits later, the supervisor restores the previous approved release
and keeps looking for newer offers. Approving the same or a lower sequence is
still disallowed.
Routine verifier check-ins to COPE are telemetry only.

Keep the supervisor, local policy files, and base image under operator control.
Updating those components requires a manual, independently authenticated install.
For planned key rotation, publish releases that satisfy both policies for the installed and fallback versions,
then provision the new policy
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
the policy and its assigned keys. COPE pins the chain on the first witnessed publication. Existing and unconfigured
legacy ballots are proof-only. Obtain a recent authenticated policy bundle from
the independent operators before auditing witnessed evidence.

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

In COPE’s **Admin → Quorum** page, save a draft and download its verifier
approval proposal. If there is no current matching roster, this exports the
existing public commitments; voters keep their encrypted backups. Process that
file with the quorum commands below, install the approved bundle, then
download the ballot proposal and repeat. Review the proposal’s exact roster,
question, rules, and dates before voting on it. Once the same ballot is
approved, publish it without editing the draft or changing the proposed roster.
Later roster changes use the same approval flow. They do not rewrite approved
ballots or make replacement credentials valid for old rosters.

During voting, only hashes are public. Voters send a proof directly to assigned
verifiers; they can see the IP address, time, and anonymous choice. Configure
every ingress to omit access logs for `/api/intake/`. The browser reports
retention after it verifies a quorum of intake signatures and a quorum of
signed retention receipts. Exported receipts reveal the choice; keep them
private unless you intend to share it.

Two minutes after the published voting deadline, the ballot settles. Verifiers
stop accepting new proof submissions at the voting deadline. During the
settlement period they can forward certified submissions or retrieve copies
from peers. A verifier refuses to seal a tally that omits a certified vote
it retains. It also validates the exact proofs, all intake certificates, and
an open checkpoint quorum before sealing. Results stay provisional if COPE
withholds the ordered history or a quorum cannot agree on it. The verifiers
can recover retained proofs from one another, but currently still rely on
COPE to publish the ordered tally and collect its checkpoints.

## Independent voter sites

From the reviewed frontend checkout, build the single-file browser release and
package the reviewed depth-10 Semaphore artifacts:

```sh
cd frontend
npx vite build --config vite.quorum-voter.config.ts
node quorum-voter-release.mjs /path/to/proof-artifacts
```

Approve the generated `dist/quorum-voter/proposal.json` as a governance
proposal below. Place `release.json`, `vote.js`, `semaphore-10.wasm`, and
`semaphore-10.zkey` in the directory named by `COPE_VERIFIER_VOTER_RELEASE`,
and install the approved bundle on each participating verifier. The bootstrap
checks every exact size and SHA-256 before offering the app. Voters can open any
trusted verifier’s `/vote/<ballot-id>` page. This is a top-level page with no
COPE login; voters should verify the independent hostname before unlocking
their backup. The verifier operator serving the page controls its bootstrap.
A malicious bootstrap can ask for a voter’s secrets, so use a trusted entry
point. A witnessed ballot needs at least a quorum of distinct HTTPS verifier
origins.

Keep `intake/`, `vote-history/`, `policy-rounds/`, `policy-decisions/`, and
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
their own archive path and a separate output file for each operator. Run
`ready` on a quorum of proposed operators using the outgoing commits and
their archive pinned to the current policy. For a governance proposal, the
current and proposed operator sets are identical.

```sh
origin=https://cope-chess.live
node frontend/quorum-policy.mjs prepare "$origin" current.json 0 /path/to/archive proposed.json > prepare-1.json
node frontend/quorum-policy.mjs commit "$origin" current.json 0 /path/to/archive proposed.json prepare-{1,2,3}.json > commit-1.json
node frontend/quorum-policy.mjs ready "$origin" current.json 0 /path/to/archive proposed.json commit-{1,2,3}.json > ready-1.json
node frontend/quorum-policy.mjs store "$origin" current.json 0 /path/to/archive proposed.json ready-{1,2,3}.json > stored-1.json
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
quorum, you can obtain readiness from the successor and store that decision.

Each `store` signer independently checks the commit quorum and saves it
before releasing a receipt. Fetch stored copies over HTTPS at
`/api/policy-decisions/<head>/<round>` on participating verifiers; omit the
round to list available rounds. `store` also accepts one peer's stored
record in place of separate readiness records and verifies both quorums locally.
`assemble` requires outgoing commit and storage quorums and an incoming
readiness quorum.
It does not change the policy on the platform or verifiers.

Finish active and scheduled ballots before running `store` for a membership change. A storing
verifier stops starting new ballots under the old policy and can finish
ones it already witnessed. To activate the successor, distribute the signed
bundle and replace the policy on the platform and verifiers, then restart
the verifiers and update the platform's assigned witnesses. Existing pins
only accept a bundle that extends the chain they already trust. Historical
evidence can still be audited against the policy under which it was signed.
Keep a recent authenticated chain head for new installations. A client that
trusts only an old head cannot distinguish a forged successor if enough retired
keys are later compromised. Retire old keys securely once assigned ballots are
finished.

If a crash leaves a `.policy-<head>.lock` directory, stop the verifier,
check that no policy command is running, and remove only the empty lock
directory. Keep all round and decision records. Update all policy participants
together; the one-step approval format from earlier development revisions
does not count under this protocol.

## Pomni deployment

The Compose wrapper follows production **https://cope-chess.live** and exposes
**https://quorum.nocturn9x.space** via Heimdall and Pyra. Set
`COPE_VERIFIER_CONFIG_DIR` to an operator-controlled, root-owned directory
mounted read-only. Keep the signed release policy and token in that directory.
Only the approved witness bundle and voter release files need to be readable
by UID 1000. Set `COPE_VERIFIER_ADMIN_ORIGIN` to a separate HTTPS hostname
and route it to port 8789 to use signed updates. On that virtual host, forward
the exact hostname and overwrite `X-Forwarded-Proto` with `https`. Keep both
upstream ports restricted to the ingress network.

To build from the exact committed source rather than the working tree:

```sh
export COPE_REVISION="$(git rev-parse HEAD)"
git archive HEAD | docker build --build-arg COPE_REVISION="$COPE_REVISION" \
  -f deploy/quorum-verifier/Dockerfile -t "cope-quorum-verifier:$COPE_REVISION" -
docker compose -f deploy/quorum-verifier/compose.yaml up -d --no-build --wait --wait-timeout 120
```

The maintained [Nginx configuration](nginx.conf) is for the public origin. It
also carries the direct vote routes and disables their access logs. If
upgrading an unsigned deployment, keep the original witness archive and
start with the new protected supervisor volume. If a prior updater already
installed signed releases, migrate the trusted active release record and
cached artifacts into the protected volume to preserve its sequence floor.

| Named volume | Mount | Preserve |
| --- | --- | --- |
| `cope-quorum-verifier_production_archive` | `/state` | Original witness key, signing journals, retained votes, evidence and reports |
| `cope-quorum-verifier_production_supervisor_runtime` | `/app/.cope-worker` | Approval sequence, installed and fallback manifests, and verified artifacts |

Back up both volumes before replacing the bootstrap. Never start two copies
with the same witness key. Compare the public key, archive count, and latest
scan at `/api/status` before and after deployment. The health check measures
the last successful directory scan.
