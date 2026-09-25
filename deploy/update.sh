#!/usr/bin/env bash
# Pulls the latest main, rebuilds what changed, and restarts the three
# systemd services. Run on the droplet, from anywhere, as the `deploy` user:
#   ./deploy/update.sh
#
# Safe to re-run after any failure: the last commit that was fully deployed
# (rebuilt + services restarted) is recorded in run/deployed_commit, and
# every run rebuilds against that -- not against whatever HEAD was before
# this run's pull -- so a failed pip/npm step or a declined schema prompt
# is picked up again next time instead of reporting "already up to date".
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

RUN_DIR="$REPO_DIR/run"
DEPLOYED_MARKER="$RUN_DIR/deployed_commit"
# Same file the dashboard's Stop button writes (process_control.request_stop):
# the scheduler finishes the ticker it's on, skips the rest of the cycle,
# and exits cleanly -- instead of being killed mid-order by SIGTERM.
SCHEDULER_STOP_FILE="$RUN_DIR/scheduler.stop_requested"
SERVICES=(bot-scheduler bot-watchdog bot-dashboard)

mkdir -p "$RUN_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
    echo "Working tree is dirty -- aborting. Investigate before pulling." >&2
    git status --short
    exit 1
fi

git pull --ff-only origin main
TARGET="$(git rev-parse HEAD)"

if [[ -f "$DEPLOYED_MARKER" ]]; then
    BASE="$(cat "$DEPLOYED_MARKER")"
    if ! git cat-file -e "${BASE}^{commit}" 2>/dev/null; then
        echo "run/deployed_commit ($BASE) is not a known commit -- doing a full deploy."
        BASE=""
    fi
else
    echo "No run/deployed_commit yet -- doing a full deploy (reinstall, rebuild, restart)."
    BASE=""
fi

if [[ -n "$BASE" && "$BASE" == "$TARGET" ]]; then
    echo "Already deployed ($TARGET). Nothing to do."
    exit 0
fi

echo "Deploying ${BASE:-<none>} -> $TARGET"

# True when $1 changed between BASE and TARGET, or always on a full deploy.
changed() {
    [[ -z "$BASE" ]] || ! git diff --quiet "$BASE" "$TARGET" -- "$1"
}

if changed pyproject.toml; then
    echo "Installing Python dependencies"
    .venv/bin/pip install -e .
fi

if changed frontend/; then
    echo "Rebuilding frontend"
    # npm ci installs exactly what package-lock.json says and never rewrites
    # it, so the tracked lockfile can't leave the tree dirty for next time.
    (cd frontend && npm ci && npm run build)
fi

if changed src/tradingsystem/db/models.py; then
    echo
    echo "!! src/tradingsystem/db/models.py changed (or this is a full deploy)."
    echo "!! There is no Alembic in this repo -- init_db.py only creates missing"
    echo "!! tables, it never alters existing ones. If this diff added, removed,"
    echo "!! or retyped a column, apply the matching ALTER TABLE against the"
    echo "!! live 'trading' database by hand before continuing, or the services"
    echo "!! below will start writing to columns that don't exist."
    echo
    if [[ -n "$BASE" ]]; then
        git diff "$BASE" "$TARGET" -- src/tradingsystem/db/models.py
        echo
    fi
    read -r -p "Schema change applied (or not needed)? [y/N] " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborting before restart -- services are untouched. Re-run once the" >&2
        echo "schema is updated; the deploy will pick up where it left off." >&2
        exit 1
    fi
fi

if systemctl is-active --quiet bot-scheduler; then
    echo "Asking the scheduler to stop cleanly (it finishes the ticker in progress)..."
    touch "$SCHEDULER_STOP_FILE"
    waited=0
    while systemctl is-active --quiet bot-scheduler; do
        if (( waited % 30 == 0 )); then
            echo "  waiting for the scheduler to exit (${waited}s)... Ctrl-C is safe; re-run to finish."
        fi
        sleep 5
        waited=$(( waited + 5 ))
    done
    echo "Scheduler stopped."
fi
# The scheduler clears this itself on a clean exit; remove it regardless so
# the freshly started scheduler doesn't immediately honor a stale request.
rm -f "$SCHEDULER_STOP_FILE"

echo "Restarting services..."
sudo systemctl restart "${SERVICES[@]}"
sleep 3

# Checked one unit at a time: `systemctl is-active a b c` succeeds if ANY
# of them is active, which would hide a service that failed to start.
all_active=true
for svc in "${SERVICES[@]}"; do
    systemctl is-active --quiet "$svc" || all_active=false
done

if $all_active; then
    echo "$TARGET" > "$DEPLOYED_MARKER"
    systemctl status --no-pager "${SERVICES[@]}" || true
    echo
    echo "Deployed $TARGET."
else
    systemctl status --no-pager "${SERVICES[@]}" || true
    echo >&2
    echo "Code for $TARGET is in place, but not every service is active (see above)." >&2
    echo "Check 'journalctl -u <service> -n 50', fix, and re-run ./deploy/update.sh." >&2
    exit 1
fi
