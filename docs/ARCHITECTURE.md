# HSA-app — Architecture

Vietnamese practice & mock-examination platform for HSA (Đánh giá năng lực ĐHQGHN) and related
exams. The question content comes from the separate, **read-only** upstream project
`/root/imtoiteu/HSA-question-bank` (see [INTEGRATION_CONTRACT.md](INTEGRATION_CONTRACT.md)).

## Decisions

| Concern | Choice | Why |
|---|---|---|
| API | Python 3.12 · FastAPI · SQLAlchemy 2 (sync, psycopg 3) · Alembic | same language as the upstream pipeline (SQLite, WMF/Pillow tooling), typed JSON API usable by a future mobile client |
| DB | PostgreSQL 16 | transactional sync, JSONB for immutable content snapshots, row-level constraints/triggers for immutability |
| Web client | React 18 · TypeScript · Vite SPA, plain CSS design system | fast, static, cacheable; no Node process in production |
| Math | KaTeX (client), LaTeX from the canonical formula records; fallback to the source preview image | robust, fast, no Word dependency |
| Assets | content-addressed store `sha256[:2]/sha256.ext`, WMF/EMF converted to PNG at sync time, served by nginx with `immutable` caching | dedup by construction, cheap to serve |
| Edge | nginx container (static SPA + `/media` + reverse proxy `/api`) | one public port, rate limiting, gzip |
| Deployment | docker compose: `db`, `api`, `web`, `backup` | reproducible, isolated from the other projects on the VPS |
| Auth | argon2id passwords, opaque server-side session tokens (HttpOnly cookie **or** `Authorization: Bearer`), CSRF double-submit for cookie sessions, DB-backed rate limits | simple, revocable, mobile-ready |
| Payments | provider-agnostic layer: VietQR (EMVCo) payload generated locally, webhook adapters (SePay, Casso, generic HMAC), manual admin reconciliation | no hard-coded bank data or secrets |

Resource budget (shared 8 GB / 4 vCPU host): Postgres `shared_buffers=128MB`, `max_connections=40`;
API = 1 uvicorn process by default (~120 MB; sync endpoints run in its threadpool; see `HSA_WORKERS`); nginx ~10 MB. All containers have memory limits.

## Components

```
 browser ──:8620──▶ web (nginx, host network) ──unix socket──▶ api (uvicorn/FastAPI) ──▶ db (postgres)
                     │ /media/* content-addressed files (read-only volume)       ▲ private bridge (MTU 65000)
                     └ /* SPA (index.html + pre-compressed hashed bundles)       │
 upstream (read-only bind mount) ──▶ scheduler: maintenance + queued `hsa-app sync` (niced) ──┘   backup ──▶ ./backups
```

Operational notes from the production host (CPU fully saturated by other tenants, kernel softirq
backlog drops visible in `/proc/net/softnet_stat`):

* nginx runs with `network_mode: host`: no docker-proxy, no bridge hop for client traffic, and the
  real client IP is available for rate limiting;
* nginx ↔ API use a Unix domain socket (a shared volume) instead of TCP over the bridge;
* the private bridge (API ↔ Postgres, scheduler ↔ Postgres) uses a jumbo MTU;
* static assets are pre-compressed (`gzip_static`), API JSON is compressed once in-process;
* one uvicorn process by default (uvicorn 0.32 kills workers that miss a 5 s ping when starved);
* the synchronisation runs in the scheduler, never inside API workers.

## Backend modules (`backend/app`)

| Module | Responsibility |
|---|---|
| `config.py` | all configuration from environment variables |
| `models.py` | SQLAlchemy schema (mirrored by Alembic migrations) |
| `content/` | hsa-md → render-ready block model; answer normalisation |
| `sync/` | upstream reader, editorial-state derivation, serving policy, asset store, idempotent import |
| `exam/` | blueprint validation, deterministic selection, sessions, scoring |
| `commerce/` | practice plans and the FREE pool (`access.py`), orders, PRO subscriptions, VietQR payloads, provider adapters, exam entitlements |
| `api/` | HTTP routers: auth, catalog, sessions, me (history/bookmarks/reports), payments, admin |
| `cli.py` | `sync`, `create-admin`, `seed`, `expire-orders`, `export-corrections` |

## Data model (summary)

* **Content**: `question_bank` (a source; HSA upstream is bank `hsa`, others can be imported) →
  `question` (identity `(bank_id, external_id=cq_…)`, current version pointer, subject, type,
  upstream states, *serving policy result*, admin override) → `question_version`
  (**immutable** render-ready snapshot + normalised answer, keyed by content hash).
  `asset` rows describe files in the content-addressed store.
* **Exams**: `exam_blueprint` (data-driven format: sections, pools, counts, timing, scoring,
  option shuffling, price) → `exam_session` (seed, blueprint snapshot, scoring config snapshot,
  timestamps, status) → `exam_item` (position → `question_version_id`, option order) →
  `exam_response` (answer, flag, time). Results are written once at submission.
* **Users**: `app_user`, `auth_session`, `password_reset`, `bookmark`, `question_report`,
  `question_correction` (the correction/feedback layer keyed by `cq_…`).
* **Commerce**: `plan` (FREE/PRO), `plan_subscription` (PRO periods, one per order, never overwritten),
  `product`, `payment_order` (unique reference; amount/reference/destination frozen by a trigger),
  `payment_order_event` (state history), `payment_transaction` (unique per provider transaction id →
  idempotent), `entitlement`, `entitlement_usage`. See [PAYMENTS.md](PAYMENTS.md).
* **Ops**: `sync_run`, `app_setting` (serving policy, payment receiving account, feature flags),
  `audit_log`, `rate_limit`.

## Immutability guarantees

1. `question_version` rows are never updated or deleted (DB trigger). A new editorial version
   creates a new row; `question.current_version_id` moves, old rows stay.
2. `exam_item` rows point to the exact `question_version_id` shown to the student, plus the
   option permutation; they are written once when the session is created.
3. After submission, `exam_item`, `exam_response` and the session's scoring fields are frozen
   (DB trigger rejects updates once `status='submitted'`).
4. Review pages always render from the session's frozen items — never from a new selection
   and never from `question.current_version_id`.

## Determinism

* Selection: every candidate *unit* (a standalone question, or a whole passage group) gets the
  key `sha256(seed ‖ blueprint_section ‖ unit_key)`; units are taken in key order until the
  section count is met. Option permutation: labels sorted by `sha256(seed ‖ external_id ‖ label)`.
  This is independent of Python's PRNG implementation.
* Each session records `seed`, `generator_version`, blueprint snapshot, scoring snapshot,
  exact items and pool fingerprint, so it is reproducible without re-running the generator.
* Scoring is a pure function of (frozen items, answers, scoring config) and is unit-tested.
