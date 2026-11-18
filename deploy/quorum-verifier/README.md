# COPE Quorum verifier

The verifier archives completed ballots and checks the supplied vote proofs and
tally. The browser interface shows reports, evidence, the first observed digest,
and conflicts. These checks do not establish roster authorization or completeness
of the voting history. The ordinary COPE website still serves its own voting code;
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

## Checking witness signatures

An optional `COPE_VERIFIER_WITNESS_POLICY` file pins a separate three-of-four
witness policy for the independent verifier. Use the actual, independently
authenticated witness keys as the values in
`{"threshold":3,"operators":{"node-one":"…","node-two":"…","node-three":"…","node-four":"…"}}`.
The report displays how many distinct pinned keys signed the exact archived
evidence digest. A substituted or incomplete assigned witness list does not
satisfy the pin. A signature threshold is an attestation to the packet; it
does not certify an authorized electorate or a complete vote history.

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
membership transitions, independently witnessed vote receipts and finalization,
and a packaged voter client distributed through authenticated channels. This
verifier release process supplies no authority over those decisions.
