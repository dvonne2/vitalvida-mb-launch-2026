#!/bin/bash
#
# VitalVida Daily Backup Script
# Runs nightly via cron at 2 AM Lagos time.
#
# What it does:
# 1. Runs bench backup --with-files for the production site
# 2. Pushes the latest backup to offsite storage (Google Drive via rclone)
# 3. Logs everything to /home/frappe/scripts/backup.log
# 4. Sends an alert email if anything fails
#

set -euo pipefail  # Fail fast on errors

# ── Configuration ─────────────────────────────────────────────
SITE="vitalvida.systemforce.ng"
BENCH_DIR="/home/frappe/frappe-bench"
BACKUP_DIR="$BENCH_DIR/sites/$SITE/private/backups"
LOG_FILE="/home/frappe/scripts/backup.log"
RCLONE_REMOTE="vv-backups"
RCLONE_FOLDER="vitalvida-erpnext-backups"
ALERT_EMAIL="admin@vitalvida.ng"  # Replace with real admin email

# ── Helper functions ──────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

alert_failure() {
    local message="$1"
    log "❌ FAILURE: $message"
    # Send alert email (uses Frappe's configured SMTP)
    cd "$BENCH_DIR"
    bench --site "$SITE" execute frappe.sendmail \
        --kwargs "{
            'recipients': ['$ALERT_EMAIL'],
            'subject': 'VitalVida Backup FAILED — $(date +%Y-%m-%d)',
            'message': '$message\n\nCheck $LOG_FILE on the server.',
            'now': True
        }" 2>&1 | tee -a "$LOG_FILE" || true
    exit 1
}

# ── Step 1: Run bench backup ──────────────────────────────────
log "─────────────────────────────────────────"
log "Starting daily backup for $SITE"

cd "$BENCH_DIR"

if ! bench --site "$SITE" backup --with-files 2>&1 | tee -a "$LOG_FILE"; then
    alert_failure "bench backup command failed"
fi

log "✓ bench backup completed"

# ── Step 2: Find the latest backup files ──────────────────────
LATEST_DB=$(ls -t "$BACKUP_DIR"/*-database.sql.gz 2>/dev/null | head -1)
LATEST_FILES=$(ls -t "$BACKUP_DIR"/*-files.tar 2>/dev/null | head -1)
LATEST_PRIVATE=$(ls -t "$BACKUP_DIR"/*-private-files.tar 2>/dev/null | head -1)
LATEST_CONFIG=$(ls -t "$BACKUP_DIR"/*-site_config_backup.json 2>/dev/null | head -1)

if [ -z "$LATEST_DB" ]; then
    alert_failure "No database backup file found after bench backup"
fi

log "Latest backup files:"
log "  Database: $(basename "$LATEST_DB") ($(du -h "$LATEST_DB" | cut -f1))"
[ -n "$LATEST_FILES" ] && log "  Public files: $(basename "$LATEST_FILES") ($(du -h "$LATEST_FILES" | cut -f1))"
[ -n "$LATEST_PRIVATE" ] && log "  Private files: $(basename "$LATEST_PRIVATE") ($(du -h "$LATEST_PRIVATE" | cut -f1))"
[ -n "$LATEST_CONFIG" ] && log "  Config: $(basename "$LATEST_CONFIG")"

# ── Step 3: Push to offsite storage via rclone ────────────────
log "Syncing to offsite storage ($RCLONE_REMOTE:$RCLONE_FOLDER)..."

# Create a dated subfolder so we can navigate historical backups
TODAY=$(date +%Y-%m-%d)
REMOTE_PATH="$RCLONE_REMOTE:$RCLONE_FOLDER/$TODAY"

# Upload each backup file
for file in "$LATEST_DB" "$LATEST_FILES" "$LATEST_PRIVATE" "$LATEST_CONFIG"; do
    if [ -n "$file" ] && [ -f "$file" ]; then
        if rclone copy "$file" "$REMOTE_PATH/" \
            --log-file="$LOG_FILE" \
            --log-level=INFO \
            --transfers=2 \
            --checkers=4 \
            --retries=3; then
            log "✓ Uploaded $(basename "$file")"
        else
            alert_failure "Failed to upload $(basename "$file") to $REMOTE_PATH"
        fi
    fi
done

# ── Step 4: Cleanup old offsite backups (keep last 30 days) ───
log "Cleaning up offsite backups older than 30 days..."
if rclone delete "$RCLONE_REMOTE:$RCLONE_FOLDER/" \
    --min-age 30d \
    --log-file="$LOG_FILE" \
    --log-level=INFO; then
    log "✓ Old backups cleaned"
else
    log "⚠ Cleanup had errors (non-fatal, continuing)"
fi

# ── Done ──────────────────────────────────────────────────────
log "✅ Daily backup complete: $TODAY"
log "─────────────────────────────────────────"
