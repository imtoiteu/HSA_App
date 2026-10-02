"""Question-bank integration audit / reconciliation (read-only on both sides).

Compares the upstream canonical bank with what the app holds, keyed by the immutable cq_… id, and
checks every stored question for the data it needs (answer, assets, shared passage, subject, QA
state, formulas). `deep=True` also recomputes the upstream input hash of every question to detect
content that changed upstream since the last sync.

The report is a JSON-serialisable dict; `to_markdown` renders it for humans. The CLI stores the last
report in app_setting['question_bank_audit'] for the admin UI.
"""
import collections
import datetime as dt
import json
import sqlite3
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (Asset, FormulaCheck, Question, QuestionBank, QuestionVersion, SourceDocument, Subject,
                      SyncRun)
from ..settings_store import get_setting
from .subjects import collect_tex, resolve_subject

TRUSTED = {"SOURCE_PROVIDED_ANSWER", "SOURCE_PROVIDED_SOLUTION"}


def _jl_count(p: Path) -> int:
    return sum(1 for l in open(p, encoding="utf-8") if l.strip()) if p.exists() else 0


def upstream_profile(root: Path) -> dict:
    root = Path(root)
    con = sqlite3.connect(f"file:{root / 'question-bank/sqlite/hsa_question_bank.sqlite'}?mode=ro", uri=True)
    q = lambda sql: con.execute(sql).fetchall()  # noqa: E731
    total = q("SELECT count(*) FROM canonical_question")[0][0]
    prof = {
        "canonical_questions": total,
        "question_types": dict(q("SELECT question_type, count(*) FROM canonical_question GROUP BY 1 ORDER BY 2 DESC")),
        "original_subjects": {str(k): v for k, v in q(
            "SELECT subject, count(*) FROM canonical_question GROUP BY 1 ORDER BY 2 DESC")},
        "answer_source": dict(q("SELECT answer_source_type, count(*) FROM canonical_question GROUP BY 1")),
        "with_trusted_answer": q("SELECT count(*) FROM canonical_question WHERE answer_source_type IN "
                                 "('SOURCE_PROVIDED_ANSWER','SOURCE_PROVIDED_SOLUTION')")[0][0],
        "with_solution": q("SELECT count(*) FROM canonical_question WHERE coalesce(solution_md,'')<>'' "
                           "OR coalesce(explanation_md,'')<>''")[0][0],
        "in_groups": q("SELECT count(*) FROM canonical_question WHERE group_id IS NOT NULL")[0][0],
        "distinct_groups_used": q("SELECT count(DISTINCT group_id) FROM canonical_question WHERE group_id IS NOT NULL")[0][0],
        "groups_total": q("SELECT count(*) FROM question_group")[0][0],
        "with_formula": q("SELECT count(*) FROM canonical_question WHERE has_formula=1")[0][0],
        "formulas_total": q("SELECT count(*) FROM formula")[0][0],
        "with_image": q("SELECT count(*) FROM canonical_question WHERE has_image=1")[0][0],
        "assets_total": q("SELECT count(*) FROM asset")[0][0],
        "with_table": q("SELECT count(*) FROM canonical_question WHERE has_table=1")[0][0],
        "review_status": dict(q("SELECT review_status, count(*) FROM canonical_question GROUP BY 1")),
    }
    con.close()
    # editorial states (bulk-build manifests)
    states, n_man = collections.Counter(), 0
    for p in sorted((root / "rendered/docx/_manifest").glob("*_Vol*.json")):
        for r in json.load(open(p, encoding="utf-8")).get("rows", []):
            if r.get("states"):
                states[r["states"][0]] += 1
                n_man += 1
    prof["editorial_primary_state"] = dict(states.most_common())
    prof["editorial_states_available"] = n_man
    # second-pass subject inference
    inf = collections.Counter()
    conf = collections.Counter()
    n_inf = 0
    p = root / "editorial/subject_inference.jsonl"
    if p.exists():
        for line in open(p, encoding="utf-8"):
            if not line.strip():
                continue
            o = json.loads(line)
            n_inf += 1
            if o.get("inferred_subject"):
                inf[o["inferred_subject"]] += 1
                conf[o.get("confidence")] += 1
    prof["subject_inference"] = {"candidates": n_inf, "inferred": sum(inf.values()), "by_subject": dict(inf.most_common()),
                                 "by_confidence": dict(conf)}
    ed = root / "editorial"
    prof["editorial_overlays"] = {n: _jl_count(ed / f"{n}.jsonl") for n in
                                  ("question_overrides", "formula_overrides", "figure_overrides", "asset_replacements")}
    return prof


def _answer_compatible(answer: dict | None, content: dict) -> str | None:
    """None if the stored answer can be scored against this question's input; otherwise a reason."""
    if not answer:
        return "no_answer"
    inp = content.get("input") or {}
    k = answer.get("kind")
    if k == "choice":
        keys = {o["label"] for o in content.get("options") or []}
        if inp.get("kind") != "choice":
            return "choice_answer_without_choice_input"
        if not answer.get("labels") or not set(answer["labels"]) <= keys:
            return "labels_not_in_options"
        if not inp.get("multi") and len(answer["labels"]) > 1:
            return "multiple_labels_single_choice"
        return None
    if k == "numeric":
        v = answer.get("value")
        return None if isinstance(v, (int, float)) and v == v and inp.get("kind") in ("numeric", "text") else \
            "numeric_answer_invalid"
    if k == "tf_sequence":
        return None if inp.get("kind") == "tf_sequence" and inp.get("n") == len(answer.get("values") or []) else \
            "tf_sequence_length_mismatch"
    if k == "boolean":
        return None if isinstance(answer.get("value"), bool) else "boolean_invalid"
    if k == "text":
        return None if (answer.get("text") or "").strip() else "text_empty"
    return "unknown_answer_kind"


def _images(nodes, out: list):
    if isinstance(nodes, list):
        for n in nodes:
            _images(n, out)
    elif isinstance(nodes, dict):
        if nodes.get("t") == "img" and nodes.get("src"):
            out.append(nodes["src"])
        for k in ("c", "rows", "header", "passage", "content", "stem", "options", "solution", "explanation", "group"):
            if k in nodes:
                _images(nodes[k], out)


def _has_warn(nodes) -> bool:
    if isinstance(nodes, list):
        return any(_has_warn(n) for n in nodes)
    if isinstance(nodes, dict):
        if nodes.get("t") == "warn":
            return True
        return any(_has_warn(nodes[k]) for k in ("c", "rows", "header", "passage", "content") if k in nodes)
    return False


def app_profile(db: Session, bank: QuestionBank, media_root: Path, upstream_ids: set | None,
                upstream_answers: dict | None, upstream_solutions: set | None) -> dict:
    media_root = Path(media_root)
    policy = get_setting(db, "serving_policy")
    rows = db.execute(select(Question.external_id, func.count()).where(Question.bank_id == bank.id)
                      .group_by(Question.external_id).having(func.count() > 1)).all()
    ids = set(db.scalars(select(Question.external_id).where(Question.bank_id == bank.id)))
    prof: dict = {"imported_unique": len(ids), "duplicate_ids": [r[0] for r in rows][:50],
                  "duplicate_count": len(rows)}
    if upstream_ids is not None:
        prof["missing_from_app"] = sorted(upstream_ids - ids)[:100]
        prof["missing_from_app_count"] = len(upstream_ids - ids)
        extra = ids - upstream_ids
        prof["extra_in_app_count"] = len(extra)
        prof["extra_in_app"] = sorted(extra)[:100]
        prof["extra_not_flagged_removed"] = db.scalar(select(func.count()).where(
            Question.bank_id == bank.id, Question.external_id.in_(extra), Question.removed_upstream.is_(False))) \
            if extra else 0
    prof["removed_upstream_flagged"] = db.scalar(select(func.count()).where(
        Question.bank_id == bank.id, Question.removed_upstream.is_(True)))

    known_assets = set(db.scalars(select(Asset.sha256)))
    failing = set(db.scalars(select(FormulaCheck.tex_sha).where(FormulaCheck.ok.is_(False))))
    checked = set(db.scalars(select(FormulaCheck.tex_sha)))
    import hashlib
    c = collections.Counter()
    by_state, by_reason, by_subject, by_source = (collections.Counter() for _ in range(4))
    by_classification, by_review = collections.Counter(), collections.Counter()
    served_by_subject, served_types, excluded_primary = (collections.Counter() for _ in range(3))
    answer_problems = collections.Counter()
    samples = collections.defaultdict(list)
    tex_all, tex_served, tex_served_visible = set(), set(), set()
    img_missing_q = img_missing_served = 0
    original_subject_counts = collections.Counter()
    alias = {}
    from ..models import SubjectAlias
    for a in db.scalars(select(SubjectAlias).where(SubjectAlias.bank_id == bank.id)):
        alias[a.source_value] = a.subject_code
    stmt = (select(Question, QuestionVersion.content, QuestionVersion.answer)
            .outerjoin(QuestionVersion, QuestionVersion.id == Question.current_version_id)
            .where(Question.bank_id == bank.id).execution_options(yield_per=1000))
    for q, content, answer in db.execute(stmt):
        c["questions"] += 1
        by_state[q.editorial_state or "(none)"] += 1
        by_subject[q.subject_code or "(none)"] += 1
        by_source[q.subject_source] += 1
        by_classification[q.classification_source or "(none)"] += 1
        by_review[q.classification_review or "(none)"] += 1
        orig, _ = resolve_subject(q.source_subject, None, None, None, alias, dict(policy, subject_inference_confidence=[]))
        original_subject_counts[orig or "(none)"] += 1
        if q.subject_code is None:
            c["missing_subject"] += 1
        if not q.editorial_state:
            c["missing_qa_state"] += 1
        if content is None:
            c["missing_current_version"] += 1
            continue
        served = q.is_served
        if served:
            c["served"] += 1
            served_by_subject[q.subject_code or "(none)"] += 1
            served_types[q.question_type] += 1
            c[f"served_{q.scoring_mode}"] += 1
        else:
            excluded_primary[q.editorial_state if q.editorial_state != "READY_TO_SERVE" else
                             ("app_check:" + (q.policy_reasons[0] if q.policy_reasons else "admin_disabled"))] += 1
        for r in q.policy_reasons:
            by_reason[r] += 1
        if q.policy_eligible:
            c["policy_eligible"] += 1
        # answers
        if upstream_answers is not None and upstream_answers.get(q.external_id) in TRUSTED and not answer:
            c["trusted_upstream_answer_not_usable"] += 1
            if len(samples["trusted_answer_not_usable"]) < 20:
                samples["trusted_answer_not_usable"].append(q.external_id)
        if served and q.scoring_mode == "auto":
            prob = _answer_compatible(answer, content)
            if prob:
                answer_problems[prob] += 1
                if len(samples["served_auto_answer_problem"]) < 20:
                    samples["served_auto_answer_problem"].append(f"{q.external_id}:{prob}")
        if answer:
            c["with_answer"] += 1
        # solutions
        if content.get("solution") or content.get("explanation"):
            c["with_solution"] += 1
        elif upstream_solutions is not None and q.external_id in upstream_solutions:
            c["upstream_solution_dropped"] += 1  # e.g. a solution that only repeated the stem
        # groups
        if q.group_key:
            c["in_group"] += 1
            g = content.get("group")
            if not g:
                c["group_snapshot_missing"] += 1
            elif not g.get("passage") and "group_context_missing" not in q.content_flags:
                c["group_passage_empty_unflagged"] += 1
        # assets
        imgs: list = []
        _images(content, imgs)
        missing = [s for s in imgs if s.split("/")[-1].split(".")[0] not in known_assets or not (media_root / s).exists()]
        if imgs:
            c["with_images"] += 1
        if missing:
            img_missing_q += 1
            if served:
                img_missing_served += 1
            if len(samples["missing_asset"]) < 20:
                samples["missing_asset"].append(f"{q.external_id}:{missing[0]}")
        if _has_warn([content.get("stem"), content.get("options"), content.get("group")]):
            c["render_placeholders_student_visible"] += 1
            if served:
                c["render_placeholders_served"] += 1
        # tables
        if '"t": "table"' in json.dumps(content.get("stem")) or any(
                '"t": "table"' in json.dumps(o.get("content")) for o in content.get("options") or []):
            c["with_table"] += 1
        # formulas
        tex = collect_tex(content)
        if tex:
            c["with_formula"] += 1
            tex_all |= tex
            if served:
                tex_served |= tex
                tex_served_visible |= collect_tex({k: content.get(k) for k in ("stem", "options", "group")})
    sha = lambda t: hashlib.sha256(t.encode("utf-8")).hexdigest()  # noqa: E731
    prof.update({
        "counts": dict(c), "editorial_state": dict(by_state.most_common()),
        "policy_reasons": dict(by_reason.most_common()), "excluded_by_primary_cause": dict(excluded_primary.most_common()),
        "effective_subject": dict(by_subject.most_common()), "subject_source": dict(by_source),
        "upstream_classification_source": dict(by_classification.most_common()),
        "upstream_classification_review": dict(by_review),
        "subject_without_inference": dict(original_subject_counts.most_common()),
        "served_by_subject": dict(served_by_subject.most_common()), "served_types": dict(served_types.most_common()),
        "served_auto_answer_problems": dict(answer_problems), "questions_missing_assets": img_missing_q,
        "served_missing_assets": img_missing_served, "samples": dict(samples),
        "formulas": {"distinct_tex": len(tex_all), "distinct_tex_served": len(tex_served),
                     "checked_by_renderer": len({sha(t) for t in tex_all} & checked),
                     "failing_all": len({sha(t) for t in tex_all} & failing),
                     "failing_served": len({sha(t) for t in tex_served} & failing),
                     "failing_served_student_visible": len({sha(t) for t in tex_served_visible} & failing)},
        "deferred_source_documents": db.scalar(select(func.count()).where(SourceDocument.bank_id == bank.id)),
        "bank": {"code": bank.code, "version": bank.version, "last_synced_at": bank.last_synced_at.isoformat()
                 if bank.last_synced_at else None, "active": bank.is_active},
    })
    last = db.scalar(select(SyncRun).where(SyncRun.bank_id == bank.id).order_by(SyncRun.id.desc()).limit(1))
    prof["last_sync"] = {"id": last.id, "status": last.status, "fingerprint": last.source_fingerprint,
                         "finished_at": last.finished_at.isoformat() if last and last.finished_at else None} if last else None
    return prof


def content_drift(db: Session, bank: QuestionBank, root: Path, media_root: Path) -> dict:
    """Recompute the raw-input hash of every upstream question and compare with the stored one."""
    from .hsa_upstream import UpstreamSource
    from .importer import source_hash
    from .media import MediaStore
    src = UpstreamSource(Path(root), MediaStore(Path(media_root)))
    stored = dict(db.execute(select(Question.external_id, Question.source_hash).where(Question.bank_id == bank.id)).all())
    db.rollback()  # end the read transaction: hashing upstream takes minutes (idle-in-transaction timeout)
    changed = []
    n = 0
    for q in src.iter_questions():
        n += 1
        if stored.get(q["canonical_question_id"]) != source_hash(src, q):
            changed.append(q["canonical_question_id"])
    return {"compared": n, "changed_since_sync": len(changed), "changed_sample": changed[:50],
            "upstream_fingerprint": src.fingerprint()}


def run_audit(db: Session, upstream_root: Path, media_root: Path, bank_code: str = "hsa", deep: bool = False) -> dict:
    bank = db.scalar(select(QuestionBank).where(QuestionBank.code == bank_code))
    if bank is None:
        raise ValueError(f"unknown bank {bank_code}")
    report = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "bank": bank_code}
    db.rollback()  # the upstream phase reads files only; do not hold a transaction open meanwhile
    up_ids = up_answers = up_solutions = None
    root = Path(upstream_root)
    if bank.source_kind == "hsa_upstream" and (root / "question-bank/sqlite/hsa_question_bank.sqlite").exists():
        report["upstream"] = upstream_profile(root)
        con = sqlite3.connect(f"file:{root / 'question-bank/sqlite/hsa_question_bank.sqlite'}?mode=ro", uri=True)
        up_answers = dict(con.execute("SELECT canonical_question_id, answer_source_type FROM canonical_question"))
        up_ids = set(up_answers)
        up_solutions = {r[0] for r in con.execute(
            "SELECT canonical_question_id FROM canonical_question WHERE coalesce(solution_md,'')<>'' "
            "OR coalesce(explanation_md,'')<>''")}
        con.close()
    report["app"] = app_profile(db, bank, media_root, up_ids, up_answers, up_solutions)
    db.rollback()
    if deep and up_ids is not None:
        report["drift"] = content_drift(db, bank, root, media_root)
    db.rollback()
    report["reconciliation"] = reconciliation(report)
    return report


def reconciliation(r: dict) -> dict:
    up, app = r.get("upstream") or {}, r["app"]
    c = app["counts"]
    unsupported = {t: n for t, n in (up.get("question_types") or {}).items() if t == "open_or_unknown"}
    return {
        "UPSTREAM CANONICAL total": up.get("canonical_questions"),
        "APP IMPORTED UNIQUE total": app["imported_unique"],
        "MISSING FROM APP": app.get("missing_from_app_count"),
        "EXTRA/STALE IN APP": app.get("extra_in_app_count"),
        "DUPLICATES": app["duplicate_count"],
        "CHANGED UPSTREAM SINCE SYNC": (r.get("drift") or {}).get("changed_since_sync"),
        "READY_TO_SERVE UPSTREAM": (up.get("editorial_primary_state") or {}).get("READY_TO_SERVE"),
        "READY_TO_SERVE IN APP": app["editorial_state"].get("READY_TO_SERVE"),
        "ELIGIBLE FOR STUDENT SERVING IN APP": c.get("served", 0),
        "  of which auto-scored": c.get("served_auto", 0),
        "  of which practice self-check": c.get("served_self_check", 0),
        "EXCLUDED BY QA (editorial state)": sum(v for k, v in app["excluded_by_primary_cause"].items()
                                               if not k.startswith("app_check:")),
        "EXCLUDED BY APP CONTENT CHECKS (READY upstream)": sum(v for k, v in app["excluded_by_primary_cause"].items()
                                                              if k.startswith("app_check:")),
        "UNSUPPORTED QUESTION TYPES (imported, not served)": unsupported,
        "QUESTIONS MISSING REQUIRED ASSETS (all / served)": f"{app['questions_missing_assets']} / {app['served_missing_assets']}",
        "QUESTIONS MISSING RESOLVABLE ANSWERS (served auto-scored)": sum(app["served_auto_answer_problems"].values()),
        "QUESTIONS WITHOUT ANY ANSWER (imported)": c.get("questions", 0) - c.get("with_answer", 0),
        "SHARED GROUPS: questions in groups / snapshots missing": f"{c.get('in_group', 0)} / {c.get('group_snapshot_missing', 0)}",
        "FORMULAS failing web renderer (all / served / served student-visible)":
            f"{app['formulas']['failing_all']} / {app['formulas']['failing_served']} / "
            f"{app['formulas'].get('failing_served_student_visible', '?')}",
        "RENDER PLACEHOLDERS in stem/options/passage (all / served)":
            f"{c.get('render_placeholders_student_visible', 0)} / {c.get('render_placeholders_served', 0)}",
        "DEFERRED SOURCE DOCUMENTS (scanned PDFs, NEEDS_MATH_AWARE_OCR)": app["deferred_source_documents"],
    }


def to_markdown(r: dict) -> str:
    lines = [f"# Question-bank audit — {r['bank']} — {r['generated_at']}", "", "## Reconciliation", ""]
    for k, v in r["reconciliation"].items():
        lines.append(f"- **{k}**: {v}")
    if r.get("upstream"):
        lines += ["", "## Upstream (read-only)", "", "```json", json.dumps(r["upstream"], ensure_ascii=False, indent=1), "```"]
    app = {k: v for k, v in r["app"].items() if k not in ("missing_from_app", "extra_in_app")}
    lines += ["", "## App", "", "```json", json.dumps(app, ensure_ascii=False, indent=1), "```"]
    if r.get("drift"):
        lines += ["", "## Content drift", "", "```json", json.dumps(r["drift"], ensure_ascii=False, indent=1), "```"]
    return "\n".join(lines) + "\n"
