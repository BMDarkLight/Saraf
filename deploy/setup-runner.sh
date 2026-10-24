#!/usr/bin/env bash
# One-time VPS setup for continuous deployment, run as root on the VPS:
#   1. Node 22 for the frontend build (Ubuntu's nodejs is 18; Vite 8 needs >= 20.19)
#   2. a self-hosted GitHub Actions runner labelled "saraf-vps", installed as a service
# usage: bash setup-runner.sh <registration-token>
# get a token (valid 1h) on a machine logged in to gh:
#   gh api -X POST repos/BMDarkLight/Saraf/actions/runners/registration-token --jq .token
set -euo pipefail

TOKEN=${1:?usage: setup-runner.sh <registration-token>}
REPO_URL=https://github.com/BMDarkLight/Saraf
DIST=https://nodejs.org/dist/latest-v22.x

if ! node -v 2>/dev/null | grep -qE '^v(2[2-9]|[3-9][0-9])'; then
  F=$(curl -fsS "$DIST/SHASUMS256.txt" | awk '/linux-x64\.tar\.xz$/ {print $2}')
  cd /tmp
  curl -fsSO "$DIST/$F"
  curl -fsS "$DIST/SHASUMS256.txt" | grep " $F\$" | sha256sum -c -
  rm -rf /opt/node && mkdir -p /opt/node && tar xJf "$F" -C /opt/node --strip-components=1 && rm "$F"
  ln -sf /opt/node/bin/node /opt/node/bin/npm /opt/node/bin/npx /usr/local/bin/
fi
command -v rsync >/dev/null || apt-get install -y -q rsync

V=$(curl -fsS https://api.github.com/repos/actions/runner/releases/latest |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')
mkdir -p /opt/actions-runner && cd /opt/actions-runner
curl -fsSL -o runner.tgz "https://github.com/actions/runner/releases/download/v$V/actions-runner-linux-x64-$V.tar.gz"
tar xzf runner.tgz && rm runner.tgz
./bin/installdependencies.sh > /dev/null
# root because deploys write /opt/saraf and restart systemd units; the repo is private
export RUNNER_ALLOW_RUNASROOT=1
grep -q RUNNER_ALLOW_RUNASROOT .env 2>/dev/null || echo "RUNNER_ALLOW_RUNASROOT=1" >> .env
./config.sh --unattended --url "$REPO_URL" --token "$TOKEN" --name saraf-vps --labels saraf-vps --replace
./svc.sh install root
./svc.sh start
echo "runner installed; node $(node -v)"
