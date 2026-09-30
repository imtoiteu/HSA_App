#!/bin/sh
# api        : migrate, seed reference data, serve
# scheduler  : periodic maintenance (expire orders, purge sessions)
# anything else is executed as-is (e.g. `hsa-app sync`)
set -e
case "$1" in
  api)
    alembic upgrade head
    hsa-app seed
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${HSA_WORKERS:-2}" \
         --proxy-headers --forwarded-allow-ips='*' --no-server-header --timeout-keep-alive 15
    ;;
  scheduler)
    while true; do
      hsa-app maintenance || echo "maintenance failed"
      sleep "${HSA_MAINTENANCE_INTERVAL:-300}"
    done
    ;;
  *)
    exec "$@"
    ;;
esac
