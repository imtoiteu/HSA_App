"""Command line: `hsa-app <command>` (inside the api container: `docker compose exec api hsa-app …`)."""
import argparse
import getpass
import json
import logging
import os
import sys
from pathlib import Path

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
    run_queued_audit(db)


def run_queued_audit(db):
    """Run a reconciliation report requested from the admin UI (app_setting['audit_request'])."""
    from .models import AppSetting
    from .settings_store import set_setting
    from .sync.audit import run_audit
    row = db.get(AppSetting, "audit_request")
    if not row or not row.value:
        return
    req = dict(row.value)
    row.value = {}
    db.commit()
    s = get_settings()
    try:
        os.nice(10)
    except OSError:
        pass
    r = run_audit(db, s.upstream_root, s.media_root, deep=bool(req.get("deep")))
    r["requested_by"] = req.get("by")
    set_setting(db, "question_bank_audit", r)
    db.commit()
    print("audit stored", json.dumps(r["reconciliation"], ensure_ascii=False)[:500])


def run_queued_sync(db):
    """Run a sync requested from the admin UI (app_setting['sync_request']), if any."""
    from .models import AppSetting
    from .sync.importer import sync_hsa
    row = db.get(AppSetting, "sync_request")
    if not row or not row.value:
        resume_orphaned_sync(db)
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


def cmd_audit(a):
    from .settings_store import set_setting
    from .sync.audit import run_audit, to_markdown
    s = get_settings()
    db = SessionLocal()
    r = run_audit(db, s.upstream_root, s.media_root, bank_code=a.bank, deep=a.deep)
    set_setting(db, "question_bank_audit", r)
    db.commit()
    if a.out:
        Path(a.out).write_text(to_markdown(r) if a.out.endswith(".md") else json.dumps(r, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    for k, v in r["reconciliation"].items():
        print(f"{k:<62} {v}")


def cmd_export_tex(a):
    """Distinct LaTeX strings of all current question versions → JSON for scripts/katex-check.mjs."""
    import hashlib
    from sqlalchemy import select
    from .models import FormulaCheck, Question, QuestionVersion
    from .sync.subjects import collect_tex
    db = SessionLocal()
    done = set() if a.all else set(db.scalars(select(FormulaCheck.tex_sha)))
    tex = set()
    for content, in db.execute(select(QuestionVersion.content).join(
            Question, Question.current_version_id == QuestionVersion.id).execution_options(yield_per=1000)):
        tex |= collect_tex(content)
    items = [{"sha": hashlib.sha256(t.encode("utf-8")).hexdigest(), "tex": t} for t in sorted(tex)]
    items = [i for i in items if i["sha"] not in done]
    out = sys.stdout if a.out == "-" else open(a.out, "w", encoding="utf-8")
    json.dump(items, out, ensure_ascii=False)
    print(f"exported {len(items)} formulas", file=sys.stderr)


def cmd_import_tex_check(a):
    import hashlib
    from sqlalchemy.dialects.postgresql import insert
    from .models import FormulaCheck
    from .sync.importer import apply_formula_checks
    db = SessionLocal()
    results = json.load(open(a.results, encoding="utf-8"))
    tex = {i["sha"]: i["tex"] for i in json.load(open(a.formulas, encoding="utf-8"))}
    rows = [{"tex_sha": r["sha"], "tex": tex[r["sha"]], "ok": bool(r["ok"]), "error": r.get("error"),
             "renderer": a.renderer} for r in results if r["sha"] in tex]
    for i in range(0, len(rows), 2000):
        chunk = rows[i:i + 2000]
        st = insert(FormulaCheck).values(chunk)
        db.execute(st.on_conflict_do_update(index_elements=["tex_sha"], set_={
            "ok": st.excluded.ok, "error": st.excluded.error, "renderer": st.excluded.renderer,
            "checked_at": st.excluded.checked_at}))
    db.commit()
    print(json.dumps({"imported": len(rows), "failing": sum(1 for r in rows if not r["ok"]),
                      **apply_formula_checks(db)}))
    db.commit()


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
    s = sub.add_parser("audit", help="question-bank reconciliation report (stored for the admin UI)")
    s.add_argument("--bank", default="hsa")
    s.add_argument("--deep", action="store_true", help="also detect upstream content changes since the last sync")
    s.add_argument("--out", help="write the report (.md or .json)")
    s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("export-tex", help="export formulas for the KaTeX renderer check")
    s.add_argument("out", nargs="?", default="-")
    s.add_argument("--all", action="store_true", help="include formulas already checked")
    s.set_defaults(fn=cmd_export_tex)
    s = sub.add_parser("import-tex-check", help="store KaTeX check results and flag affected questions")
    s.add_argument("formulas")
    s.add_argument("results")
    s.add_argument("--renderer", default="katex")
    s.set_defaults(fn=cmd_import_tex_check)
    a = p.parse_args(argv)
    logging.getLogger().setLevel(get_settings().log_level)
    a.fn(a)


if __name__ == "__main__":
    main()


def resume_orphaned_sync(db):
    """A run left 'running'/'interrupted' by a killed process (container restart, deploy) is resumed
    from its checkpoint. 'failed' runs are not retried automatically (they need a human)."""
    from sqlalchemy import select, text
    from .models import SyncRun
    from .sync.importer import LOCK_KEY, sync_hsa
    last = db.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
    if not last or last.status not in ("running", "interrupted"):
        return
    with db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT") as probe:  # as in sync_hsa
        if not probe.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}):
            return  # a live process is syncing
        probe.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    s = get_settings()
    try:
        os.nice(15)
    except OSError:
        pass
    print(f"resuming orphaned sync run {last.id} from {last.checkpoint}", flush=True)
    try:
        run = sync_hsa(db, s.upstream_root, s.media_root, triggered_by=last.triggered_by or "resume")
        print(json.dumps({"sync_run": run.id, "status": run.status}), flush=True)
    except RuntimeError as ex:
        db.rollback()
        print(f"resume postponed: {ex}")
