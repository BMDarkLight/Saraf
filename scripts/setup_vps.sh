#!/usr/bin/env bash
# One-shot setup of the engine on a fresh Debian/Ubuntu VPS, in shadow mode (no real orders).
#
#   git clone https://<github-token>@github.com/BMDarkLight/wallex-agent /opt/wallex-agent
#   cd /opt/wallex-agent && git checkout main-7nynge && bash scripts/setup_vps.sh
#
# Asks for the keys (input hidden) and writes them only to .env (chmod 600, gitignored), runs the
# read-only pre-flight check, then starts a timed shadow session as a systemd service.
# Re-running is safe: existing .env values are kept unless you type a new one.
# Env overrides: HOURS (default 4), LLM_BASE_URL, LLM_MODEL, ENGINE_LLM_MAX_TOKENS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOURS="${HOURS:-4}"
cd "$ROOT"
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }

echo "== system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip >/dev/null

echo "== uv"
if ! command -v uv >/dev/null; then
  # PyPI first (GitHub / astral.sh downloads are often blocked from Iranian hosts)
  python3 -m venv /opt/uv-venv && /opt/uv-venv/bin/pip install -q uv && ln -sf /opt/uv-venv/bin/uv /usr/local/bin/uv \
    || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
uv --version

echo "== python >= 3.11"
PY=python3
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  apt-get install -y -qq python3.11 python3.11-venv >/dev/null 2>&1 && PY=python3.11 \
    || { uv python install 3.12 && PY=3.12; }
fi
echo "using $PY"

echo "== .env"
[ -f .env ] || cp .env.example .env
chmod 600 .env
setenv() {  # setenv KEY VALUE: replace KEY=... or append it
  python3 - "$1" "$2" <<'PY'
import pathlib, sys
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); lines = p.read_text().splitlines()
for i, l in enumerate(lines):
    if l.startswith(k + "="):
        lines[i] = f"{k}={v}"; break
else:
    lines.append(f"{k}={v}")
p.write_text("\n".join(lines) + "\n")
PY
}
current() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }
ask() {  # ask KEY PROMPT [secret]
  local cur val; cur="$(current "$1")"
  if [ -n "${3:-}" ]; then read -rsp "$2 [${cur:+keep existing}]: " val; echo
  else read -rp "$2 [${cur}]: " val; fi
  [ -n "$val" ] && setenv "$1" "$val"
  [ -n "$(current "$1")" ] || { echo "$1 is required"; exit 1; }
}
ask WALLEX_API_KEY "Wallex API key" secret
ask LLM_API_KEY "LLM API key (BoxAPI / GapGPT)" secret
[ -n "${LLM_BASE_URL:-}" ] && setenv LLM_BASE_URL "$LLM_BASE_URL"
ask LLM_BASE_URL "LLM base URL (OpenAI-compatible, from the provider docs)"
[ -n "${LLM_MODEL:-}" ] && setenv LLM_MODEL "$LLM_MODEL"
ask LLM_MODEL "LLM model"
setenv ENGINE_MODE shadow
setenv ENGINE_FEE_RATE 0.001
setenv ENGINE_MAKER_FEE_RATE 0.001
setenv ENGINE_EXECUTION maker
case "$(current LLM_MODEL)" in
  gpt-5*|gpt-6*) setenv ENGINE_LLM_MAX_TOKENS "${ENGINE_LLM_MAX_TOKENS:-2000}" ;;
  *qwen*) [ -n "$(current ENGINE_LLM_EXTRA_BODY)" ] || setenv ENGINE_LLM_EXTRA_BODY '{"reasoning_effort": "none"}' ;;
esac

echo "== dependencies"
cd backend
uv sync --frozen --no-dev --python "$PY"

echo "== pre-flight check (read-only)"
if ! uv run --no-dev python -m wallex_agent.engine check; then
  echo; echo "Fix the FAILED lines (key, IP whitelist, LLM URL/model), then re-run this script."; exit 1
fi

echo "== shadow service (${HOURS}h)"
cat >/etc/systemd/system/wallex-shadow.service <<EOF
[Unit]
Description=Wallex engine shadow session (no real orders)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$ROOT/backend
Environment=PYTHONUNBUFFERED=1 TZ=UTC
ExecStart=$(command -v uv) run --no-dev python -m wallex_agent.engine run --mode shadow --hours $HOURS
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl restart wallex-shadow
sleep 5
systemctl --no-pager --lines=15 status wallex-shadow || true
cat <<EOF

Running. Follow it with:   journalctl -u wallex-shadow -f
Output (journal, trades):  $ROOT/data/engine/shadow/
Stop early:                systemctl stop wallex-shadow
Bundle the results:        tar czf shadow.tgz -C $ROOT/data/engine shadow
EOF
