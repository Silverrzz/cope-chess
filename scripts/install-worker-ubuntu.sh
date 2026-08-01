#!/usr/bin/env bash
set -Eeuo pipefail

if [ "$(id -u)" -ne 0 ]; then
  printf 'Run this installer as root.\n' >&2
  exit 1
fi

repo_dir="${1:-$PWD}"
if [ ! -f "$repo_dir/pyproject.toml" ]; then
  printf 'Usage: sudo scripts/install-worker-ubuntu.sh /path/to/tagged/cope-chess\n' >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  docker.io \
  docker-buildx \
  git \
  python3 \
  python3-pip \
  python3-venv
docker buildx version

repo_dir="$(cd "$repo_dir" && pwd)"
repository_url="$(git -C "$repo_dir" remote get-url origin)"
commit="$(git -C "$repo_dir" rev-parse HEAD)"
if ! printf '%s' "$commit" | grep -Eq '^[0-9a-f]{40}$'; then
  printf 'The worker source is not on a full Git commit.\n' >&2
  exit 1
fi

id cope-worker >/dev/null 2>&1 || useradd --system --home-dir /var/lib/cope-worker --create-home --shell /usr/sbin/nologin cope-worker
usermod -aG docker cope-worker
systemctl enable --now docker
install -d -o cope-worker -g cope-worker -m 0700 /var/lib/cope-worker
install -d -o cope-worker -g cope-worker -m 0755 /opt/cope-worker /opt/cope-worker/releases
install -d -o root -g root -m 0755 /etc/cope
if [ ! -d /opt/cope-worker/repository/.git ]; then
  runuser -u cope-worker -- git clone "$repo_dir" /opt/cope-worker/repository
fi
runuser -u cope-worker -- git -C /opt/cope-worker/repository remote set-url origin "$repository_url"
runuser -u cope-worker -- git -C /opt/cope-worker/repository fetch --prune origin
release="/opt/cope-worker/releases/$commit"
if [ ! -x "$release/venv/bin/cope" ]; then
  runuser -u cope-worker -- mkdir -p "$release"
  runuser -u cope-worker -- git -C /opt/cope-worker/repository worktree add --detach "$release/source" "$commit"
  runuser -u cope-worker -- sh -c "printf '%s\n' '$commit' > '$release/source/cope/BUILD_VERSION'"
  runuser -u cope-worker -- python3 -m venv "$release/venv"
  runuser -u cope-worker -- "$release/venv/bin/python" -m pip install --upgrade pip
  runuser -u cope-worker -- "$release/venv/bin/python" -m pip install "$release/source[worker]"
fi
ln -sfn "$release" /opt/cope-worker/current
install -o root -g root -m 0644 "$repo_dir/deploy/cope-worker.service" /etc/systemd/system/cope-worker.service

if [ ! -f /etc/cope/worker.env ]; then
  install -o root -g root -m 0644 /dev/null /etc/cope/worker.env
  printf 'COPE_WORKER_SERVER_URL=wss://cope.example.com/worker\n' > /etc/cope/worker.env
  printf 'COPE_UPDATE_ROOT=/opt/cope-worker\nCOPE_UPDATE_REPOSITORY_URL=%s\n' "$repository_url" >> /etc/cope/worker.env
fi
if [ ! -f /etc/cope/worker.token ]; then
  install -o root -g cope-worker -m 0640 /dev/null /etc/cope/worker.token
fi
if [ ! -f /var/lib/cope-worker/worker.json ]; then
  existing_session="$(sed -n 's/^COPE_WORKER_SESSION_ID=//p' /etc/cope/worker.env | tail -n 1)"
  if [ -n "$existing_session" ] && [ "$existing_session" != "replace-with-worker-session-id" ]; then
    runuser -u cope-worker -- sh -c "printf '{\"session_id\":\"%s\"}\n' '$existing_session' > /var/lib/cope-worker/worker.json"
    chmod 0600 /var/lib/cope-worker/worker.json
  fi
fi

systemctl daemon-reload
printf 'Worker runtime installed. Edit /etc/cope/worker.env, place the registration token in /etc/cope/worker.token, then enable cope-worker.service.\n'
