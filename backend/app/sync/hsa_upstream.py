"""Read-only adapter for /root/imtoiteu/HSA-question-bank (see docs/INTEGRATION_CONTRACT.md).

`UpstreamSource` opens the canonical SQLite snapshot read-only and loads the editorial overlays
and (when present) the editorial-state manifests. `build_question` turns one canonical question
into a `BuiltQuestion`: render-ready content, normalised answer, editorial states (upstream
manifest if available, otherwise derived with the same rules as upstream `editorial_states`),
app-level content checks and provenance.
"""
import collections
import glob
import hashlib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..content import answers as A
from ..content import render as R
from .media import MediaStore, StoredFile, audit_image, convert_vector_to_png, trimmed_png_bytes
from .vendor import formula_check, wmf_math

log = logging.getLogger(__name__)

PRIORITY = ["REJECTED", "NEEDS_FORMULA_REVIEW", "NEEDS_VISUAL_REVIEW", "NEEDS_ANSWER_LINKING", "NEEDS_REVIEW",
            "READY_TO_SERVE"]
FORMULA_OK = {"ORIGINAL_STRUCTURED", "EXTRACTED_VALIDATED", "RECONSTRUCTED_VALIDATED"}
FIGURE_HINT = re.compile(r"hình vẽ|hình bên|như hình|đồ thị|biểu đồ|bảng biến thiên|sơ đồ|hình dưới|hình sau|"
                         r"bảng số liệu|bảng sau|figure|chart|graph|diagram", re.I)
MATHY = re.compile(r"[√π∫∑≤≥≠±×÷∞∈∆Δαβγλ]|\^|\bsin\b|\bcos\b|\blog\b|\blim\b|\d\s*/\s*\d|[a-z]\s*=\s*\d")
MATH_SUBJECTS = {"math", "physics", "chemistry"}
INLINE_OPTIONS = re.compile(r"\*\*A\*\*|(?:^|\s)A\.\s.+\sB\.\s", re.S)
BUILDER_VERSION = "hsa-builder-3"  # bump when build_question output changes (forces rebuild)


def _jl(path: Path):
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def _file_sig(p: Path) -> str:
    try:
        st = p.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"
    except FileNotFoundError:
        return "-"


@dataclass
class BuiltQuestion:
    external_id: str
    question_type: str
    source_subject: str | None
    topic: str | None
    subtopic: str | None
    cognitive_level: str | None
    language: str | None
    group_key: str | None
    exam_systems: list
    has_image: bool
    has_formula: bool
    has_table: bool
    review_status: str | None
    review_flags: list
    answer_source_type: str | None
    content: dict
    answer: dict | None
    states: list
    state_source: str
    state_notes: list
    scoring_mode: str
    app_reasons: list
    provenance: dict
    media: dict = field(default_factory=dict)  # sha -> StoredFile
    inferred_subject: str | None = None
    inference_confidence: str | None = None
    inference_evidence: list | None = None

    @property
    def content_hash(self) -> str:
        blob = json.dumps({"c": self.content, "a": self.answer}, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class UpstreamSource:
    def __init__(self, root: Path, media: MediaStore):
        self.root = Path(root)
        self.qb = self.root / "question-bank"
        self.db_path = self.qb / "sqlite" / "hsa_question_bank.sqlite"
        if not self.db_path.exists():
            raise FileNotFoundError(f"upstream SQLite not found: {self.db_path}")
        self.media = media
        # read-only; upstream replaces the file atomically so this handle is a consistent snapshot
        self.con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.con.row_factory = sqlite3.Row
        self.formulas = {r["formula_id"]: dict(r) for r in self.con.execute(
            "SELECT formula_id, source_format, latex, latex_confidence, issues, preview_asset_id FROM formula")}
        self.assets = {r["asset_id"]: dict(r) for r in self.con.execute("SELECT * FROM asset")}
        self.docs = {r["document_id"]: dict(r) for r in self.con.execute(
            "SELECT document_id, file_kind, extraction_method, document_role, exam_system, exam_name, exam_type, "
            "year, representative_path FROM document")}
        ed = self.root / "editorial"
        self.f_over = {o.get("new_formula_id") or o["formula_id"]: o for o in _jl(ed / "formula_overrides.jsonl")}
        self.q_over = {o["question_id"]: o for o in _jl(ed / "question_overrides.jsonl")}
        self.asset_repl = {o["asset_id"]: o for o in _jl(ed / "asset_replacements.jsonl")}
        figs = _jl(ed / "figure_overrides.jsonl")
        self.fig_by_asset = {o["asset_id"]: o for o in figs if o.get("asset_id")}
        self.fig_over = collections.defaultdict(list)
        for o in figs:
            if o.get("question_id") and not o.get("asset_id"):
                self.fig_over[o["question_id"]].append(o)
        self.manifest_states = self._load_manifests()
        # second-pass subject inference (canonical subject is unchanged upstream)
        self.inference = {o["question_id"]: o for o in _jl(ed / "subject_inference.jsonl")}
        # upstream's validated final subject decision (supersedes the second pass when present)
        self.effective = {o["question_id"]: o for o in _jl(ed / "subject_effective.jsonl")}
        self.vec_cache = self.root / "rendered" / "cache" / "vec"
        self._fstatus: dict = {}
        self._wmf: dict = {}
        self._img: dict = {}

    # ---- identity of the snapshot -------------------------------------------------------------
    def fingerprint(self) -> str:
        parts = [_file_sig(self.db_path)]
        for n in ("question_overrides", "formula_overrides", "figure_overrides", "asset_replacements",
                  "subject_inference", "subject_effective"):
            parts.append(_file_sig(self.root / "editorial" / f"{n}.jsonl"))
        parts.append(f"manifest:{len(self.manifest_states)}")
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16] + f"@{BUILDER_VERSION}"

    def _load_manifests(self) -> dict:
        states = {}
        for p in sorted(glob.glob(str(self.root / "rendered" / "docx" / "_manifest" / "*_Vol*.json"))):
            try:
                m = json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("unreadable editorial manifest %s (skipped)", p)
                continue
            for r in m.get("rows", []):
                if r.get("question_id") and r.get("states"):
                    states[r["question_id"]] = {"states": r["states"], "notes": r.get("notes") or []}
        return states

    def upstream_revision(self) -> str | None:
        """Git commit of the upstream project, read from .git without running git (read-only)."""
        head = self.root / ".git" / "HEAD"
        try:
            ref = head.read_text().strip()
            if ref.startswith("ref: "):
                p = self.root / ".git" / ref[5:]
                if p.exists():
                    return p.read_text().strip()[:12]
                packed = self.root / ".git" / "packed-refs"
                for line in packed.read_text().splitlines() if packed.exists() else []:
                    if line.endswith(ref[5:]):
                        return line.split()[0][:12]
                return None
            return ref[:12]
        except OSError:
            return None

    def deferred_documents(self) -> list[dict]:
        """Scanned PDFs without a text layer (no questions extracted; upstream NEEDS_MATH_AWARE_OCR).
        Read from the upstream pipeline state (read-only); empty if unavailable."""
        state = self.root / "inventory" / "state.sqlite"
        if not state.exists():
            return []
        out = []
        try:
            con = sqlite3.connect(f"file:{state}?mode=ro", uri=True)
            try:
                con.execute("SELECT 1 FROM blobs LIMIT 1")
            except sqlite3.OperationalError:
                # WAL database on a read-only mount: SQLite cannot create its -shm file. Open it as
                # immutable (still strictly read-only; ignores in-flight WAL pages of the live pipeline).
                con.close()
                con = sqlite3.connect(f"file:{state}?mode=ro&immutable=1", uri=True)
            con.row_factory = sqlite3.Row
            for r in con.execute("SELECT sha256, doc_detail FROM blobs WHERE doc_status='NEEDS_OCR'"):
                detail = json.loads(r["doc_detail"]) if r["doc_detail"] else {}
                path = con.execute("SELECT member_path FROM occurrences WHERE blob_sha=? AND member_path IS NOT NULL "
                                   "LIMIT 1", (r["sha256"],)).fetchone()
                if not path:
                    path = con.execute("SELECT rel_path FROM source_files WHERE sha256=? LIMIT 1",
                                       (r["sha256"],)).fetchone() if _has_col(con, "source_files", "rel_path") else None
                out.append({"external_id": f"doc_{r['sha256'][:16]}", "path": path[0] if path else None,
                            "status": "NEEDS_MATH_AWARE_OCR", "pages": detail.get("pages"), "detail": detail})
            con.close()
        except sqlite3.Error as ex:
            log.warning("cannot read upstream pipeline state for deferred documents: %s", ex)
        return out

    # ---- iteration ------------------------------------------------------------------------------
    def count(self) -> int:
        return self.con.execute("SELECT count(*) FROM canonical_question").fetchone()[0]

    def all_ids(self) -> set:
        return {r[0] for r in self.con.execute("SELECT canonical_question_id FROM canonical_question")}

    def iter_questions(self, after: str | None = None, only: list | None = None):
        if only:
            ph = ",".join("?" * len(only))
            rows = self.con.execute(f"SELECT * FROM canonical_question WHERE canonical_question_id IN ({ph}) "
                                    "ORDER BY canonical_question_id", only)
        else:
            rows = self.con.execute("SELECT * FROM canonical_question WHERE canonical_question_id > ? "
                                    "ORDER BY canonical_question_id", (after or "",))
        for r in rows:
            yield self._load(r)

    def _load(self, r) -> dict:
        q = dict(r)
        for k in ("tables", "correct_answer", "review_flags", "exam_systems"):
            q[k] = json.loads(q[k]) if q[k] else ([] if k != "correct_answer" else None)
        cid = q["canonical_question_id"]
        q["options"] = [dict(o) for o in self.con.execute(
            "SELECT label, content_md, is_correct FROM canonical_option WHERE canonical_question_id=? ORDER BY label",
            (cid,))]
        rep = self.con.execute("SELECT question_id, document_id, source_question_number, section, location, "
                               "review_flags, exam_system, year, extraction_method FROM question_occurrence "
                               "WHERE question_id=?", (q["representative_occurrence_id"],)).fetchone()
        q["rep"] = dict(rep) if rep else {}
        if rep:
            q["rep"]["review_flags"] = json.loads(rep["review_flags"]) if rep["review_flags"] else []
        q["doc"] = self.docs.get(q["rep"].get("document_id"), {})
        q["group"] = None
        if q["group_id"]:
            g = self.con.execute("SELECT * FROM question_group WHERE group_id=?", (q["group_id"],)).fetchone()
            if g:
                q["group"] = dict(g)
                q["group"]["tables"] = json.loads(g["tables"]) if g["tables"] else []
        return q

    # ---- formulas -------------------------------------------------------------------------------
    def asset_path(self, a) -> Path:
        return self.qb / a["storage_path"]

    def formula_status(self, fid):
        if fid in self._fstatus:
            return self._fstatus[fid]
        ov = self.f_over.get(fid)
        f = self.formulas.get(fid)
        if f is None and ov:
            st = (ov.get("status", "NEEDS_FORMULA_REVIEW"), {"override": True})
        elif f is None:
            st = ("NEEDS_FORMULA_REVIEW", {"reason": "missing formula record"})
        else:
            rec = {"latex": f["latex"], "latex_confidence": f["latex_confidence"] or 0,
                   "source_format": f["source_format"], "issues": json.loads(f["issues"]) if f["issues"] else []}
            wmf = None
            a = self.assets.get(f["preview_asset_id"] or "")
            if a and a["ext"] == "wmf":
                p = self.asset_path(a)
                wmf = p.read_bytes() if p.exists() else None
            # texmath (TeX→OMML) acceptance is a DOCX concern; KaTeX renders the LaTeX on the web
            st = formula_check.formula_status(rec, wmf, texmath_ok=True, override=ov)
        self._fstatus[fid] = st
        return st

    def latex_of(self, fid):
        ov = self.f_over.get(fid)
        if ov and ov.get("status") == "RECONSTRUCTED_VALIDATED":
            return ov["latex"]
        f = self.formulas.get(fid)
        if not f or not f["latex"]:
            return None
        latex = f["latex"]
        if f["source_format"] == "wmf_picture":
            latex = wmf_math.cosmetic(latex)
        return latex

    def wmf_reconstruct(self, aid):
        if aid not in self._wmf:
            a = self.assets.get(aid)
            res = None
            if a and a["ext"] == "wmf":
                p = self.asset_path(a)
                if p.exists():
                    try:
                        res = wmf_math.reconstruct(p.read_bytes())[0]
                    except Exception:  # noqa: BLE001
                        res = None
            self._wmf[aid] = res
            if len(self._wmf) > 50000:
                self._wmf.clear()
        return self._wmf[aid]

    # ---- images ---------------------------------------------------------------------------------
    def stored_image(self, aid) -> tuple[StoredFile | None, list, dict]:
        """(web-displayable stored file, audit issues, info) for an upstream asset; memoised."""
        if aid in self._img:
            return self._img[aid]
        a = self.assets.get(aid)
        result = (None, ["missing"], {})
        if a:
            src = self.asset_path(a)
            if src.exists():
                orig_sha = a["sha256"]
                if a["ext"] in ("wmf", "emf"):
                    cached = self.vec_cache / f"{aid}.png"   # upstream LibreOffice render when available
                    data = cached.read_bytes() if cached.exists() else convert_vector_to_png(src)
                    sf = self.media.put_bytes(data, "png", derived_from=orig_sha) if data else None
                elif a["ext"] in ("png", "jpg", "jpeg", "gif", "jfif", "tmp"):
                    from .media import sniff_ext
                    ext = sniff_ext(src) if a["ext"] == "tmp" else a["ext"]
                    sf = self.media.put(src, ext=ext, sha=orig_sha) if ext in ("png", "jpg", "jpeg", "gif") else None
                else:
                    sf = None
                if sf is None:
                    result = (None, ["unconvertible"], {"ext": a["ext"]})
                else:
                    p = self.media.path_of(sf.sha256, sf.ext)
                    issues, info = audit_image(p)
                    if "excessive_whitespace" in issues and info.get("bbox"):
                        data = trimmed_png_bytes(p, info["bbox"])
                        if data:
                            sf = self.media.put_bytes(data, "png", derived_from=sf.sha256)
                    result = (sf, issues, info)
        self._img[aid] = result
        if len(self._img) > 20000:
            self._img.clear()
        return result

    def redrawn_file(self, rel: str) -> StoredFile | None:
        p = self.root / rel
        if not p.exists():
            return None
        return self.media.put(p)


def _has_col(con, table, col) -> bool:
    return any(r[1] == col for r in con.execute(f"PRAGMA table_info({table})"))


def _img_block(sf: StoredFile, max_w: int | None = None) -> dict:
    b = {"t": "img", "src": sf.rel_path}
    if sf.width and sf.height:
        b["w"], b["h"] = sf.width, sf.height
    return b


class _Resolver(R.Resolver):
    pass


def make_resolver(src: UpstreamSource, cid: str, media_out: dict) -> R.Resolver:
    res = _Resolver()

    def keep(sf: StoredFile):
        media_out[sf.sha256] = sf
        res.assets.add(sf.sha256)

    def formula(fid):
        st, detail = src.formula_status(fid)
        latex = src.latex_of(fid)
        if st in FORMULA_OK and latex:
            return [{"t": "m", "tex": latex}]
        f = src.formulas.get(fid) or {}
        a = src.assets.get(f.get("preview_asset_id") or "")
        if a and a["ext"] == "wmf":
            rr = src.wmf_reconstruct(a["asset_id"])
            if rr:
                if rr["kind"] == "text":
                    return [{"t": "s", "v": rr["text"]}]
                return [{"t": "m", "tex": rr["latex"]}]
        res.issue("formula", f"formula {fid} unresolved")
        if a:
            sf, _, _ = src.stored_image(a["asset_id"])
            if sf:
                keep(sf)
                return [dict(_img_block(sf), t="img", inline=True, review=True)]
        if latex:
            return [{"t": "m", "tex": latex, "review": True}]
        return [{"t": "warn", "v": "công thức cần kiểm tra"}]

    def image(aid):
        rep = src.asset_repl.get(aid)
        if rep:
            if rep.get("source_content_suspect"):
                res.issue("content", "source content suspect: " + rep["source_content_suspect"])
            if rep["kind"] == "decorative_remove":
                return [], []
            if rep["kind"] == "md":
                inl = []
                for k, line in enumerate(rep["md"].split("\n")):
                    if k:
                        inl.append({"t": "br"})
                    inl += R.inlines(line, res)
                return inl, []
            if rep["kind"] == "table":
                blocks = R.blocks(rep.get("lead_md") or "", res)
                rows = [[{"md": c} for c in r] for r in rep["rows"]]
                blocks.append(R.table_block({"rows": rows}, res))
                return [], blocks
        red = src.fig_by_asset.get(aid)
        if red and red.get("figure_status") == "REDRAWN_VALIDATED":
            sf = src.redrawn_file(red["redrawn_path"])
            if sf:
                keep(sf)
                out = [_img_block(sf)]
                if red.get("caption_md"):
                    out += R.blocks(red["caption_md"], res)
                return [], out
        a = src.assets.get(aid)
        if not a or not src.asset_path(a).exists():
            res.issue("missing_visual", f"image {aid} missing")
            return [{"t": "warn", "v": "thiếu hình"}], []
        if a["ext"] == "wmf":
            rr = src.wmf_reconstruct(aid)
            if rr:
                return ([{"t": "s", "v": rr["text"]}] if rr["kind"] == "text" else [{"t": "m", "tex": rr["latex"]}]), []
        sf, issues, info = src.stored_image(aid)
        if sf is None:
            res.issue("missing_visual", f"image {aid} not displayable ({','.join(issues)})")
            return [{"t": "warn", "v": "thiếu hình"}], []
        if "watermark_like" in issues or "mostly_blank" in issues:
            return [], []
        if "tiny_glyph_image" in issues:
            res.issue("formula", f"unresolved raster text fragment {aid}")
            keep(sf)
            return [dict(_img_block(sf), inline=True, review=True)], []
        if "page_screenshot" in issues:
            res.issue("visual", f"page screenshot {aid}")
        if "low_resolution" in issues:
            res.issue("visual", f"low-resolution figure {aid} {info.get('size')}")
        keep(sf)
        return [], [_img_block(sf)]

    def redrawn():
        figs = [f for f in src.fig_over.get(cid, []) if f.get("figure_status") == "REDRAWN_VALIDATED"]
        if not figs:
            return []
        sf = src.redrawn_file(figs[0]["redrawn_path"])
        if not sf:
            res.issue("missing_visual", "redrawn figure file missing")
            return []
        keep(sf)
        return [_img_block(sf)]

    res.formula, res.image, res.redrawn = formula, image, redrawn
    return res


def _has_warn(nodes) -> bool:
    if isinstance(nodes, list):
        return any(_has_warn(n) for n in nodes)
    if not isinstance(nodes, dict):
        return False
    if nodes.get("t") == "warn" or nodes.get("review"):
        return True
    for k in ("c", "rows", "content", "header", "passage"):  # paragraphs, table cells, options, passages
        if k in nodes and _has_warn(nodes[k]):
            return True
    return False


def _strip_stem_echo(stem: str, sol: str | None) -> str | None:
    """Some sources repeat the stem at the start of the solution; drop the echo."""
    if not sol or not stem or len(stem) < 40:
        return sol
    common = len(os.path.commonprefix([stem, sol]))
    if common >= 0.8 * len(stem):
        rest = sol[common:].lstrip()
        return rest or None
    return sol


def _strip_answer_from_stem(stem: list, q: dict) -> tuple[list, str | None]:
    """Some sources print the key as a standalone last paragraph of the stem ("…là bao nhiêu mét?\n\n4,5").
    Drop that paragraph when it is exactly the source-provided key (never anything else)."""
    ans = q.get("correct_answer") or {}
    if q.get("answer_source_type") not in ("SOURCE_PROVIDED_ANSWER", "SOURCE_PROVIDED_SOLUTION") or len(stem) < 2:
        return stem, None
    key = (ans.get("text") or "").strip()
    last = stem[-1]
    if not key or last.get("t") != "p" or any(n.get("t") != "s" for n in last.get("c", [])):
        return stem, None
    text = "".join(n["v"] for n in last["c"]).strip().rstrip(".")
    return (stem[:-1], text) if text == key.rstrip(".") else (stem, None)


def derive_states(src: UpstreamSource, q: dict, issues: list, answer_source: str) -> tuple[list, list]:
    """Port of upstream editorial.editorial_states (render issues come from our resolver)."""
    states, notes = set(), []
    flags = set(q["review_flags"]) | set(q["rep"].get("review_flags", []))
    ov = src.q_over.get(q["canonical_question_id"])
    reconstructed = bool(ov and ov.get("status") == "RECONSTRUCTED_VALIDATED")
    if q["review_status"] == "rejected":
        states.add("REJECTED")
    for kind, detail in issues:
        if kind == "formula":
            states.add("NEEDS_FORMULA_REVIEW")
        elif kind in ("visual", "missing_visual"):
            states.add("NEEDS_VISUAL_REVIEW")
        elif kind == "content":
            states.add("NEEDS_REVIEW")
        notes.append(detail)
    pdf = q["doc"].get("extraction_method") == "pdf_text_layer"
    text = (q["stem_md"] or "") + " ".join(o["content_md"] or "" for o in q["options"])
    if pdf and not reconstructed:
        if q.get("subject") in MATH_SUBJECTS or MATHY.search(text):
            states.add("NEEDS_FORMULA_REVIEW")
            notes.append("PDF text layer may flatten mathematics")
        if FIGURE_HINT.search(text) and not src.fig_over.get(q["canonical_question_id"]):
            states.add("NEEDS_VISUAL_REVIEW")
            notes.append("figure referenced but not available from the PDF text layer")
    if "legacy_encoding_mojibake" in flags or "encoding_replacement_char" in flags:
        states.add("NEEDS_REVIEW")
    if answer_source in ("UNRESOLVED", "AUTOMATICALLY_INFERRED_CANDIDATE", None):
        states.add("NEEDS_ANSWER_LINKING")
    if q["review_status"] == "needs_review":
        resolved = {"pdf_text_layer_fidelity", "formula_low_confidence", "formula_unconverted"} if reconstructed else set()
        remaining = {f for f in q["review_flags"] if f not in resolved
                     and f not in ("missing_solution", "image_dependent", "missing_answer")}
        if remaining:
            states.add("NEEDS_REVIEW")
    if not states:
        states.add("READY_TO_SERVE")
    return [s for s in PRIORITY if s in states], notes


def build_question(src: UpstreamSource, q: dict, subject_of=lambda s: s, text_auto: bool = False) -> BuiltQuestion:
    cid = q["canonical_question_id"]
    ov = src.q_over.get(cid) or {}
    media: dict = {}
    res = make_resolver(src, cid, media)
    tables = q["tables"]

    # --- group / passage (shown once per group; header numbers neutralised)
    group = None
    if q["group"]:
        g = q["group"]
        header = R.blocks(R.neutralize_group_header(g["header_md"]), res, g["tables"])
        passage = R.blocks(g["content_md"], res, g["tables"])
        group = {"key": g["group_id"], "header": header, "passage": passage}

    stem_md = ov.get("stem_md") or q["stem_md"] or ""
    stem = R.blocks(stem_md, res, tables)
    stem, answer_stripped = _strip_answer_from_stem(stem, q)
    over_opts = ov.get("options") or {}
    opts_md = [(o["label"], over_opts.get(o["label"], o["content_md"] or "")) for o in q["options"]]
    if over_opts and not opts_md:
        opts_md = sorted(over_opts.items())
    options = [{"label": l, "content": R.blocks(md, res, tables)} for l, md in opts_md]

    sol_md = ov.get("solution_md") or q.get("solution_md")
    sol_md = _strip_stem_echo(q["stem_md"] or "", sol_md)
    expl_md = ov.get("explanation_md") or q.get("explanation_md")
    explanation = R.blocks(expl_md, res, tables) if expl_md else None
    solution = R.blocks(sol_md, res, tables) if sol_md else None

    # --- answer: source evidence only (overlay may transcribe a source-evidenced key)
    raw_ans, src_type = q["correct_answer"], q["answer_source_type"]
    if ov.get("answer") and (not raw_ans or src_type == "UNRESOLVED"):
        raw_ans, src_type = {"kind": "labels", "labels": ov["answer"]["labels"]}, "EDITORIAL_TRANSCRIBED"
    labels = [l for l, _ in opts_md]
    answer, ans_problem = A.normalize_answer(q["question_type"], raw_ans, src_type, labels, dict(opts_md))
    inp = A.input_spec(q["question_type"], opts_md, answer, stem_md)
    mode = A.scoring_mode(inp, answer, bool(solution or explanation), text_auto=text_auto)

    # --- editorial states
    if cid in src.manifest_states:
        states, notes, state_source = src.manifest_states[cid]["states"], src.manifest_states[cid]["notes"], \
            "upstream_manifest"
    else:
        states, notes = derive_states(src, q, res.issues, src_type)
        state_source = "derived"

    # --- app-level content checks
    reasons = []
    if ans_problem:
        reasons.append(ans_problem)
    if q["group"] and not (q["group"]["content_md"] or "").strip() and not q["group"]["tables"]:
        reasons.append("group_context_missing")
    if q["question_type"] == "short_response" and INLINE_OPTIONS.search(stem_md):
        reasons.append("inline_options_in_short_response")
    if not R.plain_text(stem).strip() and not any(b["t"] in ("img", "table") for b in stem):
        reasons.append("empty_stem")
    if inp["kind"] == "choice":
        if len(options) < 2:
            reasons.append("too_few_options")
        if any(not o["content"] for o in options):
            reasons.append("empty_option")
    # content the student would see that cannot be displayed (placeholders / unvalidated fallbacks);
    # editorial issues such as low resolution are already expressed by the editorial state
    if _has_warn(stem) or _has_warn(options) or (group and _has_warn(group)):
        reasons.append("render_warning")
    if inp["kind"] == "none" and mode == "none":
        reasons.append("no_input")
    if q["question_type"] == "open_or_unknown":
        reasons.append("unsupported_for_serving")
    if q["group"]:
        rng = R.header_range(q["group"].get("header_md"))
        num = q["rep"].get("source_question_number")
        if rng and num is not None and not (rng[0] <= num <= rng[1]):
            # the passage header names other questions: this question is attached to the wrong passage
            reasons.append("group_membership_suspect")

    content = {
        "type": q["question_type"],
        "language": q.get("language") or "vi",
        "input": inp,
        "group": group,
        "stem": stem,
        "options": options,
        "solution": solution,
        "explanation": explanation,
        "shuffle_safe": A.shuffle_safe(q["question_type"], {l: R.plain_text(o["content"]) for l, o in
                                                          zip(labels, options)}),
        "assets": sorted(res.assets),
    }
    rep, doc = q["rep"], q["doc"]
    provenance = {
        "representative_occurrence_id": q["representative_occurrence_id"],
        "document_id": rep.get("document_id"),
        "document_path": doc.get("representative_path"),
        "document_role": doc.get("document_role"),
        "extraction_method": doc.get("extraction_method"),
        "exam_name": doc.get("exam_name"),
        "exam_system": rep.get("exam_system") or doc.get("exam_system"),
        "year": rep.get("year") or doc.get("year"),
        "section": rep.get("section"),
        "source_question_number": rep.get("source_question_number"),
        "n_occurrences": q.get("n_occurrences"),
        "answer_confidence": q.get("answer_confidence"),
        "overlay": ov.get("status") if ov else None,
        "render_issues": [d for _, d in res.issues][:20],
        "answer_removed_from_stem": answer_stripped or None,
    }
    return BuiltQuestion(
        external_id=cid, question_type=q["question_type"], source_subject=q.get("subject"),
        topic=q.get("topic"), subtopic=q.get("subtopic"), cognitive_level=q.get("cognitive_level"),
        language=q.get("language"), group_key=q.get("group_id"), exam_systems=q.get("exam_systems") or [],
        has_image=bool(q.get("has_image")), has_formula=bool(q.get("has_formula")),
        has_table=bool(q.get("has_table")), review_status=q.get("review_status"),
        review_flags=q.get("review_flags") or [], answer_source_type=src_type, content=content, answer=answer,
        states=states, state_source=state_source, state_notes=notes[:20], scoring_mode=mode,
        app_reasons=sorted(set(reasons)), provenance=provenance, media=media,
        inferred_subject=(src.inference.get(cid) or {}).get("inferred_subject"),
        inference_confidence=(src.inference.get(cid) or {}).get("confidence"),
        inference_evidence=(src.inference.get(cid) or {}).get("evidence"))
