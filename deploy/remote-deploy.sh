#!/usr/bin/env bash
# remote-deploy.sh — idempotent deploy of SPY-DER on a VPS.
#
# Runs ON the VPS as root. Provisions on first run (service user, checkout,
# venv, state tree, systemd units) and fast-forwards on every later run. Safe to
# re-run any number of times.
#
# Until now SPY-DER had no deploy path of its own. Its code reached the box only
# as a side effect of a neighbouring project's deploy doing `git reset --hard`
# on this checkout — which meant a SPY-DER-only merge never shipped, the venv
# was never updated, and the units in this directory were never installed. All
# four of `spy-der-{engine,settlement,dashboard-api}.service` and the validation
# timers were therefore present in the repo and absent from systemd.
#
# The ONE thing this never touches is the secrets file (/etc/spy-der/spy-der.env):
# it is created by hand once and the script refuses to start units until it
# exists, so a key is never overwritten by a deploy.
set -euo pipefail

REPO_URL="${SPY_DER_REPO_URL:-https://github.com/DGator86/SPY-DER.git}"
DEPLOY_REF="${DEPLOY_REF:-origin/main}"
APP_DIR="${SPY_DER_APP_DIR:-/opt/spy-der}"
ENV_FILE=/etc/spy-der/spy-der.env
CONFIG_FILE=/etc/spy-der/config.yaml
STATE_DIR=/var/lib/spy-der
SVC_USER=spy-der

#: Long-running services. Installed and restarted every deploy.
SERVICES=(
    spy-der-market
    spy-der-engine
    spy-der-settlement
    spy-der-agent
    spy-der-dashboard-api
)

#: Timer-driven units. Installed and enabled; the timer owns the schedule.
TIMERS=(
    spy-der-dojo-daily
    spy-der-dojo-recent
    spy-der-dojo-weekly
    spy-der-validation-daily
    spy-der-validation-weekly
)

#: State subdirectories the runtime owns (deploy/config.yaml.example).
STATE_DIRS=(
    market chains bars forecasts candidates decisions positions settlements
    journal reports reports/dojo reports/validation memories lessons configs usage
)

log() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "remote-deploy.sh must run as root (got uid $(id -u))." >&2
    exit 1
fi

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

log "Service user + directories"
id -u "$SVC_USER" >/dev/null 2>&1 || \
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
mkdir -p "$APP_DIR" /etc/spy-der "$STATE_DIR"

log "Code -> $DEPLOY_REF"
if [ ! -d "$APP_DIR/.git" ]; then
    git clone "$REPO_URL" "$APP_DIR"
fi
git -C "$APP_DIR" remote set-url origin "$REPO_URL"
git -C "$APP_DIR" fetch --prune origin
if git -C "$APP_DIR" rev-parse --verify -q "origin/${DEPLOY_REF#origin/}^{commit}" >/dev/null; then
    TARGET="origin/${DEPLOY_REF#origin/}"
else
    TARGET="$DEPLOY_REF"
fi
git -C "$APP_DIR" reset --hard "$TARGET"
echo "Deployed commit: $(git -C "$APP_DIR" rev-parse --short HEAD)"

log "Virtualenv + package"
if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
# Editable: the console scripts in pyproject.toml resolve against the checkout,
# so a later `git reset --hard` alone still moves the code. Re-running the
# install is what picks up NEW entry points and NEW dependencies — the step
# whose absence left `spy-der dashboard-api` unrunnable after it was written.
"$APP_DIR/venv/bin/pip" install --quiet -e "$APP_DIR"

log "State tree"
for sub in "${STATE_DIRS[@]}"; do
    mkdir -p "$STATE_DIR/$sub"
done
# Own the tree as the service user and leave it world-readable. Readable matters:
# the state under reports/ is a published surface that other local readers
# (a dashboard adapter running as its own user) must be able to open. The
# runtime writes reports 0644 for the same reason. Do not chown this tree to
# another user — the units declare StateDirectory=spy-der, and systemd resets
# ownership to spy-der on start, so a competing chown just flaps.
chown -R "$SVC_USER:$SVC_USER" "$STATE_DIR"
chmod 0755 "$STATE_DIR"
find "$STATE_DIR" -type d -exec chmod 0755 {} +
# Repair the published surface. The runtime writes these 0644, but reports
# produced before that fix landed are still 0600 — created by tempfile.mkstemp,
# whose mode os.replace carried onto the target. Those files are unreadable by
# any other local user, which is how a Dojo report that existed on disk showed
# up on the dashboard as a Dojo that had never run. Nothing under here is a
# secret; credentials live in /etc/spy-der/spy-der.env.
find "$STATE_DIR/reports" -type f -name '*.json' -exec chmod 0644 {} + 2>/dev/null || true
[ -f "$STATE_DIR/live_state.json" ] && chmod 0644 "$STATE_DIR/live_state.json"

# Publish what is deployed so the dashboard can answer "did my change land?"
# without anyone opening an SSH session.
cat > "$STATE_DIR/deploy.json" <<JSON
{
  "commit": "$(git -C "$APP_DIR" rev-parse HEAD)",
  "commit_short": "$(git -C "$APP_DIR" rev-parse --short HEAD)",
  "subject": "$(git -C "$APP_DIR" log -1 --pretty=%s | tr -d '"\\\\')",
  "committed_at": "$(git -C "$APP_DIR" log -1 --pretty=%cI)",
  "ref": "$DEPLOY_REF",
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
chmod 0644 "$STATE_DIR/deploy.json"

if [ -f "$CONFIG_FILE" ]; then
    echo "config: $CONFIG_FILE"
else
    install -m 0644 "$APP_DIR/deploy/config.yaml.example" "$CONFIG_FILE"
    echo "config: installed default -> $CONFIG_FILE"
fi

log "Self-update timer (pull-based deploys; inbound SSH not required)"
install -m 644 "$APP_DIR/deploy/spy-der-update.service" /etc/systemd/system/spy-der-update.service
install -m 644 "$APP_DIR/deploy/spy-der-update.timer" /etc/systemd/system/spy-der-update.timer
systemctl daemon-reload
systemctl enable --now spy-der-update.timer >/dev/null 2>&1 || true

log "systemd units"
for unit in "${SERVICES[@]}" "${TIMERS[@]}"; do
    if [ -f "$APP_DIR/deploy/$unit.service" ]; then
        install -m 644 "$APP_DIR/deploy/$unit.service" "/etc/systemd/system/$unit.service"
    fi
    if [ -f "$APP_DIR/deploy/$unit.timer" ]; then
        install -m 644 "$APP_DIR/deploy/$unit.timer" "/etc/systemd/system/$unit.timer"
    fi
done
systemctl daemon-reload

if [ ! -f "$ENV_FILE" ]; then
    printf '\n\033[1;33m%s\033[0m\n' "Secrets file $ENV_FILE not found — units NOT started." >&2
    cat >&2 <<EOF
Code is deployed, but the runtime needs credentials first. One-time setup:

    sudo install -D -m 640 -o root -g $SVC_USER \\
         $APP_DIR/deploy/spy-der.env.example $ENV_FILE
    sudo nano $ENV_FILE          # TRADIER_ACCESS_TOKEN and/or MASSIVE_API_KEY; XAI_API_KEY for the agent

The next self-update run will start them.
EOF
    exit 0
fi

log "Enable + restart services"
for unit in "${SERVICES[@]}"; do
    [ -f "/etc/systemd/system/$unit.service" ] || continue
    systemctl enable "$unit" >/dev/null 2>&1 || true
    systemctl restart "$unit"
done

log "Enable timers"
for unit in "${TIMERS[@]}"; do
    [ -f "/etc/systemd/system/$unit.timer" ] || continue
    systemctl enable --now "$unit.timer" >/dev/null 2>&1 || true
done

sleep 2
for unit in "${SERVICES[@]}"; do
    [ -f "/etc/systemd/system/$unit.service" ] || continue
    systemctl --no-pager --lines=0 status "$unit" || true
done

log "Deploy complete."
