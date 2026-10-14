"""Remediation batch: rebuild the flattened formulas of questions from the text layer of Word/MathType PDFs.

Same contract as the LaTeX batch (`formula_batch`), with the Word engine (`formula_word`):

1. the rebuild: every glyph of the question's region used, every stroke explained (fraction bar, radical,
   slash, underline, page furniture), no glyph without a known meaning (Symbol / MT Extra tables);
2. the page image: no ink outside the text layer's glyphs and the explained strokes, and — on the page rendered
   *without* its text — no drawing or picture ink that is not one of the explained strokes (figures, tables,
   accents or equations drawn as images are refused);
3. identity: the rebuilt stem/options use the words of the stored (flattened) version, and the letters and
   digits of the stored version all reappear in the rebuild;
4. KaTeX renders every formula;
5. the answer: the same rules as the other batches; a key from an answer table alone also needs the worked
   solution's "Chọn X" printed after the question;
6. no shared passage (a group's passage is not rebuilt here) and no figure/table in the stored version.

A verified rebuild is saved as a review draft and, when the answer and subject checks pass, approved. Anything
uncertain changes nothing and the reason is recorded.
"""
from __future__ import annotations

import collections
import json
import re
import unicodedata
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session, lazyload

from ..models import FormulaCheck, Question, ReviewEvent, SourceCache
from . import content as C
from . import formula_word as W
from . import service as S
from .formula_batch import CANDIDATES, COLLECTION_SUBJECT, is_pdftex, tex_sha
from .remediate import (METHOD, Verdict, actor, check_answer, check_subject, nfc_content, plain_text,
                        solution_contradicts, subject_scores)
from .sources import default_store

BATCH = "pdf_formula_word"
ENGINE = "formula_word/1"


def candidates(db: Session, limit: int | None, documents: list[str] | None = None, retry: bool = False,
               questions: list[int] | None = None) -> list[int]:
    sql = f"SELECT q.id FROM question q JOIN question_review r ON r.question_id = q.id WHERE {CANDIDATES}"
    if questions:
        sql += f" AND q.id IN ({','.join(str(int(x)) for x in questions)})"
    if not retry:
        sql += (f" AND NOT EXISTS (SELECT 1 FROM review_event e WHERE e.question_id = q.id"
                f" AND e.action IN ('remediation_skip', 'edit') AND e.note LIKE '[{BATCH}]%')")
    if documents:
        sql += " AND q.provenance->>'document_id' = ANY(:docs)"
    sql += " ORDER BY q.provenance->>'document_id', (q.provenance->>'source_question_number')::int, q.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(db.scalars(text(sql), {"docs": documents} if documents else {}))


def _layout_jobs(jobs: list[tuple], store, log=print) -> dict:
    """jobs: [(key, sha, page, number, doc)] → {key: rebuild result}."""
    out: dict = {}
    by_sha = collections.defaultdict(list)
    for job in jobs:
        by_sha[job[1]].append(job)
    for sha, items in by_sha.items():
        pdf = store.pdf_path(sha)
        if not pdf.exists():
            for key, *_ in items:
                out[key] = {"content": None, "reason": "source PDF missing from the cache", "transient": True}
            continue
        try:
            latex = is_pdftex(pdf)
        except Exception:  # noqa: BLE001
            latex = False
        if latex:
            for key, *_ in items:
                out[key] = {"content": None, "reason": "LaTeX-typeset PDF (rebuilt by the LaTeX engine)"}
            continue
        items.sort(key=lambda j: j[2])
        for key, _, page, number, doc in items:
            try:   # pages are read per question: the furniture and band logic look at the page and its neighbours
                pages = W.read_pages(pdf, {page, page + 1, page + 2})
                res = W.layout_question(pages, page, number)
                if res.content is not None:
                    why = W.ink_check(pdf, res, pages) or W.drawing_check(pdf, res, pages)
                    if why:
                        res.content, res.reason = None, why
            except W.Unresolved as ex:
                res = W.Result(reason=str(ex))
            except Exception as ex:  # noqa: BLE001  a damaged/unsupported PDF: nothing is changed
                res = W.Result(reason=f"PDF could not be read: {ex.__class__.__name__}")
            out[key] = {"content": res.content, "reason": res.reason, "answer_said": res.answer_in_solution,
                        "ref": {"document_id": doc, "page": page, "pages": res.pages, "blob_sha256": sha,
                                "question_number": number, "method": "pdf_layout", "engine": ENGINE,
                                "batch": BATCH}}
        log(f"  {sha[:10]}: {len(items)} rebuilt/checked")
    return out


def rebuild(db: Session, qids: list[int], store, log=print) -> dict[int, dict]:
    out: dict[int, dict] = {}
    jobs = []
    for qid in qids:
        q = db.get(Question, qid)
        prov = q.provenance or {}
        doc = prov.get("document_id")
        sc = db.get(SourceCache, doc) if doc else None
        if not sc or sc.status != "ready" or not sc.blob_sha256:
            out[qid] = {"content": None, "reason": f"source not available ({sc.status if sc else 'none'})",
                        "transient": True}
            continue
        occ = next((o for o in store.up.occurrences(q.external_id)
                    if o["occurrence_id"] == prov.get("representative_occurrence_id")), None)
        if not occ or not occ["pages"] or occ["document_id"] != doc or not occ["source_question_number"]:
            out[qid] = {"content": None, "reason": "source page of the question unknown"}
            continue
        jobs.append((qid, sc.blob_sha256, occ["pages"][0], occ["source_question_number"], doc))
    db.rollback()  # no transaction stays open while PDFs are parsed
    out.update(_layout_jobs(jobs, store, log))
    return out


def _chars(content: dict, parts=("stem", "options")) -> collections.Counter:
    """Letters and digits of a content (formulas by their visible characters) as a multiset."""
    out = []

    def walk(blocks):
        for b in blocks or []:
            for n in b.get("c") or []:
                if n.get("t") == "s":
                    out.append(n.get("v", ""))
                elif n.get("t") == "m":
                    t = re.sub(r"\\(?:text|mathrm|operatorname)\{([^{}]*)\}", r" \1 ", n.get("tex", ""))
                    # function names print their letters (\log → log); other commands are symbols
                    t = re.sub(r"\\(log|ln|lg|sin|cos|tan|cot|lim|max|min|exp|sup|inf|det|arg|gcd|deg|sinh|"
                               r"cosh|tanh|arcsin|arccos|arctan)(?![A-Za-z])", r" \1 ", t)
                    out.append(re.sub(r"\\[A-Za-z]+", " ", t))
    for k in parts:
        if k == "options":
            for o in content.get("options") or []:
                walk(o.get("content"))
        else:
            walk(content.get(k))
    s = unicodedata.normalize("NFC", "".join(out)).lower()
    return collections.Counter(c for c in s if c.isalnum())


def char_cover(stored: dict, rebuilt: dict) -> float:
    """Share of the stored (flattened) stem/options letters and digits that the rebuild also has. The flattening
    drops symbols but never invents letters: a rebuild of the right region covers nearly all of them."""
    a, b = _chars(stored), _chars(rebuilt)
    n = sum(a.values())
    if not n:
        return 1.0
    return sum(min(v, b.get(k, 0)) for k, v in a.items()) / n


def run(db: Session, limit: int | None = None, apply: bool = False, media_root=None, export_tex: str | None = None,
        documents: list[str] | None = None, tex_results: list[str] | None = None, retry: bool = False,
        questions: list[int] | None = None, log=print) -> dict:
    store = default_store()
    who = actor(db)
    stats = {"examined": 0, "rebuilt": 0, "drafts": 0, "promoted": 0, "unresolved": 0, "formula_fixed": 0,
             "answer_fixed": 0, "subject_fixed": 0, "source_conflicts": 0, "tex_unchecked": 0, "reasons": {}}
    qids = candidates(db, limit, documents, retry, questions)
    built = rebuild(db, qids, store, log=log)
    checked = {r.tex_sha: r.ok for r in db.execute(select(FormulaCheck.tex_sha, FormulaCheck.ok))}
    for path in tex_results or []:  # output of frontend/scripts/katex-check.mjs (same KaTeX as the web app)
        for row in json.loads(Path(path).read_text(encoding="utf-8")):
            checked[row["sha"]] = bool(row["ok"])
    unchecked: dict[str, str] = {}
    for qid in qids:
        stats["examined"] += 1
        b = built[qid]
        q = db.scalar(select(Question).options(lazyload(Question.current_version)).where(Question.id == qid))
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)

        def unresolved(reason, conflict=False, record=True):
            stats["unresolved"] += 1
            key = re.sub(r"\d+", "N", reason.split(" (")[0].split(":")[0])[:60]
            stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
            stats["source_conflicts"] += bool(conflict)
            note = f"[{BATCH}] {reason}"
            last = db.scalar(select(ReviewEvent.note).where(ReviewEvent.question_id == q.id,
                                                            ReviewEvent.action == "remediation_skip")
                             .order_by(ReviewEvent.id.desc()).limit(1))
            if apply and record and last != note:
                db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id,
                                   action="remediation_skip", changes=[], note=note))
                db.commit()
            log(f"  SKIP {q.external_id}: {reason}")

        if b["content"] is None:
            unresolved(b["reason"] or "not rebuilt", record=not b.get("transient"))
            continue
        stats["rebuilt"] += 1
        base, _ = nfc_content(v.content)
        if base.get("group"):
            unresolved("question of a shared passage (the passage is not rebuilt by this batch)")
            continue
        if C.images(v.content) or C.has_table(v.content):
            unresolved("the stored version carries a figure/table")
            continue
        rebuilt = b["content"]
        same = W.same_question(base, rebuilt)
        if same < 0.95:
            unresolved(f"rebuilt text does not match the stored question ({same:.0%} of its words)", conflict=True)
            continue
        cover = char_cover(base, rebuilt)
        if cover < 0.97:
            unresolved(f"rebuilt question lacks characters of the stored one ({cover:.0%} of its letters/digits)",
                       conflict=True)
            continue
        if [o["label"] for o in rebuilt["options"]] != [o["label"] for o in base.get("options") or []] and \
                len(base.get("options") or []) == 4:
            unresolved("option labels differ from the stored question", conflict=True)
            continue
        said = b.get("answer_said")
        texs = W.all_tex(rebuilt)
        missing = [t for t in texs if tex_sha(t) not in checked]
        if missing:
            for t in missing:
                unchecked[tex_sha(t)] = t
            stats["tex_unchecked"] += 1
            unresolved("formulas not yet checked with KaTeX", record=False)
            continue
        bad = [t for t in texs if not checked[tex_sha(t)]]
        if bad:
            unresolved(f"KaTeX cannot render a rebuilt formula: {bad[0][:60]}")
            continue
        key = (v.answer or {}).get("labels") if (v.answer or {}).get("kind") == "choice" else None
        if said and key and [said] != key:
            unresolved(f"the source solution says {said} but the key is {key}", conflict=True)
            continue
        if not apply:
            stats["drafts"] += 1
            log(f"  OK   {q.external_id} p.{b['ref']['page']} câu {b['ref']['question_number']}: {len(texs)} formulas"
                + (f", solution says {said}" if said else ""))
            continue
        ref = b["ref"]
        edit = {"stem": rebuilt["stem"], "options": rebuilt["options"], "solution": rebuilt.get("solution")}
        note = (f"[{BATCH}] dựng lại công thức từ bố cục trang PDF gốc (Word/MathType: font Symbol/MT Extra, vị trí "
                f"ký tự, gạch phân số, dấu căn; trang {', '.join(map(str, ref['pages']))}, câu "
                f"{ref['question_number']}): {len(texs)} công thức, mọi ký tự trong vùng câu hỏi được dùng, ảnh trang "
                "(cả khi bỏ chữ) không còn nét nào chưa giải thích, KaTeX hiển thị không lỗi")
        try:
            S.save_edit(db, q, who, edit, note=note, source_ref=ref, media_root=media_root,
                        tex_checks={t: {"ok": True} for t in texs})
        except S.ReviewError as ex:
            db.rollback()
            unresolved(f"draft refused: {ex}")
            continue
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        before = S.dim_view(q, r, v)["status"]
        stats["formula_fixed"] += 1
        if before.get("formula") not in ("ok", "na"):
            S.confirm(db, q, who, "formula", "ok" if texs else "na",
                      f"[{BATCH}] {len(texs)} công thức dựng lại từ bố cục PDF (Word/MathType), đối chiếu ảnh trang "
                      f"(có chữ và không chữ), KaTeX OK", media_root=media_root, method="pdf_layout")
        if before.get("content") != "ok":
            S.confirm(db, q, who, "content", "ok",
                      f"[{BATCH}] đề và phương án dựng lại từ trang {ref['page']} của tài liệu gốc; khớp từ ngữ bản cũ "
                      f"({same:.0%}), ký tự bản cũ có đủ ({cover:.0%})", media_root=media_root, method="pdf_layout")
        if before.get("source") != "ok":
            S.confirm(db, q, who, "source", "ok",
                      f"[{BATCH}] {ref['document_id']} trang {ref['page']}, sha256 {ref['blob_sha256'][:12]}",
                      media_root=media_root, method=METHOD)
        db.commit()
        stats["drafts"] += 1
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        dims = S.dim_view(q, r, v)["status"]
        av = Verdict(True, "đáp án đã được xác minh độc lập (claude_review)") if dims.get("answer") == "ok" else \
            check_answer(q, v.answer, v.content, store.up)
        if not av.ok and av.reason.startswith("single answer evidence") and said and key == [said]:
            av = Verdict(True, f"bảng đáp án gốc {key} + lời giải gốc “Chọn {said}” ngay sau câu hỏi "
                               f"({ref['document_id']} trang {ref['page']}, câu {ref['question_number']})")
        if not av.ok:
            unresolved(f"formula rebuilt (draft in Review Studio); answer: {av.reason}",
                       conflict="disagree" in av.reason or "says" in av.reason)
            continue
        if dims.get("answer") != "ok":
            contra = solution_contradicts(v.answer, v.content)
            if contra:
                unresolved(f"formula rebuilt (draft in Review Studio); {contra}", conflict=True)
                continue
        if dims.get("subject") != "ok" and (q.subject_source == "unclassified" or q.subject_code in (None, "general")):
            path = (q.provenance or {}).get("document_path") or ""
            coll = next((subj for rx, subj in COLLECTION_SUBJECT if rx.search(path)), None)
            if coll:
                txt = " ".join(plain_text(v.content.get(k) or []) for k in ("stem", "solution")) + " " + " ".join(
                    plain_text(o.get("content") or []) for o in v.content.get("options") or [])
                sc = subject_scores(txt)
                if sc.get(coll, 0) >= max(sc.values()) and (sc.get(coll, 0) >= 1 or texs):
                    why = (f"[{BATCH}] môn theo bộ tài liệu gốc chỉ gồm một môn: “{path.split(' ▸ ')[-1][:120]}”; "
                           f"nội dung không nghiêng về môn khác (từ khoá {coll}: {sc.get(coll, 0)})")
                    S.set_subject(db, q, who, coll, why, media_root=media_root)
                    S.confirm(db, q, who, "subject", "ok", why, media_root=media_root, method=METHOD)
                    stats["subject_fixed"] += 1
                    r = S.get_review(db, q)
                    v = S.working_version(db, q, r)
                    dims = S.dim_view(q, r, v)["status"]
        sj = check_subject(q, v.content) if dims.get("subject") != "ok" else Verdict(True, "môn đã được xác nhận")
        if not sj.ok:
            unresolved(f"formula rebuilt (draft in Review Studio); subject: {sj.reason}")
            continue
        if dims.get("answer") != "ok":
            S.confirm(db, q, who, "answer", "ok", f"[{BATCH}] {av.reason}"
                      + (f"; lời giải gốc: Chọn {said}" if said and "Chọn" not in av.reason else ""),
                      media_root=media_root, method=METHOD)
        if dims.get("subject") != "ok":
            S.confirm(db, q, who, "subject", "ok", f"[{BATCH}] {q.subject_code}: {sj.reason}", media_root=media_root,
                      method=METHOD)
        if dims.get("visual") not in ("ok", "na"):
            S.confirm(db, q, who, "visual", "na", f"[{BATCH}] không có hình (vùng câu hỏi trên trang gốc, kể cả khi "
                      "bỏ chữ, không có hình vẽ/ảnh)", media_root=media_root, method=METHOD)
        try:
            res = S.approve(db, q, who, f"[{BATCH}] tự động theo nguồn: công thức dựng lại từ PDF gốc (Word/MathType), "
                                        "đáp án khớp tài liệu gốc", media_root=media_root)
        except S.ReviewError as ex:
            db.rollback()
            unresolved(f"formula rebuilt (draft in Review Studio); approval refused: {ex}")
            continue
        db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id, action="remediation",
                           changes=[], note=f"[{BATCH}] promoted", source_ref=ref))
        db.commit()
        stats["promoted"] += res["gate"] == "ready"
        log(f"  READY {q.external_id} served={res['served']} reasons={res['reasons']}")
    if export_tex:
        Path(export_tex).write_text(json.dumps([{"sha": k, "tex": t} for k, t in unchecked.items()],
                                               ensure_ascii=False), encoding="utf-8")
        stats["tex_exported"] = len(unchecked)
    if apply:
        db.commit()
    return stats
