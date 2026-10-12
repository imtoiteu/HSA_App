"""Source documents for the Review Studio viewer (read-only).

The upstream bank keeps the original files inside the archives of `source-original/` (its content-addressed
blob store is mostly dematerialised). A document is prepared on request by the worker:

1. follow its `source_chains` (archive → nested archive → member) with bsdtar/7z/unzip, reading only;
2. verify the extracted bytes against the document's `blob_sha256` (a mismatch is never shown);
3. convert DOC/DOCX to PDF with LibreOffice (headless, niced) so every source is viewed as pages;
4. pages are rendered lazily with pdftoppm and cached (thumbnails and full pages at fixed resolutions).

Files live under HSA_SOURCE_ROOT (a private volume), never under the public media store. When the source
cannot be found or verified the status is SOURCE_UNAVAILABLE; provenance is never invented.
"""
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)
DOC_RE = re.compile(r"^doc_[0-9a-f]{8,32}$")
DPIS = (48, 110, 160, 220)
_render_lock = threading.Semaphore(2)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


class Upstream:
    """Read-only access to the upstream SQLite (opened in immutable/read-only mode per call)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.db = self.root / "question-bank" / "sqlite" / "hsa_question_bank.sqlite"

    def _con(self):
        if not self.db.exists():
            return None
        return sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)

    def document(self, document_id: str) -> dict | None:
        con = self._con()
        if con is None:
            return None
        try:
            r = con.execute("SELECT document_id, blob_sha256, file_kind, extraction_method, document_role, exam_name, "
                            "year, representative_path, source_chains FROM document WHERE document_id=?",
                            (document_id,)).fetchone()
        finally:
            con.close()
        if not r:
            return None
        keys = ["document_id", "blob_sha256", "file_kind", "extraction_method", "document_role", "exam_name", "year",
                "representative_path", "source_chains"]
        d = dict(zip(keys, r))
        d["source_chains"] = json.loads(d["source_chains"] or "[]")
        return d

    def occurrences(self, canonical_id: str) -> list[dict]:
        con = self._con()
        if con is None:
            return []
        try:
            rows = con.execute("SELECT question_id, document_id, source_question_number, section, location, "
                               "extraction_method FROM question_occurrence WHERE canonical_question_id=?",
                               (canonical_id,)).fetchall()
        finally:
            con.close()
        out = []
        for qid, doc, num, sec, loc, meth in rows:
            loc = json.loads(loc or "{}")
            out.append({"occurrence_id": qid, "document_id": doc, "source_question_number": num, "section": sec,
                        "extraction_method": meth, "unit": loc.get("unit"),
                        "pages": pages_of(loc.get("blocks")), "solution_pages": pages_of(loc.get("solution_blocks")),
                        "blocks": (loc.get("blocks") or [])[:3]})
        return out

    def page_map(self) -> dict:
        """occurrence id → first page (PDF text-layer occurrences), for review-refresh."""
        con = self._con()
        if con is None:
            return {}
        out = {}
        try:
            for qid, loc in con.execute("SELECT question_id, location FROM question_occurrence "
                                        "WHERE location LIKE '%pdf_page%'"):
                p = pages_of(json.loads(loc or "{}").get("blocks"))
                if p:
                    out[qid] = p[0]
        finally:
            con.close()
        return out


def pages_of(blocks) -> list[int]:
    pages = []
    for b in blocks or []:
        m = re.match(r"^p(\d+)\.", str(b))
        if m and int(m.group(1)) not in pages:
            pages.append(int(m.group(1)))
    return pages


# ------------------------------------------------------------------------------------------------
# cache layout
# ------------------------------------------------------------------------------------------------
class SourceStore:
    def __init__(self, root: Path, upstream_root: Path):
        self.root = Path(root)
        self.up = Upstream(upstream_root)
        self.upstream_root = Path(upstream_root)

    def pdf_path(self, sha: str) -> Path:
        return self.root / "pdf" / sha[:2] / f"{sha}.pdf"

    def text_path(self, sha: str) -> Path:
        return self.root / "text" / sha[:2] / f"{sha}.json"

    def page_path(self, sha: str, page: int, dpi: int) -> Path:
        return self.root / "pages" / sha[:2] / sha / f"{dpi}_{page}.png"

    # --------------------------------------------------------------------------------------------
    def prepare(self, document_id: str) -> dict:
        """Materialise (and convert) one document. Returns {status, pages, file_kind, detail}."""
        doc = self.up.document(document_id)
        if doc is None:
            return {"status": "unavailable", "detail": "Tài liệu không có trong ngân hàng nguồn."}
        sha, kind = doc["blob_sha256"], (doc["file_kind"] or "").lower()
        pdf = self.pdf_path(sha)
        if pdf.exists():
            return {"status": "ready", "pages": page_count(pdf), "file_kind": kind, "blob_sha256": sha}
        with tempfile.TemporaryDirectory(prefix="hsa_src_", dir=self._tmp()) as tmp:
            src = self._materialize(doc, Path(tmp))
            if src is None:
                return {"status": "unavailable", "file_kind": kind, "blob_sha256": sha,
                        "detail": "Không tìm thấy hoặc không xác minh được tệp gốc trong source-original."}
            if kind == "pdf":
                out = src
            elif kind in ("docx", "doc"):
                out = convert_to_pdf(src, Path(tmp))
                if out is None:
                    return {"status": "failed", "file_kind": kind, "blob_sha256": sha,
                            "detail": "Không chuyển được DOC/DOCX sang PDF."}
            else:
                return {"status": "unavailable", "file_kind": kind, "blob_sha256": sha,
                        "detail": f"Định dạng {kind} chưa hỗ trợ xem trước."}
            pdf.parent.mkdir(parents=True, exist_ok=True)
            part = pdf.with_suffix(".part")
            shutil.copyfile(out, part)  # never move: `out` may be the read-only upstream blob itself
            part.replace(pdf)
        _give_to_app(self.root, pdf)
        return {"status": "ready", "pages": page_count(pdf), "file_kind": kind, "blob_sha256": sha}

    def _tmp(self) -> str:
        p = self.root / "tmp"
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def _materialize(self, doc: dict, tmp: Path) -> Path | None:
        sha = doc["blob_sha256"]
        ext = (doc["file_kind"] or "bin").lower()
        blob = self.upstream_root / "extracted" / "blobs" / sha[:2] / f"{sha}.{ext}"
        if blob.exists() and sha256_file(blob) == sha:
            return blob
        for chain in doc["source_chains"]:
            steps = chain.get("chain") or []
            if not steps:
                continue
            cur = find_nfc(self.upstream_root / "source-original", steps[0]["path"])
            try:
                for i, step in enumerate(steps[1:]):
                    if cur is None:
                        break
                    nxt = tmp / f"step{i}_{Path(step['path']).name[-60:]}"
                    cur = extract_member(cur, step["path"], nxt)
                if cur is not None and cur.exists() and sha256_file(cur) == sha:
                    return cur
            except Exception as ex:  # noqa: BLE001  try the next chain
                log.warning("source chain failed for %s: %s", doc["document_id"], ex)
        return None

    # --------------------------------------------------------------------------------------------
    def render(self, sha: str, page: int, dpi: int) -> Path | None:
        dpi = min(DPIS, key=lambda d: abs(d - dpi))
        out = self.page_path(sha, page, dpi)
        if out.exists():
            return out
        pdf = self.pdf_path(sha)
        if not pdf.exists():
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        with _render_lock:
            prefix = out.parent / f"tmp_{dpi}_{page}_{os.getpid()}_{threading.get_ident()}"
            r = subprocess.run(["nice", "-n", "5", "pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi),
                                "-png", "-singlefile", str(pdf), str(prefix)], capture_output=True, timeout=90)
            made = Path(str(prefix) + ".png")
            if r.returncode != 0 or not made.exists():
                return None
            made.replace(out)
        return out

    def page_texts(self, sha: str) -> list[str]:
        tp = self.text_path(sha)
        if tp.exists():
            return json.loads(tp.read_text(encoding="utf-8"))
        pdf = self.pdf_path(sha)
        if not pdf.exists():
            return []
        r = subprocess.run(["nice", "-n", "5", "pdftotext", "-enc", "UTF-8", str(pdf), "-"], capture_output=True,
                           timeout=180)
        pages = r.stdout.decode("utf-8", "replace").split("\f") if r.returncode == 0 else []
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        return pages

    def locate(self, sha: str, text: str) -> list[dict]:
        """Pages whose text best matches a question's text (for sources without page metadata)."""
        words = re.findall(r"\w+", nfc(text).lower())[:60]
        if len(words) < 3:
            return []
        grams = {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}
        scored = []
        for i, t in enumerate(self.page_texts(sha)):
            tw = re.findall(r"\w+", nfc(t).lower())
            tg = {" ".join(tw[j:j + 3]) for j in range(len(tw) - 2)}
            hit = len(grams & tg)
            if hit:
                scored.append({"page": i + 1, "score": round(hit / len(grams), 3)})
        scored.sort(key=lambda x: -x["score"])
        return scored[:5]


# ------------------------------------------------------------------------------------------------
# tools
# ------------------------------------------------------------------------------------------------
APP_UID = 10001  # the API/scheduler user (Dockerfile); the sources worker runs as root to read upstream archives


def _give_to_app(root: Path, path: Path):
    """Files prepared by the (root) sources worker must stay readable/writable by the API user."""
    if os.geteuid() != 0:
        return
    for p in [path, *[x for x in path.parents if x == root or root in x.parents]]:
        try:
            os.chown(p, APP_UID, APP_UID)
        except OSError:
            pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_nfc(base: Path, rel: str) -> Path | None:
    """Resolve a relative path whose components may differ from the disk only by Unicode normalisation."""
    cur = base
    for part in Path(rel).parts:
        cand = cur / part
        if cand.exists():
            cur = cand
            continue
        try:
            names = {nfc(n): n for n in os.listdir(cur)}
        except OSError:
            return None
        if nfc(part) not in names:
            return None
        cur = cur / names[nfc(part)]
    return cur if cur.is_file() else None


def _list_members(archive: Path) -> list[str]:
    # 7z first: libarchive misreads some RAR archives of the bank (lists a nested archive's members instead)
    for cmd in (["7z", "l", "-slt", "-ba", str(archive)], ["bsdtar", "-tf", str(archive)]):
        if not shutil.which(cmd[0]):
            continue
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            continue
        out = r.stdout.decode("utf-8", "replace")
        if cmd[0] == "7z":
            return [l[7:] for l in out.splitlines() if l.startswith("Path = ")]
        return out.splitlines()
    if archive.suffix.lower() == ".zip" or archive.read_bytes()[:2] == b"PK":
        import zipfile
        with zipfile.ZipFile(archive) as z:
            return z.namelist()
    return []


def extract_member(archive: Path, member: str, out: Path) -> Path | None:
    """Extract one member (matched with Unicode normalisation) to `out`. Read-only on the archive."""
    names = _list_members(archive)
    target = next((n for n in names if nfc(n) == nfc(member)), None)
    if target is None:
        target = next((n for n in names if nfc(n).endswith(nfc(member))), None)
    if target is None:
        return None
    with open(out, "wb") as f:
        for cmd in (["nice", "-n", "15", "7z", "e", "-so", str(archive), target],
                    ["nice", "-n", "15", "bsdtar", "-xOf", str(archive), target]):
            if not shutil.which(cmd[3]):
                continue
            f.seek(0)
            f.truncate()
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, timeout=900)
            if r.returncode == 0 and f.tell() > 0:
                return out
    try:
        import zipfile
        with zipfile.ZipFile(archive) as z, open(out, "wb") as f:
            f.write(z.read(target))
        return out
    except Exception:  # noqa: BLE001
        return None


def convert_to_pdf(src: Path, tmp: Path) -> Path | None:
    if not shutil.which("soffice"):
        return None
    work = tmp / "conv"
    work.mkdir(exist_ok=True)
    ext = src.suffix.lower() if src.suffix.lower() in (".doc", ".docx") else ".docx"
    inp = work / f"source{ext}"
    shutil.copyfile(src, inp)
    env = dict(os.environ, HOME=str(work))
    try:
        subprocess.run(["nice", "-n", "15", "soffice", "--headless", "--norestore", "--convert-to", "pdf",
                        "--outdir", str(work), str(inp)], capture_output=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        return None
    out = work / "source.pdf"
    return out if out.exists() and out.stat().st_size > 0 else None


def page_count(pdf: Path) -> int | None:
    if not shutil.which("pdfinfo"):
        return None
    r = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, timeout=60)
    m = re.search(rb"Pages:\s+(\d+)", r.stdout)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------------------------------------
# worker queue (source_cache rows)
# ------------------------------------------------------------------------------------------------
def request(db, document_id: str, user_id: int | None) -> dict:
    from ..models import SourceCache
    row = db.get(SourceCache, document_id)
    if row is None:
        row = SourceCache(document_id=document_id, status="queued", requested_by=user_id)
        db.add(row)
    elif row.status in ("failed", "unavailable") and (dt.datetime.now(dt.timezone.utc) - row.requested_at) > \
            dt.timedelta(minutes=10):
        row.status, row.detail, row.requested_at = "queued", None, dt.datetime.now(dt.timezone.utc)
    db.flush()
    return cache_payload(row)


def cache_payload(row) -> dict:
    return {"document_id": row.document_id, "status": row.status, "pages": row.pages, "file_kind": row.file_kind,
            "detail": row.detail, "blob_sha256": row.blob_sha256 if row.status == "ready" else None}


def process_queue(db, store: SourceStore, limit: int = 3) -> int:
    from sqlalchemy import select

    from ..models import SourceCache
    n = 0
    for _ in range(limit):
        row = db.scalar(select(SourceCache).where(SourceCache.status == "queued")
                        .order_by(SourceCache.requested_at).limit(1).with_for_update(skip_locked=True))
        if row is None:
            db.rollback()  # end the polling transaction (never sit "idle in transaction" holding locks)
            break
        row.status = "preparing"
        db.commit()
        try:
            res = store.prepare(row.document_id)
        except Exception as ex:  # noqa: BLE001
            log.exception("preparing %s failed", row.document_id)
            res = {"status": "failed", "detail": f"{type(ex).__name__}: {ex}"[:500]}
        row = db.get(SourceCache, row.document_id)
        row.status = res["status"]
        row.pages, row.file_kind = res.get("pages"), res.get("file_kind")
        row.blob_sha256, row.detail = res.get("blob_sha256"), res.get("detail")
        if row.status == "ready":
            row.ready_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        n += 1
    return n


@lru_cache(maxsize=1)
def default_store():
    from ..config import get_settings
    s = get_settings()
    return SourceStore(s.source_root, s.upstream_root)
