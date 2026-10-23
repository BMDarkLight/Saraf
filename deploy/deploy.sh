#!/usr/bin/env bash
# Deploys a checked-out commit to /opt/wallex-agent and restarts the services.
# Run by the self-hosted runner on the VPS (.github/workflows/deploy.yml); usage: deploy.sh <checkout> <commit>
# Server-only state is never touched: .env (keys, mode), data/ (engine journal + state, paper wallets).
set -euo pipefail

SRC=${1:?checkout directory}
COMMIT=${2:-unknown}
APP=/opt/wallex-agent
UV=${UV:-/root/.local/bin/uv}

# Build the UI in the checkout first: a failed build leaves the running deployment untouched
(cd "$SRC/frontend" && npm ci --no-audit --no-fund && npm run build)

rsync -a --delete \
  --exclude .git/ --exclude .github/ --exclude .env --exclude data/ \
  --exclude backend/.venv/ --exclude frontend/node_modules/ \
  "$SRC"/ "$APP"/

(cd "$APP/backend" && "$UV" sync --frozen --no-dev)

# Record the commit in the engine's start event; default the engine to shadow unless .env says otherwise
if grep -q '^GIT_COMMIT=' "$APP/.env"; then
  sed -i "s/^GIT_COMMIT=.*/GIT_COMMIT=$COMMIT/" "$APP/.env"
else
  echo "GIT_COMMIT=$COMMIT" >> "$APP/.env"
fi
grep -q '^ENGINE_RUN_ARGS=' "$APP/.env" || echo "ENGINE_RUN_ARGS=--mode shadow" >> "$APP/.env"
# The strategy runs for real from a copy of deploy/strategy.env (data/engine/profile.env, made by the strategy card's
# switch); refresh that copy so changes to the strategy's settings reach the live engine on deploy, not only on a toggle.
if [ -f "$APP/data/engine/profile.env" ]; then
  cp "$APP/deploy/strategy.env" "$APP/data/engine/profile.env"
fi

install -m 644 "$APP"/deploy/wallex-engine.service "$APP"/deploy/wallex-web.service \
  "$APP"/deploy/wallex-strategy.service "$APP"/deploy/wallex-persona@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet wallex-engine wallex-web wallex-strategy
systemctl restart wallex-web wallex-engine wallex-strategy

# copy-trading personas: one shadow session each (wallex_agent/personas.py lists them)
PERSONAS=$(cd "$APP/backend" && "$UV" run --no-dev python -m wallex_agent.personas)
for id in $PERSONAS; do
  systemctl enable --quiet "wallex-persona@$id"
  systemctl restart "wallex-persona@$id"
done

for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:8000/api/config && systemctl is-active --quiet wallex-engine; then
    MODE=$(cat "$APP/data/engine/mode.env" 2>/dev/null || grep '^ENGINE_RUN_ARGS=' "$APP/.env")
    echo "deployed $COMMIT: web up, engine $(systemctl is-active wallex-engine) ($MODE), strategy session $(systemctl is-active wallex-strategy)"
    exit 0
  fi
  sleep 2
done
echo "deploy $COMMIT failed health check" >&2
journalctl -u wallex-web -u wallex-engine --no-pager -n 40 >&2
exit 1
