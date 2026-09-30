# Deployment & operations

Target: a single VPS (tested on 8 GB RAM / 4 vCPU shared with other projects) running Docker.

## Services (`docker-compose.yml`, project `hsaapp`)

| service | image | role | exposure | limits |
|---|---|---|---|---|
| `db` | postgres:16-alpine | application database (volume `pgdata`) | internal only | 640 MB, 1.5 CPU |
| `api` | hsaapp-api | migrations + seed on start, FastAPI (2 uvicorn workers) | internal only | 900 MB, 1.5 CPU |
| `scheduler` | hsaapp-api | every 5 min: expire orders, purge stale sessions/rate-limit windows, run syncs queued from the admin UI (niced) | internal only | 900 MB, 1 CPU |
| `web` | hsaapp-web | nginx: SPA, `/media` (immutable cache), `/api` proxy, rate limits, security headers | `${WEB_BIND}:${WEB_PORT}` (default `0.0.0.0:8620`) | 96 MB |
| `backup` | postgres:16-alpine | daily `pg_dump -Fc` to `${BACKUP_PATH}`, keeps `${BACKUP_KEEP_DAYS}` days | internal only | 128 MB |

The upstream bank is bind-mounted **read-only** into `api` and `scheduler` at `/upstream`. Media lives in the
`media` volume (content-addressed; mounted read-only into `web`). Nothing else on the host is
touched; the database is never published. Only port 8620 was chosen after checking which ports
were free (80 is used by Apache, 3000/8000/8080/8090/8501/9010/9080 by other projects).

## First deployment

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # → HSA_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # → POSTGRES_PASSWORD
$EDITOR .env
deploy/deploy.sh
docker compose exec api hsa-app create-admin you@example.com     # prompts for the password
docker compose run --rm scheduler nice -n 15 hsa-app sync         # first import (hours on a busy host)
```

On hosts with a slow registry connection: `OFFLINE_WHEELS=1` fills `backend/wheels/` from the local
pip cache and `WEB_DOCKERFILE=Dockerfile.prebuilt` builds the SPA on the host (Node ≥ 18).

## TLS / domain

The stack serves plain HTTP on 8620. For a public domain put a TLS reverse proxy in front (e.g. an
Apache/nginx/Caddy vhost proxying to `127.0.0.1:8620`), set `WEB_BIND=127.0.0.1`,
`HSA_PUBLIC_BASE_URL=https://your.domain` and `HSA_COOKIE_SECURE=true`, then `deploy/deploy.sh`.
Payment webhooks should only be enabled over HTTPS.

## Updating

```bash
git pull && deploy/deploy.sh          # migrations run automatically on api start
```

## Question-bank synchronisation

`hsa-app sync` is incremental and safe to run any time. From Admin → Ngân hàng & đồng bộ, "Đồng bộ
ngay" queues a request that the scheduler runs within ~5 minutes. Never run a sync inside the `api`
container: it is CPU-heavy and shares the API workers' CPU quota (uvicorn restarts workers that miss
their health ping). Behaviour:
unchanged questions are skipped via a hash of their raw upstream inputs; changed content creates a
new immutable version; completed exams keep the versions they used. Interrupted runs resume from
their checkpoint. Only one sync runs at a time (advisory lock). Suggested cadence: after each
upstream editorial release, e.g. a host cron entry

```
30 4 * * *  cd /root/imtoiteu/HSA-app && docker compose run --rm -T scheduler nice -n 15 hsa-app sync >> backups/sync.log 2>&1
```

## Backups & restore

Daily dumps: `${BACKUP_PATH}/hsa_YYYYmmdd_HHMM.dump`. The media volume is fully reproducible from the
upstream bank (`hsa-app sync --full`), but can also be archived:
`docker run --rm -v hsaapp_media:/m -v $PWD/backups:/b alpine tar czf /b/media.tgz -C /m .`

Restore into a fresh stack:

```bash
docker compose up -d db
docker compose exec -T db pg_restore -U hsa -d hsa --clean --if-exists < backups/hsa_YYYYmmdd_HHMM.dump
docker compose up -d
```

## Health, logs, monitoring

* `GET /api/health` (DB check) — used by container health checks; `GET /healthz` on nginx.
* Logs are JSON lines on stdout (`docker compose logs -f api`), rotated by Docker (10 MB × 5).
* Slow SQL (>1 s) is logged by Postgres.
* Admin → Tổng quan shows users, sessions, served questions, open reports, revenue, pending orders,
  unmatched transactions and the last sync run.

## Security notes

argon2id password hashing; opaque server-side sessions (HttpOnly, SameSite=Lax cookies; Bearer for
mobile clients); CSRF double-submit for cookie sessions; DB-backed rate limits on auth/register/
reset/orders/reports plus nginx `limit_req`; strict CSP and security headers; API docs disabled in
production; secrets only in `.env`; webhook secrets compared in constant time.
