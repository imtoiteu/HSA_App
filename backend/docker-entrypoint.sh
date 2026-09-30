#!/bin/sh
# api        : migrate, seed reference data, serve
# scheduler  : periodic maintenance (expire orders, purge sessions)
# anything else is executed as-is (e.g. `hsa-app sync`)
set -e
case "$1" in
  api)
    alembic upgrade head
    hsa-app seed
    # Unix domain socket shared with nginx (no TCP hop between them); world-writable so the nginx
    # worker (other uid) can connect. Set HSA_BIND_TCP=1 to listen on :8000 instead.
    if [ "${HSA_BIND_TCP:-0}" = "1" ]; then
      exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${HSA_WORKERS:-1}" \
           --proxy-headers --forwarded-allow-ips='*' --no-server-header --timeout-keep-alive 15
    fi
    rm -f /run/hsa/api.sock
    umask 000
    exec uvicorn app.main:app --uds /run/hsa/api.sock --workers "${HSA_WORKERS:-1}" \
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
