#!/bin/sh
set -eu
umask 077

site=https://cope-chess.live
state=${COPE_WORKER_HOME:-${XDG_STATE_HOME:-"$HOME/.local/state"}/cope-worker}
bin=${COPE_WORKER_BIN_DIR:-"$HOME/.local/bin"}
token_file=
prepare_only=false
container=false

while [ "$#" -gt 0 ]; do
    case "$1" in
        --server)
            site=${2%/}
            shift 2
            ;;
        --token-file)
            token_file=$2
            shift 2
            ;;
        --prepare-only)
            prepare_only=true
            shift
            ;;
        --container)
            container=true
            shift
            ;;
        *)
            printf 'Usage: sh -s -- [--server HTTPS_ORIGIN] [--token-file FILE] [--prepare-only] [--container]\n' >&2
            exit 2
            ;;
    esac
done

case "$site" in
    https://*) ;;
    *) printf 'The worker installer requires an HTTPS Cope origin.\n' >&2; exit 2 ;;
esac

for program in curl python3 git; do
    command -v "$program" >/dev/null || { printf '%s is required to install Cope.\n' "$program" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit("Cope requires Python 3.11 or later.") if sys.version_info < (3, 11) else None'

mkdir -p "$state" "$bin" "$state/installation"
root=$(cd "$state" && pwd)
if [ "$prepare_only" = true ] && { [ -s "$root/worker.json" ] || [ -s "$root/worker.json.identity-key" ]; }; then
    printf 'Prepare the image with a fresh COPE_WORKER_HOME before enrolling a machine.\n' >&2
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

if [ -n "$token_file" ]; then
    "$bin/cope" config --server "$site/worker" --token-file "$token_file"
else
    "$bin/cope" config --server "$site/worker"
fi
if [ "$container" = true ]; then
    "$bin/cope" config --compose-file "$release_dir/source/compose.worker.yaml"
fi

if [ "$prepare_only" = true ]; then
    printf 'Cope installed at %s/cope. Supply the worker token at first boot.\n' "$bin"
    exit 0
fi

if [ -z "$token_file" ] && [ ! -s "$root/token" ] && [ ! -s "$root/worker.json" ] && ! "$bin/cope" config --output json | python3 -c 'import json, os, sys; sys.exit(not os.path.isfile(json.load(sys.stdin).get("token_file") or ""))'; then
    if ! ( : </dev/tty ) 2>/dev/null; then
        printf 'Pass --token-file FILE or use --prepare-only for an image.\n' >&2
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
