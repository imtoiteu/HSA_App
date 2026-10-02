"""Import an additional question bank from app-format JSONL (docs/QUESTION_IMPORT_FORMAT.md).

Same guarantees as the HSA sync: keyed by (bank, external_id), content-hash versioning, immutable
versions, no deletions. Images are referenced as {{image:relative/path.png}} relative to the file.
"""
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..content import answers as A
from ..content import render as R
from ..models import QuestionBank, Subject
from ..settings_store import get_setting
from .hsa_upstream import PRIORITY, BuiltQuestion, _has_warn
from .importer import _upsert_assets, apply_built
from .media import MediaStore, audit_image

VALID_TYPES = {"single_choice", "multiple_choice", "true_false", "true_false_statements", "numeric_response",
               "short_response", "error_identification", "constructed_response", "open_or_unknown"}


class ImportError_(Exception):
    pass


def build_record(rec: dict, base: Path, media: MediaStore) -> BuiltQuestion:
    for k in ("external_id", "type"):
        if not rec.get(k):
            raise ImportError_(f"missing field {k}")
    if rec["type"] not in VALID_TYPES:
        raise ImportError_(f"unknown type {rec['type']}")
    res = R.Resolver()
    stored = {}

    def image_path(rel):
        p = (base / rel).resolve()
        if base.resolve() not in p.parents or not p.exists():
            res.issue("missing_visual", f"image {rel} missing")
            return [{"t": "warn", "v": "thiếu hình"}], []
        sf = media.put(p)
        issues, info = audit_image(media.path_of(sf.sha256, sf.ext))
        if "low_resolution" in issues:
            res.issue("visual", f"low-resolution figure {rel}")
        stored[sf.sha256] = sf
        res.assets.add(sf.sha256)
        b = {"t": "img", "src": sf.rel_path}
        if sf.width:
            b["w"], b["h"] = sf.width, sf.height
        return [], [b]

    res.image_path = image_path
    tables = rec.get("tables") or []
    g = rec.get("group")
    group = None
    if g:
        group = {"key": g["key"], "header": R.blocks(R.neutralize_group_header(g.get("header_md")), res, tables),
                 "passage": R.blocks(g.get("passage_md"), res, tables)}
    stem = R.blocks(rec.get("stem_md"), res, tables)
    opts_md = [(o["label"], o.get("md") or "") for o in rec.get("options") or []]
    options = [{"label": l, "content": R.blocks(md, res, tables)} for l, md in opts_md]
    solution = R.blocks(rec["solution_md"], res, tables) if rec.get("solution_md") else None
    explanation = R.blocks(rec["explanation_md"], res, tables) if rec.get("explanation_md") else None
    raw = rec.get("answer")
    answer, problem = A.normalize_answer(rec["type"], raw, "IMPORTED" if raw else None, [l for l, _ in opts_md],
                                         dict(opts_md))
    inp = A.input_spec(rec["type"], opts_md, answer, rec.get("stem_md") or "")
    mode = A.scoring_mode(inp, answer, bool(solution or explanation))
    state = rec.get("status") or "NEEDS_REVIEW"
    if state not in PRIORITY:
        raise ImportError_(f"unknown status {state}")
    states = [state]
    if res.issues and state == "READY_TO_SERVE":
        states = ["NEEDS_VISUAL_REVIEW"]
    if raw is None and state == "READY_TO_SERVE" and mode == "none":
        states = ["NEEDS_ANSWER_LINKING"]
    reasons = [problem] if problem else []
    if inp["kind"] == "choice" and len(options) < 2:
        reasons.append("too_few_options")
    if _has_warn(stem) or _has_warn(options) or (group and _has_warn(group)):
        reasons.append("render_warning")
    content = {"type": rec["type"], "language": rec.get("language") or "vi", "input": inp, "group": group,
               "stem": stem, "options": options, "solution": solution, "explanation": explanation,
               "shuffle_safe": A.shuffle_safe(rec["type"], {l: R.plain_text(o["content"]) for (l, _), o in
                                                             zip(opts_md, options)}),
               "assets": sorted(res.assets)}
    return BuiltQuestion(
        external_id=str(rec["external_id"]), question_type=rec["type"], source_subject=rec.get("subject"),
        topic=rec.get("topic"), subtopic=rec.get("subtopic"), cognitive_level=rec.get("cognitive_level"),
        language=rec.get("language") or "vi", group_key=(g or {}).get("key"),
        exam_systems=rec.get("exam_systems") or [], has_image=bool(res.assets),
        has_formula="{{tex:" in json.dumps(rec, ensure_ascii=False), has_table=bool(tables),
        review_status=None, review_flags=[], answer_source_type="IMPORTED" if answer else None, content=content,
        answer=answer, states=states, state_source="import", state_notes=[d for _, d in res.issues][:20],
        scoring_mode=mode, app_reasons=sorted(set(reasons)), provenance=rec.get("source") or {}, media=stored)


def import_jsonl(db: Session, bank_code: str, path: str, media_root: Path, name: str | None = None) -> dict:
    path = Path(path)
    bank = db.scalar(select(QuestionBank).where(QuestionBank.code == bank_code))
    if bank is None:
        bank = QuestionBank(code=bank_code, name=name or bank_code, source_kind="jsonl_import")
        db.add(bank)
        db.flush()
    if bank.source_kind == "hsa_upstream":
        raise ImportError_("the HSA bank is synchronised from upstream, not imported")
    policy = get_setting(db, "serving_policy")
    subjects = {s.code for s in db.scalars(select(Subject))}
    alias = {s: s for s in subjects}
    alias[""] = "general"
    media = MediaStore(media_root)
    stats = {"seen": 0, "created": 0, "updated": 0, "versions_created": 0, "content_changed": 0, "errors": []}
    fp = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            if not line.strip():
                continue
            stats["seen"] += 1
            try:
                rec = json.loads(line)
                if rec.get("subject") and rec["subject"] not in subjects:
                    raise ImportError_(f"unknown subject {rec['subject']}")
                b = build_record(rec, path.parent, media)
                shash = hashlib.sha256(line.encode()).hexdigest()
                with db.begin_nested():
                    _upsert_assets(db, [b])
                    apply_built(db, bank.id, b, shash, alias, policy, None, f"jsonl:{fp}", stats)
            except (ImportError_, ValueError, KeyError) as ex:
                stats["errors"].append({"line": ln, "error": str(ex)})
    db.commit()
    return stats
