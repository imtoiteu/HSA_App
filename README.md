# HSA-app — Luyện thi HSA

Vietnamese practice & mock-examination platform for the HSA / Đánh giá năng lực (ĐHQG Hà Nội)
exam. Students practise by subject, take timed full mock exams that follow the real 3-part
structure, get deterministic scores, review answers and worked solutions, bookmark and report
questions. Admins curate the question pool, design exam formats, set prices, reconcile payments
and watch usage — all from the web UI.

Question content comes **read-only** from the separate project
[`HSA-question-bank`](docs/INTEGRATION_CONTRACT.md) (canonical SQLite + editorial overlays,
immutable `cq_…` ids). The DOCX/HTML editorial outputs of that project are never used as data.

| | |
|---|---|
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Upstream integration contract | [docs/INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md) |
| Which questions students get | [docs/SERVING_POLICY.md](docs/SERVING_POLICY.md) |
| Exam blueprints (formats as data) | [docs/EXAM_BLUEPRINTS.md](docs/EXAM_BLUEPRINTS.md) |
| Payments & access model | [docs/PAYMENTS.md](docs/PAYMENTS.md) |
| Importing other question banks | [docs/QUESTION_IMPORT_FORMAT.md](docs/QUESTION_IMPORT_FORMAT.md) |
| Deployment, backups, operations | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |

## Stack

FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16 · React 18 + TypeScript (Vite) · KaTeX · nginx ·
docker compose. See the architecture document for the reasoning.

## Repository layout

```
backend/            API, sync layer, exam engine, commerce, CLI (`hsa-app …`), tests, Alembic migrations
frontend/           React SPA (student app + admin UI), nginx config, Dockerfiles
deploy/             deploy.sh, backup.sh
docs/               design & operations documentation
docker-compose.yml  production stack (db, api, scheduler, web, backup)
.env.example        configuration template (copy to .env; never commit .env)
```

## Quick start (development)

```bash
# database for development/tests
docker run -d --name hsaapp-testdb -e POSTGRES_USER=hsa -e POSTGRES_PASSWORD=test -e POSTGRES_DB=hsa_test \
  -p 127.0.0.1:54340:5432 postgres:16-alpine

cd backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
export HSA_DATABASE_URL=postgresql+psycopg://hsa:test@127.0.0.1:54340/hsa_test \
       HSA_UPSTREAM_ROOT=/root/imtoiteu/HSA-question-bank HSA_MEDIA_ROOT=$PWD/../data/media
.venv/bin/alembic upgrade head && .venv/bin/hsa-app seed
.venv/bin/hsa-app sync --limit 2000          # a slice of the real bank (read-only)
.venv/bin/uvicorn app.main:app --reload       # API on :8000

cd ../frontend && npm install && npm run dev  # SPA on :5180, proxies /api and /media
```

## Tests

```bash
cd backend && .venv/bin/pytest -q                 # 78 tests (fixture upstream + API flows)
.venv/bin/pytest -q -m upstream                   # extra checks on real upstream records (read-only)
cd frontend && npm test                           # renderer/answer-input component tests
```

The backend suite covers data import (idempotency, versioning, removal, restartability, overlays,
manifest precedence), question eligibility, rendering metadata, answer persistence and resume,
deterministic scoring, randomized-exam reproducibility, completed-exam immutability (DB triggers),
shared passage groups, duplicate prevention, payments (VietQR, webhooks, idempotency, manual
reconciliation, entitlements), auth/CSRF/rate limiting and the admin API.

## Production

```bash
cp .env.example .env    # fill in secrets
deploy/deploy.sh        # build, migrate, seed, start, health-check
docker compose exec api hsa-app create-admin admin@example.com   # first admin (prompts for password)
docker compose exec api hsa-app sync                              # import/refresh the question bank
```

Details, backups and restore in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
