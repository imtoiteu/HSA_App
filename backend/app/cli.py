"""Command line: `hsa-app <command>` (inside the api container: `docker compose exec api hsa-app …`)."""
import argparse
import getpass
import json
import logging
import os
import sys

from .config import get_settings
from .db import SessionLocal
from .logging_setup import setup_logging


def cmd_sync(a):
    from .sync.importer import sync_hsa
    s = get_settings()
    db = SessionLocal()
    try:
        run = sync_hsa(db, s.upstream_root, s.media_root, full=a.full, only=a.only or None, limit=a.limit,
                       triggered_by="cli",
                       progress=lambda st: print(f"  seen {st['seen']} built {st['created'] + st['updated']} "
                                                 f"unchanged {st['unchanged']} versions+{st['versions_created']}",
                                                 flush=True))
        print(json.dumps({"run": run.id, "status": run.status, **run.stats}, ensure_ascii=False, indent=1))
    finally:
        db.close()


def cmd_recompute(a):
    from .sync.importer import recompute_policy
    db = SessionLocal()
    print(recompute_policy(db))
    db.commit()


def cmd_seed(a):
    from .seed import seed
    db = SessionLocal()
    print(seed(db))


def cmd_create_admin(a):
    from .auth import hash_password, password_problem
    from .models import User
    from sqlalchemy import select
    pw = os.environ.get("HSA_ADMIN_PASSWORD") or getpass.getpass("Mật khẩu admin: ")
    prob = password_problem(pw)
    if prob:
        sys.exit(prob)
    db = SessionLocal()
    email = a.email.strip().lower()
    u = db.scalar(select(User).where(User.email == email))
    if u:
        u.role, u.password_hash, u.is_active = "admin", hash_password(pw), True
        print(f"updated admin {email}")
    else:
        db.add(User(email=email, display_name=a.name or "Quản trị viên", password_hash=hash_password(pw), role="admin"))
        print(f"created admin {email}")
    db.commit()


def cmd_maintenance(a):
    """Periodic housekeeping: expire orders, purge stale sessions/rate-limit windows."""
    from .auth import cleanup
    from .commerce.service import expire_orders
    db = SessionLocal()
    print({"expired_orders": expire_orders(db)})
    cleanup(db)
    run_queued_sync(db)


def run_queued_sync(db):
    """Run a sync requested from the admin UI (app_setting['sync_request']), if any."""
    from .models import AppSetting
    from .sync.importer import sync_hsa
    row = db.get(AppSetting, "sync_request")
    if not row or not row.value:
        return
    req = dict(row.value)
    row.value = {}
    db.commit()
    s = get_settings()
    try:
        os.nice(15)
    except OSError:
        pass
    try:
        run = sync_hsa(db, s.upstream_root, s.media_root, full=bool(req.get("full")),
                       triggered_by=f"admin:{req.get('by', '?')}")
        print(json.dumps({"sync_run": run.id, "status": run.status}, ensure_ascii=False))
    except RuntimeError as ex:  # another sync is running: keep the request for the next round
        db.rollback()
        row = db.get(AppSetting, "sync_request")
        row.value = req
        db.commit()
        print(f"sync postponed: {ex}")


def cmd_export_corrections(a):
    from .admin_ops import export_corrections
    db = SessionLocal()
    n = export_corrections(db, a.out, mark=not a.dry_run)
    print(f"exported {n} corrections to {a.out}")


def cmd_import_jsonl(a):
    from .sync.jsonl_import import import_jsonl
    db = SessionLocal()
    print(json.dumps(import_jsonl(db, a.bank, a.path, get_settings().media_root, name=a.name), ensure_ascii=False))


def main(argv=None):
    setup_logging()
    p = argparse.ArgumentParser(prog="hsa-app")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="synchronise the HSA upstream bank (idempotent, incremental)")
    s.add_argument("--full", action="store_true", help="rebuild every question (content hashes still dedupe)")
    s.add_argument("--only", nargs="*", help="only these cq_… ids")
    s.add_argument("--limit", type=int)
    s.set_defaults(fn=cmd_sync)
    sub.add_parser("recompute-policy", help="re-evaluate the serving policy").set_defaults(fn=cmd_recompute)
    sub.add_parser("seed", help="insert reference data (idempotent)").set_defaults(fn=cmd_seed)
    s = sub.add_parser("create-admin", help="create/promote an admin (password from HSA_ADMIN_PASSWORD or prompt)")
    s.add_argument("email")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_create_admin)
    sub.add_parser("maintenance", help="expire orders, purge stale sessions").set_defaults(fn=cmd_maintenance)
    s = sub.add_parser("export-corrections", help="write proposed corrections in the upstream proposal format")
    s.add_argument("out")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_export_corrections)
    s = sub.add_parser("import-jsonl", help="import/update an additional question bank from app-format JSONL")
    s.add_argument("bank")
    s.add_argument("path")
    s.add_argument("--name")
    s.set_defaults(fn=cmd_import_jsonl)
    a = p.parse_args(argv)
    logging.getLogger().setLevel(get_settings().log_level)
    a.fn(a)


if __name__ == "__main__":
    main()
