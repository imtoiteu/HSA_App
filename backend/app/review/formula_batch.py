"""Remediation batch: rebuild the flattened formulas of PDF text-layer questions from the source PDF's layout.

For every candidate (an upstream NEEDS_FORMULA_REVIEW question extracted from the text layer of a LaTeX-typeset
PDF) the question's region of its source page is rebuilt with `formula_pdf` and accepted only when every check
holds:

1. the rebuild itself: every glyph of the region used exactly once, every stroke explained (fraction bar,
   radical, overline), no figure/table/image, no glyph without a known meaning;
2. the page image: no ink in the region that the text layer does not explain;
3. identity: the rebuilt stem/options use the words of the stored (flattened) version of the same question;
4. KaTeX: every rebuilt formula renders with the web renderer (`formula_check` table, filled by
   `frontend/scripts/katex-check.mjs` + `hsa-app import-tex-check`; unchecked formulas are exported first);
5. the answer: the source key (same rules as the other batches) and, when the source has a worked solution,
   its "Chọn đáp án X" agrees.

A verified rebuild is saved as a review draft (the old version stays in the history). Only when the answer and
subject checks also pass is the question approved; otherwise the draft waits in Review Studio with the formula
dimension confirmed, and the reason is recorded. Anything uncertain changes nothing.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session, lazyload

from ..models import FormulaCheck, Question, ReviewEvent, SourceCache
from . import content as C
from . import formula_pdf as F
from . import service as S
from .remediate import (BASE, METHOD, Verdict, actor, check_answer, check_subject, nfc_content, plain_text,
                        solution_contradicts, subject_scores)
from .sources import default_store

BATCH = "pdf_formula_latex"
# folders of the bank's source collections that hold one subject only ("…/3. Toán Thường/…", the ĐGNL-HCM
# study folders: every classified question under them carries that subject, apart from a few upstream mislabels)
COLLECTION_SUBJECT = [(re.compile(r"(?:^|/)\d+\.\s*Toán\b"), "math"),
                      (re.compile(r"Tài liệu ôn tập/Toán \(ĐGNL\+THPT\)/"), "math"),
                      (re.compile(r"Tài liệu ôn tập/Lý \(ĐGNL\+THPT\)/"), "physics"),
                      (re.compile(r"Tài liệu ôn tập/Hóa/"), "chemistry")]


def collection_subject(path: str) -> str | None:
    path = unicodedata.normalize("NFC", path or "")
    return next((subj for rx, subj in COLLECTION_SUBJECT if rx.search(path)), None)
ENGINE = "formula_pdf/1"
# upstream flagged the question only because it comes from a PDF text layer (formulas flattened); the other
# flags listed here are consequences of the flattening that the rebuild resolves (options lost into the stem)
FLAGS_OK = ("pdf_text_layer_fidelity", "not_nfc_normalized", "option_label_normalized", "empty_option",
            "too_few_options", "answer_mapping_ambiguous", "solution_link_ordinal_mismatch")
CANDIDATES = BASE + f"""
      AND q.provenance->>'extraction_method' = 'pdf_text_layer'
      AND q.editorial_states && '{{NEEDS_FORMULA_REVIEW}}'
      AND q.editorial_states <@ '{{NEEDS_FORMULA_REVIEW,NEEDS_REVIEW}}'
      AND q.question_type = 'single_choice'
      AND q.review_flags <@ ARRAY{list(FLAGS_OK)}::varchar[]
"""


def tex_sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


# figure mode: questions whose text layer lost a figure (NEEDS_VISUAL_REVIEW) — the figure is cut from the page.
# A draft of this remediation whose figure has since been confirmed is "reviewed" (every dimension confirmed): it is
# rebuilt again (unchanged content keeps the confirmations), re-checked and promoted
CANDIDATES_FIG = BASE.replace("OR r.status = 'draft' AND", "OR r.status IN ('draft', 'reviewed') AND") + f"""
      AND q.provenance->>'extraction_method' = 'pdf_text_layer'
      AND q.editorial_states && '{{NEEDS_VISUAL_REVIEW}}'
      AND q.editorial_states <@ '{{NEEDS_FORMULA_REVIEW,NEEDS_VISUAL_REVIEW,NEEDS_REVIEW}}'
      AND q.question_type = 'single_choice'
      AND q.review_flags <@ ARRAY{list(FLAGS_OK)}::varchar[]
"""


def candidates(db: Session, limit: int | None, documents: list[str] | None = None, retry: bool = False,
               questions: list[int] | None = None, figures: bool = False) -> list[int]:
    sql = f"SELECT q.id FROM question q JOIN question_review r ON r.question_id = q.id WHERE " \
          f"{CANDIDATES_FIG if figures else CANDIDATES}"
    if questions:
        sql += f" AND q.id IN ({','.join(str(int(x)) for x in questions)})"
    if not retry:  # handled before (refused with a reason, or rebuilt into a draft): only --retry looks again
        sql += (f" AND NOT EXISTS (SELECT 1 FROM review_event e WHERE e.question_id = q.id"
                f" AND e.action IN ('remediation_skip', 'edit') AND e.note LIKE '[{BATCH}]%')")
    if documents:
        sql += " AND q.provenance->>'document_id' = ANY(:docs)"
    sql += " ORDER BY q.provenance->>'document_id', (q.provenance->>'source_question_number')::int, q.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(db.scalars(text(sql), {"docs": documents} if documents else {}))


def is_pdftex(pdf: Path) -> bool:
    """Only LaTeX-typeset PDFs (Computer Modern math fonts): the engine knows their glyphs and layout rules."""
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser
    with open(pdf, "rb") as f:
        info = PDFDocument(PDFParser(f)).info
    prod = b" ".join(v for i in info for k, v in i.items() if k in ("Producer", "Creator") and isinstance(v, bytes))
    return b"pdfTeX" in prod or b"LaTeX" in prod


def _reset(pages):
    for pg in pages.values():  # each rebuild starts from a clean page state
        for g in pg.glyphs:
            g.used = False
        for r_ in pg.rules:
            r_.used = False
        for s_ in pg.shapes:
            s_.used = False
        F.frame(pg)


def store_figures(db: Session, content: dict, media_root, ref: dict) -> int:
    """Store the cropped figure PNGs of a rebuild in the media store (asset rows) and point the nodes at them."""
    import io
    from PIL import Image
    from ..models import Asset
    from ..sync.media import MediaStore
    n = 0
    for part in ("stem", "solution"):
        for b in content.get(part) or []:
            if b.get("t") == "img" and "png" in b:
                data = b.pop("png")
                box = b.pop("box", None)
                im = Image.open(io.BytesIO(data))
                sf = MediaStore(media_root).put_bytes(data, "png")
                if db.get(Asset, sf.sha256) is None:
                    db.add(Asset(sha256=sf.sha256, ext=sf.ext, mime="image/png", bytes=sf.bytes, width=im.width,
                                 height=im.height, source_ref=f"{ref['document_id']}:p{box[0] if box else ref['page']}"[:80]))
                    db.flush()
                b.update({"src": f"{sf.sha256[:2]}/{sf.sha256}.{sf.ext}", "w": im.width, "h": im.height})
                n += 1
    return n


def crop_figure(pdf, page: int, box: list, dpi: int = 200) -> bytes:
    """PNG of a figure's box on the source page (a 2-point margin)."""
    import subprocess
    import tempfile
    s = dpi / 72.0
    x0, y0, x1, y1 = box
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile",
                        "-x", str(int((x0 - 2) * s)), "-y", str(int((y0 - 2) * s)), "-W", str(int((x1 - x0 + 4) * s)),
                        "-H", str(int((y1 - y0 + 4) * s)), str(pdf), f"{tmp}/f"], capture_output=True, timeout=120,
                       check=True)
        return Path(f"{tmp}/f.png").read_bytes()


def _attach_figures(pdf, content: dict) -> int:
    """Figure placeholders of a rebuild (layout_question figures=True) → image nodes carrying the cropped PNG
    (stored in the media store when the draft is saved)."""
    n = 0
    for part in ("stem", "solution"):
        for b in content.get(part) or []:
            if b.get("t") == "img" and "fig" in b:
                f = b.pop("fig")
                b["png"] = crop_figure(pdf, f["page"], f["box"])
                b["src"] = "__figure__"
                b["box"] = [f["page"]] + [round(v, 1) for v in f["box"]]
                n += 1
    return n


def _layout_jobs(jobs: list[tuple], store, log=print, stem_fallback: bool = False, figures: bool = False) -> dict:
    """jobs: [(key, sha, page, number, doc)] → {key: rebuild result}. Each PDF is parsed once per chunk."""
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
        if not latex:
            for key, *_ in items:
                out[key] = {"content": None, "reason": "source PDF is not LaTeX-typeset (engine not applicable)"}
            continue
        items.sort(key=lambda j: j[2])
        for k in range(0, len(items), 40):  # bounded memory: a few dozen pages at a time
            chunk = items[k:k + 40]
            want = {j[2] + d for j in chunk for d in (0, 1, 2)}
            try:
                pages = F.read_pages(pdf, want)
            except Exception as ex:  # noqa: BLE001  a damaged/unsupported PDF: nothing is changed
                for key, *_ in chunk:
                    out[key] = {"content": None, "reason": f"PDF could not be read: {ex.__class__.__name__}"}
                continue
            for key, _, page, number, doc in chunk:
                _reset(pages)
                res = F.layout_question(pages, page, number, figures=figures)
                if res.content is not None:
                    why = F.ink_check(pdf, res, pages)
                    if why:
                        res.content, res.reason = None, why
                if res.content is not None and res.figures:
                    _attach_figures(pdf, res.content)
                if res.content is None and stem_fallback:
                    # another edition: its stem/options and the solution's verdict are enough to confirm
                    _reset(pages)
                    res2 = F.layout_question(pages, page, number, stem_only=True, figures=figures)
                    if res2.content is not None and not F.ink_check(pdf, res2, pages):
                        res = res2
                out[key] = {"content": res.content, "reason": res.reason, "answer_said": res.answer_in_solution,
                            "figures": len(res.figures) if res.content is not None else 0,
                            "ref": {"document_id": doc, "page": page, "pages": res.pages, "blob_sha256": sha,
                                    "question_number": number, "method": "pdf_layout", "engine": ENGINE,
                                    "batch": BATCH}}
            log(f"  {sha[:10]}: {len(chunk)} rebuilt/checked")
    return out


def _same_rebuild(a: dict, b: dict) -> bool:
    """Two occurrences rebuilt to the same stem and options (formulas included) are the same question."""
    def norm(blocks):
        return json.dumps([[{k: (" ".join(v.split()) if isinstance(v, str) else v) for k, v in n.items()}
                            for n in bl.get("c") or []] for bl in blocks or []], ensure_ascii=False, sort_keys=True)
    return norm(a["stem"]) == norm(b["stem"]) and \
        [norm(o["content"]) for o in a["options"]] == [norm(o["content"]) for o in b["options"]]


def rebuild(db: Session, qids: list[int], store, log=print, figures: bool = False) -> dict[int, dict]:
    """Rebuild every question (no writes) from its representative occurrence; then rebuild its occurrences in
    the other editions of the book: one that rebuilds to the identical stem/options and carries a worked
    solution gives the solution and its "Chọn đáp án X" (an answer printed next to the question).
    Returns {qid: {"content"|None, "reason", "answer_said", "ref", "sibling"?}}."""
    out: dict[int, dict] = {}
    jobs, occs = [], {}
    for qid in qids:
        q = db.get(Question, qid)
        prov = q.provenance or {}
        doc = prov.get("document_id")
        sc = db.get(SourceCache, doc) if doc else None
        if not sc or sc.status != "ready" or not sc.blob_sha256:
            out[qid] = {"content": None, "reason": f"source not available ({sc.status if sc else 'none'})",
                        "transient": bool(sc and sc.status in ("queued", "preparing"))}
            continue
        occs[qid] = store.up.occurrences(q.external_id)
        occ = next((o for o in occs[qid] if o["occurrence_id"] == prov.get("representative_occurrence_id")), None)
        if not occ or not occ["pages"] or occ["document_id"] != doc or not occ["source_question_number"]:
            out[qid] = {"content": None, "reason": "source page of the question unknown"}
            continue
        jobs.append((qid, sc.blob_sha256, occ["pages"][0], occ["source_question_number"], doc))
    shas = {}
    for qid, o in occs.items():
        for x in o:
            if x["document_id"] not in shas:
                sc = db.get(SourceCache, x["document_id"])
                shas[x["document_id"]] = sc.blob_sha256 if sc and sc.status == "ready" and sc.blob_sha256 else None
    # editions that print a worked solution ending in "Chọn đáp án X" after (nearly) every question
    cand_docs = [d for d, sha in shas.items() if sha]
    solved = set(db.scalars(text(
        "SELECT q.provenance->>'document_id' FROM question q JOIN question_version v ON v.id = q.current_version_id "
        "WHERE q.provenance->>'document_id' = ANY(:d) GROUP BY 1 "
        "HAVING count(*) FILTER (WHERE v.content::text LIKE '%Chọn đáp án%') >= 0.5 * count(*)"),
        {"d": cand_docs})) if cand_docs else set()
    db.rollback()  # no transaction stays open while PDFs are parsed
    out.update(_layout_jobs(jobs, store, log, figures=figures))
    # second signals: the same question in other editions of the book (different page layout). An edition that
    # rebuilds to the identical stem and options confirms the formulas; one with a worked solution also gives the
    # solution and its "Chọn đáp án X"
    sib_jobs = []
    for qid, o in occs.items():
        b = out.get(qid)
        if not b or b["content"] is None:
            continue
        others = [x for x in o if shas.get(x["document_id"]) and x["document_id"] != b["ref"]["document_id"]
                  and x["pages"] and x["source_question_number"]]
        others.sort(key=lambda x: (x["document_id"] not in solved, x["document_id"]))
        seen_docs = set()
        for x in others:
            if x["document_id"] in seen_docs or len(seen_docs) >= 3:
                continue
            seen_docs.add(x["document_id"])
            sib_jobs.append(((qid, x["occurrence_id"]), shas[x["document_id"]], x["pages"][0],
                             x["source_question_number"], x["document_id"]))
    sibs = _layout_jobs(sib_jobs, store, log, stem_fallback=True, figures=figures) if sib_jobs else {}
    for (qid, occ_id), sb in sibs.items():
        b = out[qid]
        if sb["content"] is None or not _same_rebuild(b["content"], sb["content"]):
            continue
        b.setdefault("confirmed_by", []).append(sb["ref"])
        if not b.get("sibling") and not b.get("answer_said") and sb.get("answer_said"):
            b["sibling"] = {"answer_said": sb["answer_said"], "solution": sb["content"]["solution"],
                            "ref": sb["ref"], "occurrence_id": occ_id}
    return out


def run(db: Session, limit: int | None = None, apply: bool = False, media_root=None, export_tex: str | None = None,
        documents: list[str] | None = None, tex_results: list[str] | None = None, retry: bool = False,
        questions: list[int] | None = None, single_edition: bool = False, figures: bool = False,
        log=print) -> dict:
    """single_edition: a rebuild found in one edition only may also be promoted (same answer/subject gates). Measured
    2026-10-15 on 100 random one-edition drafts against the page images: 98 exact, 2 lost a raised script at the top
    edge of the question's region — the engine now refuses those (glyph in the region on none of its lines)."""
    store = default_store()
    who = actor(db)
    stats = {"examined": 0, "rebuilt": 0, "drafts": 0, "promoted": 0, "unresolved": 0, "formula_fixed": 0,
             "structural_fixed": 0, "answer_fixed": 0, "visual_fixed": 0, "subject_fixed": 0, "source_conflicts": 0,
             "tex_unchecked": 0, "reasons": {}}
    qids = candidates(db, limit, documents, retry, questions, figures)
    built = rebuild(db, qids, store, log=log, figures=figures)
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
            key = reason.split(" (")[0].split(":")[0][:60]
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
        rebuilt = b["content"]
        same = F.same_question(base, rebuilt)
        if same < 0.95:
            unresolved(f"rebuilt text does not match the stored question ({same:.0%} of its words)", conflict=True)
            continue
        if [o["label"] for o in rebuilt["options"]] != [o["label"] for o in base.get("options") or []] and \
                len(base.get("options") or []) == 4:
            unresolved("option labels differ from the stored question", conflict=True)
            continue
        sib = b.get("sibling")
        if sib and not rebuilt.get("solution"):
            rebuilt = dict(rebuilt, solution=sib["solution"])
        said, said_ref = (b.get("answer_said"), b["ref"]) if b.get("answer_said") else \
            ((sib["answer_said"], sib["ref"]) if sib else (None, None))
        texs = F.all_tex(rebuilt)
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
            unresolved(f"the source solution ({said_ref['document_id']} p.{said_ref['page']}) says {said} but the key "
                       f"is {key}", conflict=True)
            continue
        conf = b.get("confirmed_by") or []
        stats["confirmed"] = stats.get("confirmed", 0) + bool(conf)
        if not apply:
            stats["drafts"] += 1
            log(f"  OK   {q.external_id} p.{b['ref']['page']} câu {b['ref']['question_number']}: {len(texs)} formulas"
                + (f", confirmed by {len(conf)} other edition(s)" if conf else ", no second edition")
                + (f", solution says {said} ({said_ref['document_id']} p.{said_ref['page']})" if said else ""))
            continue
        ref = b["ref"]
        nfig = store_figures(db, rebuilt, media_root, ref) if b.get("figures") else 0
        edit = {"stem": rebuilt["stem"], "options": rebuilt["options"], "solution": rebuilt.get("solution")}
        note = (f"[{BATCH}] dựng lại công thức từ bố cục trang PDF gốc (font/vị trí ký tự, gạch phân số, dấu căn; "
                f"trang {', '.join(map(str, ref['pages']))}, câu {ref['question_number']}): {len(texs)} công thức, "
                "mọi ký tự trong vùng câu hỏi được dùng đúng một lần, ảnh trang không còn nét nào chưa giải thích, "
                "KaTeX hiển thị không lỗi"
                + (f"; lời giải lấy từ cùng câu trong {sib['ref']['document_id']} trang {sib['ref']['page']} "
                   f"(câu {sib['ref']['question_number']}, đề và phương án dựng lại giống hệt)"
                   if sib and sib["solution"] and rebuilt.get("solution") is sib["solution"] else "")
                + (f"; {nfig} hình cắt từ trang gốc (vùng nét vẽ của câu, kèm nhãn trong hình), cần xác nhận hình"
                   if nfig else ""))
        try:
            S.save_edit(db, q, who, edit, note=note, source_ref=ref, media_root=media_root,
                        tex_checks={t: {"ok": True} for t in texs})  # stored in formula_check with the version
        except S.ReviewError as ex:
            db.rollback()
            unresolved(f"draft refused: {ex}")
            continue
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        before = S.dim_view(q, r, v)["status"]
        if not conf and not single_edition:
            # one edition only: the rebuild waits for a reviewer (the formula dimension stays to be confirmed)
            db.commit()
            stats["drafts"] += 1
            unresolved("formula rebuilt from one edition only (draft in Review Studio, formula to be confirmed by a "
                       "reviewer)")
            continue
        conf_txt = ", ".join(f"{c['document_id']} trang {c['page']}" for c in conf)
        why_ok = (f"cùng câu ở ấn bản khác dựng lại giống hệt ({conf_txt})" if conf else
                  "chỉ có một ấn bản: mọi ký tự trong vùng câu được dùng đúng một lần, không còn ký tự/nét nào chưa giải "
                  "thích; độ chính xác của bộ dựng đo trên 100 bản nháp một ấn bản ngẫu nhiên đối chiếu ảnh trang")
        stats["formula_fixed"] += 1
        if before.get("formula") not in ("ok", "na"):
            S.confirm(db, q, who, "formula", "ok" if texs else "na",
                      f"[{BATCH}] {len(texs)} công thức dựng lại từ bố cục PDF (LaTeX), đối chiếu ảnh trang, KaTeX OK; "
                      + why_ok,
                      media_root=media_root, method="pdf_layout")
        if before.get("content") != "ok":
            S.confirm(db, q, who, "content", "ok",
                      f"[{BATCH}] đề và phương án dựng lại từ trang {ref['page']} của tài liệu gốc; khớp từ ngữ bản cũ "
                      f"({same:.0%})", media_root=media_root, method="pdf_layout")
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
            # the key table is positional; the worked solution printed right after the same question is not
            av = Verdict(True, f"bảng đáp án gốc {key} + lời giải gốc “Chọn đáp án {said}” ngay sau câu hỏi "
                               f"({said_ref['document_id']} trang {said_ref['page']}, câu {said_ref['question_number']})")
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
            # unclassified upstream: the subject of the single-subject collection the book belongs to, when the
            # question's own content agrees
            path = (q.provenance or {}).get("document_path") or ""
            coll = collection_subject(path)
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
                      + (f"; lời giải gốc: Chọn đáp án {said}" if said and "Chọn đáp án" not in av.reason else ""),
                      media_root=media_root, method=METHOD)
        if dims.get("subject") != "ok":
            S.confirm(db, q, who, "subject", "ok", f"[{BATCH}] {q.subject_code}: {sj.reason}", media_root=media_root,
                      method=METHOD)
        if b.get("figures") and (C.images(v.content) or C.has_table(v.content)):
            # figures cut from the page: a person (or the reviewer's contact-sheet check) confirms them first
            if dims.get("visual") != "ok":
                stats.setdefault("visual_pending", []).append(q.external_id)
                unresolved("figure(s) cut from the source page (draft in Review Studio): visual confirmation needed")
                continue
        elif C.images(v.content) or C.has_table(v.content):
            unresolved("formula rebuilt (draft in Review Studio); the stored version carries a figure/table")
            continue
        if dims.get("visual") not in ("ok", "na"):
            S.confirm(db, q, who, "visual", "na", f"[{BATCH}] không có hình (vùng câu hỏi trên trang gốc không có "
                      "hình vẽ/ảnh)", media_root=media_root, method=METHOD)
        try:
            res = S.approve(db, q, who, f"[{BATCH}] tự động theo nguồn: công thức dựng lại từ PDF gốc, đáp án khớp "
                                        "tài liệu gốc", media_root=media_root)
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
