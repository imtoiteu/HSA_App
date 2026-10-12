#!/bin/sh
# api        : migrate, seed reference data, serve
# scheduler  : worker loop (Review Studio sources, maintenance, review refresh)
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
    # source documents for the Review Studio, maintenance and the incremental review refresh
    while true; do
      hsa-app worker --no-sources || echo "worker exited"
      sleep 10
    done
    ;;
  sources)
    # Review Studio source documents (runs as root with reduced capabilities: the upstream archives are
    # readable by root only; the mount is read-only)
    while true; do
      hsa-app worker --sources-only || echo "sources worker exited"
      sleep 10
    done
    ;;
  *)
    exec "$@"
    ;;
esac
