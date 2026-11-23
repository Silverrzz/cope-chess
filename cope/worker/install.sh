#!/bin/sh
set -eu
umask 077

site=${COPE_WORKER_INSTALL_ORIGIN:-https://cope-chess.live}
state=${COPE_WORKER_HOME:-${XDG_STATE_HOME:-"$HOME/.local/state"}/cope-worker}
bin=${COPE_WORKER_BIN_DIR:-"$HOME/.local/bin"}
token_file=
token=
prepare_only=false
container=false
autostart=false
seal_image=false

while [ "$#" -gt 0 ]; do
    case "$1" in
        --host|--server)
            site=${2:?"$1 requires a Cope host"}
            shift 2
            ;;
        --token)
            token=${2:?"--token requires a worker token"}
            shift 2
            ;;
        --token-file)
            token_file=${2:?"--token-file requires a file"}
            shift 2
            ;;
        --prepare-only)
            prepare_only=true
            shift
            ;;
        --autostart)
            autostart=true
            shift
            ;;
        --seal-image)
            seal_image=true
            prepare_only=true
            shift
            ;;
        --container)
            container=true
            shift
            ;;
        *)
            printf 'Usage: sh -s -- [--host HOST] [--token TOKEN | --token-file FILE] [--prepare-only | --seal-image] [--autostart | --container]\n' >&2
            exit 2
            ;;
    esac
done

if [ -n "$token" ] && [ -n "$token_file" ]; then
    printf 'Use either --token or --token-file.\n' >&2
    exit 2
fi
case "$token" in
    *[!A-Za-z0-9_-]*) printf 'Invalid worker token.\n' >&2; exit 2 ;;
esac
if [ "$container" = true ] && { [ "$autostart" = true ] || [ "$seal_image" = true ]; }; then
    printf 'Use --autostart and --seal-image with a native worker.\n' >&2
    exit 2
fi
if [ "$autostart" = true ]; then
    if ! command -v systemctl >/dev/null || ! systemctl show --property=Version --value >/dev/null 2>&1; then
        printf 'Autostart requires a running systemd. Otherwise, configure the provider to run cope start --foreground --boot.\n' >&2
        exit 2
    fi
    if [ "$(id -u)" -ne 0 ] && ! command -v sudo >/dev/null; then
        printf 'Autostart needs sudo to register the system service for the installing user.\n' >&2
        exit 2
    fi
fi

for program in curl python3 git; do
    command -v "$program" >/dev/null || { printf '%s is required to install Cope.\n' "$program" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit("Cope requires Python 3.11 or later.") if sys.version_info < (3, 11) else None'
site=$(python3 - "$site" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

value = sys.argv[1]
message = "Use a Cope hostname, HTTPS origin, or wss:// worker URL."
try:
    parsed = urlsplit(value if "://" in value else "https://" + value)
    port = parsed.port
except ValueError:
    sys.exit(message)
if (
    parsed.scheme not in {"https", "wss"} or not parsed.hostname
    or any(char.isspace() for char in value) or parsed.username is not None
    or parsed.path.rstrip("/") not in {"", "/worker"} or parsed.query or parsed.fragment
    or port == 0
):
    sys.exit(message)
print(urlunsplit(("https", parsed.netloc, "", "", "")))
PY
)

mkdir -p "$state" "$bin" "$state/installation"
root=$(cd "$state" && pwd)
if [ "$prepare_only" = true ] && [ "$seal_image" = false ] && { [ -s "$root/worker.json" ] || [ -s "$root/worker.json.identity-key" ]; }; then
    printf 'This worker is enrolled. Use --seal-image to prepare it for cloning.\n' >&2
    exit 2
fi
metadata=$(curl --fail --silent --show-error "$site/api/worker-release")
release=$(printf '%s' "$metadata" | python3 -c '
import json, re, sys
commit = json.load(sys.stdin).get("commit") or ""
if not re.fullmatch("[a-f0-9]{40}", commit):
    sys.exit("Cope has no installable worker release.")
print(commit)
')
repository=$(printf '%s' "$metadata" | python3 -c '
import json, sys
url = json.load(sys.stdin).get("repository_url") or ""
print(url if "://" in url else "https://" + url.removesuffix(".git") + ".git")
')
case "$repository" in
    https://*) ;;
    *) printf 'Cope did not provide an HTTPS release repository.\n' >&2; exit 1 ;;
esac

release_dir="$root/installation/$release"
if [ ! -x "$release_dir/venv/bin/cope" ]; then
    mkdir -p "$release_dir"
    if [ ! -d "$release_dir/source/.git" ]; then
        git init --quiet "$release_dir/source"
    fi
    git -C "$release_dir/source" fetch --quiet --depth=1 "$repository" "$release"
    git -C "$release_dir/source" checkout --quiet --detach FETCH_HEAD
    [ "$(git -C "$release_dir/source" rev-parse HEAD)" = "$release" ]
    printf '%s\n' "$release" > "$release_dir/source/cope/BUILD_VERSION"
    python3 -m venv "$release_dir/venv"
    "$release_dir/venv/bin/python" -m pip install --disable-pip-version-check "$release_dir/source[worker]"
fi

cat > "$root/installation/cope-launch" <<'EOF'
#!/bin/sh
installation=$(dirname "$(readlink -f "$0")")
root=$(dirname "$installation")
export COPE_WORKER_HOME="$root"
current="${COPE_UPDATE_ROOT:-$root/releases}/current/venv/bin/cope"
if [ -x "$current" ]; then
    exec env -u COPE_BUILD_VERSION "$current" "$@"
fi
exec "$installation/base-cope" "$@"
EOF
chmod 700 "$root/installation/cope-launch"
ln -sfn "$release_dir/venv/bin/cope" "$root/installation/base-cope"
if [ -e "$bin/cope" ] && [ "$(readlink -f "$bin/cope")" != "$root/installation/cope-launch" ]; then
    printf '%s/cope belongs to another installation. Set COPE_WORKER_BIN_DIR to use a different path.\n' "$bin" >&2
    exit 1
fi
ln -sfn "$root/installation/cope-launch" "$bin/cope"

if [ -n "$token" ]; then
    temporary_token=$(mktemp "$root/.token.XXXXXX")
    trap 'rm -f -- "$temporary_token"' 0
    printf '%s\n' "$token" > "$temporary_token"
    token_file="$root/token"
    mv -f -- "$temporary_token" "$token_file"
    trap - 0
    unset token
fi
if [ -n "$token_file" ]; then
    "$bin/cope" config --server "$site/worker" --token-file "$token_file"
else
    "$bin/cope" config --server "$site/worker"
fi
if [ "$container" = true ]; then
    "$bin/cope" config --compose-file "$release_dir/source/compose.worker.yaml"
fi

if [ "$autostart" = true ] || [ "$seal_image" = true ]; then
    "$bin/cope" config --output json | python3 -c '
import json, re, sys
from pathlib import Path

settings = json.load(sys.stdin)
if settings.get("compose_file"):
    sys.exit("Use these options with a native worker, without a Compose file configured.")
if sys.argv[2] == "true":
    token_file = Path(settings.get("token_file") or Path(sys.argv[1]) / "token").expanduser()
    try:
        shared_token = re.fullmatch(r"cope_identity_[A-Za-z0-9_-]{43}", token_file.read_text().strip())
    except (OSError, UnicodeError):
        shared_token = False
    if not shared_token:
        sys.exit("Sealing an image requires a shared Cope worker identity token.")
' "$root" "$seal_image"
fi

if [ "$autostart" = true ]; then
    run_as_root() {
        if [ "$worker_uid" -eq 0 ]; then
            "$@"
        else
            sudo -- "$@"
        fi
    }
    worker_uid=$(id -u)
    unit_name="cope-worker-user-$worker_uid.service"
    unit_path="/etc/systemd/system/$unit_name"
    description="Description=Cope worker installed for UID $worker_uid"
    if [ -L "$unit_path" ] || { [ -e "$unit_path" ] && ! grep -Fxq "$description" "$unit_path"; }; then
        printf 'A different service already uses %s.\n' "$unit_path" >&2
        exit 1
    fi
    unit=$(mktemp "$root/.autostart.XXXXXX")
    trap 'rm -f -- "$unit"' 0
    python3 - "$root/installation/cope-launch" "$worker_uid" "$description" > "$unit" <<'PY'
import sys

launcher, uid, description = sys.argv[1:]
if any(ord(char) < 32 for char in launcher):
    sys.exit("The worker installation path contains unsupported control characters.")
launcher = '"' + launcher.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
print(f"""[Unit]
{description}
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User={uid}
ExecStart=:/bin/sh {launcher} start --foreground --boot
Restart=on-failure
RestartPreventExitStatus=78
RestartSec=5
KillMode=mixed
TimeoutStopSec=25
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target""")
PY
    run_as_root install -o root -g root -m 0644 "$unit" "$unit_path"
    run_as_root systemctl daemon-reload
    run_as_root systemctl enable "$unit_name"
    rm -f -- "$unit"
    trap - 0
    printf 'Autostart enabled with %s.\n' "$unit_name"
fi

if [ "$seal_image" = true ]; then
    "$bin/cope" stop grace
    python3 - "$root" <<'PY'
import fcntl
import sys
from contextlib import ExitStack
from pathlib import Path

root = Path(sys.argv[1])
with ExitStack() as locks:
    try:
        for filename in ("launch.lock", "control.lock", "supervisor.lock"):
            handle = locks.enter_context((root / filename).open("a+"))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("Worker changed state while sealing the image. Stop it and retry.")
    for filename in ("worker.json", "worker.json.identity-key", "desired.json", "status.json"):
        (root / filename).unlink(missing_ok=True)
PY
    printf 'Cope is stopped and ready for image capture. Each clone will get a new machine identity.\n'
    exit 0
fi

if [ "$prepare_only" = true ]; then
    printf 'Cope installed at %s/cope without starting a worker.\n' "$bin"
    exit 0
fi

if [ -z "$token_file" ] && [ ! -s "$root/token" ] && [ ! -s "$root/worker.json" ] && ! "$bin/cope" config --output json | python3 -c 'import json, os, sys; sys.exit(not os.path.isfile(json.load(sys.stdin).get("token_file") or ""))'; then
    if ! ( : </dev/tty ) 2>/dev/null; then
        printf 'Pass --token TOKEN or --token-file FILE, or use --prepare-only for an image.\n' >&2
        exit 2
    fi
    printf 'Paste your Cope worker token: ' >/dev/tty
    IFS= read -r token </dev/tty
    if [ -z "$token" ]; then
        printf 'A worker token is required.\n' >&2
        exit 2
    fi
    printf '%s\n' "$token" > "$root/token"
fi

"$bin/cope" start
