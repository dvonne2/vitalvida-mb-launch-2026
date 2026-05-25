#!/bin/bash


set -euo pipefail

# ── Configuration ─────────────────────────────────────────────
SITE="vitalvida.systemforce.ng"
BENCH_DIR="/home/frappe/frappe-bench"
RESTORE_DIR="/home/frappe/restore-tests"
RCLONE_REMOTE="vv-backups"
RCLONE_FOLDER="vitalvida-erpnext-backups"
LOG_FILE="/home/frappe/scripts/restore-test.log"
ALERT_EMAIL="admin@vitalvida.ng"

# Temporary site name (will be created and torn down)
TEST_SITE="restore-test-$(date +%Y%m%d-%H%M%S).vitalvida.local"

# Critical doctypes that must have rows in a healthy restore
CRITICAL_DOCTYPES=("VV Order" "VV Media Buyer" "Delivery Agent" "User" "Item")
MIN_ROWS_EXPECTED=1  # At least 1 row in each

# ── Helper functions ──────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

cleanup() {
    log "Cleaning up test site $TEST_SITE..."
    cd "$BENCH_DIR"
    if bench --site "$TEST_SITE" drop-site --no-backup --root-password "$(grep -oP '"db_password":\s*"\K[^"]+' sites/$SITE/site_config.json)" --force 2>&1 | tee -a "$LOG_FILE"; then
        log "✓ Test site dropped"
    else
        log "⚠ Could not drop test site (manual cleanup may be needed)"
    fi
    rm -rf "$RESTORE_DIR"
}

# Always clean up, even on failure
trap cleanup EXIT

alert() {
    local subject="$1"
    local body="$2"
    cd "$BENCH_DIR"
    bench --site "$SITE" execute frappe.sendmail \
        --kwargs "{
            'recipients': ['$ALERT_EMAIL'],
            'subject': '$subject',
            'message': '$body',
            'now': True
        }" 2>&1 | tee -a "$LOG_FILE" || true
}

# ── Step 1: Find the latest offsite backup ────────────────────
log "─────────────────────────────────────────"
log "Starting restore test"

mkdir -p "$RESTORE_DIR"
cd "$RESTORE_DIR"

# Find the most recent backup source
if command -v rclone &> /dev/null; then
    LATEST_DATE=$(rclone lsd "$RCLONE_REMOTE:$RCLONE_FOLDER/" | awk '{print $5}' | sort -r | head -1)
    if [ -z "$LATEST_DATE" ]; then
        alert "VitalVida Restore Test FAILED" "No backups found in $RCLONE_REMOTE:$RCLONE_FOLDER"
        exit 1
    fi
    log "Latest backup found: $LATEST_DATE"
    log "Downloading backup files..."
    if ! rclone copy "$RCLONE_REMOTE:$RCLONE_FOLDER/$LATEST_DATE/" "$RESTORE_DIR/" --log-file="$LOG_FILE"; then
        alert "VitalVida Restore Test FAILED" "Could not download backups from $LATEST_DATE"
        exit 1
    fi
else
    log "ℹ rclone not installed — simulating offsite download (Staging mode)"
    LATEST_DATE=$(date +%Y-%m-%d)
    # Copy from local bench backups directly to simulate download
    cp "$BENCH_DIR/sites/$SITE/private/backups/"* "$RESTORE_DIR/" 2>/dev/null || true
fi

DB_FILE=$(ls "$RESTORE_DIR"/*-database.sql.gz 2>/dev/null | head -1)
FILES_TAR=$(ls "$RESTORE_DIR"/*-files.tar 2>/dev/null | head -1)
PRIVATE_TAR=$(ls "$RESTORE_DIR"/*-private-files.tar 2>/dev/null | head -1)

if [ -z "$DB_FILE" ]; then
    alert "VitalVida Restore Test FAILED" "Database backup file missing from $LATEST_DATE"
    exit 1
fi

log "✓ Downloaded: $(basename "$DB_FILE")"

# ── Step 2: Create the test site ──────────────────────────────
cd "$BENCH_DIR"

# Get the MariaDB root password from production site_config
DB_ROOT_PW=$(python3 -c "import json; print(json.load(open('sites/$SITE/site_config.json'))['db_password'])")

log "Creating temporary site: $TEST_SITE"
if ! bench new-site "$TEST_SITE" \
    --mariadb-root-password "$DB_ROOT_PW" \
    --admin-password "restore-test-admin-pw" \
    --no-mariadb-socket 2>&1 | tee -a "$LOG_FILE"; then
    alert "VitalVida Restore Test FAILED" "Could not create test site"
    exit 1
fi

log "✓ Test site created"

# ── Step 3: Restore the backup ────────────────────────────────
log "Restoring backup into test site..."

RESTORE_ARGS=("--mariadb-root-password" "$DB_ROOT_PW" "$DB_FILE")
[ -n "$FILES_TAR" ] && RESTORE_ARGS+=("--with-public-files" "$FILES_TAR")
[ -n "$PRIVATE_TAR" ] && RESTORE_ARGS+=("--with-private-files" "$PRIVATE_TAR")

if ! bench --site "$TEST_SITE" restore "${RESTORE_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"; then
    alert "VitalVida Restore Test FAILED" "bench restore command failed for $LATEST_DATE backup"
    exit 1
fi

log "✓ Restore completed"

# ── Step 4: Sanity checks ─────────────────────────────────────
log "Running sanity checks..."

CHECK_FAILED=0
CHECK_RESULTS=""

for doctype in "${CRITICAL_DOCTYPES[@]}"; do
    # Use bench execute to query the doctype safely
    COUNT=$(bench --site "$TEST_SITE" execute frappe.db.count --args "['$doctype']" 2>&1 | tail -1 | tr -d '[:space:]')

    if [[ ! "$COUNT" =~ ^[0-9]+$ ]]; then
        log "  ❌ $doctype: could not query (got: $COUNT)"
        CHECK_FAILED=1
        CHECK_RESULTS+="$doctype: QUERY FAILED\n"
    elif [ "$COUNT" -lt "$MIN_ROWS_EXPECTED" ]; then
        log "  ❌ $doctype: only $COUNT rows (expected $MIN_ROWS_EXPECTED+)"
        CHECK_FAILED=1
        CHECK_RESULTS+="$doctype: $COUNT rows (FAIL)\n"
    else
        log "  ✓ $doctype: $COUNT rows"
        CHECK_RESULTS+="$doctype: $COUNT rows (OK)\n"
    fi
done

# ── Step 5: Report results ────────────────────────────────────
if [ "$CHECK_FAILED" -eq 0 ]; then
    log "✅ All sanity checks passed for $LATEST_DATE backup"
    alert "VitalVida Restore Test PASSED — $LATEST_DATE" \
        "Weekly backup restore test succeeded.\n\nBackup date: $LATEST_DATE\n\nDoctype row counts:\n$CHECK_RESULTS"
else
    log "❌ Sanity checks FAILED for $LATEST_DATE backup"
    alert "VitalVida Restore Test FAILED — $LATEST_DATE" \
        "Backup restore succeeded but data sanity checks failed.\n\nBackup date: $LATEST_DATE\n\nResults:\n$CHECK_RESULTS\n\nInvestigate immediately."
    # Don't exit 1 here — let cleanup run, but flag as failed
fi

log "─────────────────────────────────────────"
