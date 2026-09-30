#!/bin/sh
# Daily logical backup of the application database (runs inside the `backup` container).
# Keeps BACKUP_KEEP_DAYS days of compressed custom-format dumps in /backups.
# Restore: docker compose exec -T db pg_restore -U hsa -d hsa --clean --if-exists < backups/hsa_YYYYmmdd_HHMM.dump
set -eu
KEEP="${BACKUP_KEEP_DAYS:-14}"
HOUR="${BACKUP_HOUR:-3}"

backup() {
  ts=$(date +%Y%m%d_%H%M)
  tmp="/backups/.hsa_${ts}.dump.part"
  if pg_dump -Fc -Z 6 -f "$tmp"; then
    mv "$tmp" "/backups/hsa_${ts}.dump"
    echo "$(date -Iseconds) backup ok: hsa_${ts}.dump ($(du -h "/backups/hsa_${ts}.dump" | cut -f1))"
  else
    rm -f "$tmp"
    echo "$(date -Iseconds) backup FAILED" >&2
  fi
  find /backups -name 'hsa_*.dump' -mtime +"$KEEP" -delete
}

# one backup at start-up if none from today exists, then daily at HOUR:00
ls /backups/hsa_$(date +%Y%m%d)_*.dump >/dev/null 2>&1 || backup
while true; do
  now=$(date +%s)
  next=$(date -d "$(date +%Y-%m-%d) ${HOUR}:00:00" +%s 2>/dev/null || echo $((now + 86400)))
  [ "$next" -le "$now" ] && next=$((next + 86400))
  sleep $((next - now))
  backup
done
