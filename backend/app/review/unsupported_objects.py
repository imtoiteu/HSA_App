"""Remediation batch: resolve the "đối tượng không hiển thị được" placeholders of Word-sourced questions.

Upstream parsed the Word documents (docx_native, or .doc converted with LibreOffice) and wrote a
`{{unsupported:drawing_shape}}` token wherever a drawing had no picture it could keep. The app renders that
token as a warning placeholder; a question showing one is never served.

Every placeholder is traced back to its drawing in the original document: the normalised document
(upstream normalized/docs) lists, per body block, the pictures and unsupported drawings in document order, and
the same block of word/document.xml holds the drawings themselves. Each drawing is then decided by code:

* **watermark** (decoration, removed): a light picture (no dark ink) that the document repeats (≥3 times): the
  publisher's mark printed on every page, behind or over the text;
* **simple shape** (a single arrow, no text) → its Unicode character;
* **picture** (anything else carrying an image) → the picture itself, inserted where the placeholder was;
* **drawing** (lines, connectors, groups, text boxes) → rendered on its own with LibreOffice, cropped to its
  ink and inserted as an image;
* anything that cannot be matched to exactly one drawing, or cannot be rendered, stays and the question keeps
  waiting for a reviewer.

A question whose placeholders were all decoration or simple shapes goes through the usual answer/subject
checks and can be approved; a question that received images is saved as a draft for the Review Studio (the
visual dimension is a reviewer's decision). Nothing is decided by an LLM.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

PLACEHOLDER = "đối tượng không hiển thị được"
BATCH = "unsupported_objects"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
      "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
      "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
      "v": "urn:schemas-microsoft-com:vml",
      "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
W = "{%s}" % NS["w"]
R_EMBED = "{%s}embed" % NS["r"]
R_ID = "{%s}id" % NS["r"]
ARROWS = {"rightArrow": "→", "leftArrow": "←", "upArrow": "↑", "downArrow": "↓", "leftRightArrow": "↔",
          "upDownArrow": "↕"}


# ------------------------------------------------------------------------------------------------
# the drawings of a Word document
# ------------------------------------------------------------------------------------------------
@dataclass
class Drawing:
    block: int
    before: str                 # text of the block before / after the placeholder (normalised document)
    after: str
    behind: bool = False
    anchor: str = "inline"
    media: bytes | None = None  # the picture's bytes when the drawing is a picture
    media_ext: str = ""
    media_uses: int = 0         # how often the document places this picture
    prst: list = field(default_factory=list)
    group: bool = False
    text: str = ""
    xml: str = ""               # the drawing element (for rendering it on its own)
    cm: tuple | None = None
    doc: str = ""               # the document it belongs to


def drawings_in(el) -> list:
    """Drawings of a body element in document order (mc:AlternateContent → its Choice only)."""
    out = []

    def walk(e):
        tag = e.tag.split("}")[-1]
        if tag == "AlternateContent":
            ch = e.find("mc:Choice", NS)
            for x in (ch if ch is not None else []):
                walk(x)
            return
        if tag in ("drawing", "pict"):
            out.append(e)
            return
        for x in e:
            walk(x)
    walk(el)
    return out


def _flat(node, out):
    """img / unsupported runs and text of a normalised block, in document order (not into text boxes)."""
    if isinstance(node, list):
        for x in node:
            _flat(x, out)
    elif isinstance(node, dict):
        if "unsupported" in node:
            out.append(("U", node))
            return
        if "img" in node:
            out.append(("I", node))
            return
        if isinstance(node.get("t"), str):
            if not node.get("synthetic"):   # list numbering ("32.", "A.") is not part of the question's text
                out.append(("T", node["t"]))
            return
        for k, v in node.items():
            if k != "textbox" and isinstance(v, (list, dict)):
                _flat(v, out)


def read_document(docx: bytes, normalized: dict) -> tuple[list[Drawing], int]:
    """(drawings behind the unsupported placeholders, number of placeholders that could not be aligned)."""
    z = zipfile.ZipFile(io.BytesIO(docx))
    rels = ET.fromstring(z.read("word/_rels/document.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}

    def media(rid):
        t = target.get(rid)
        if not t:
            return None, ""
        name = t.lstrip("/")
        name = name if name.startswith("word/") else "word/" + name
        try:
            return z.read(name), Path(name).suffix.lstrip(".").lower()
        except KeyError:
            return None, ""

    def rid_of(d):
        bl = d.find(".//a:blip", NS)
        if bl is not None and bl.get(R_EMBED):
            return bl.get(R_EMBED)
        im = d.find(".//v:imagedata", NS)
        return im.get(R_ID) if im is not None else None

    body = list(ET.fromstring(z.read("word/document.xml")).find(W + "body"))
    uses: dict[str, int] = {}
    for el in body:
        for d in drawings_in(el):
            rid = rid_of(d)
            if rid:
                data, _ = media(rid)
                if data:
                    k = hashlib.sha256(data).hexdigest()
                    uses[k] = uses.get(k, 0) + 1
    out, unaligned = [], 0
    blocks = normalized.get("blocks") or []
    btext = []
    for b in blocks:
        f: list = []
        _flat(b, f)
        btext.append("".join(x[1] for x in f if x[0] == "T"))
    for bi, b in enumerate(blocks):
        if '"unsupported"' not in json.dumps(b):
            continue
        flat: list = []
        _flat(b, flat)
        prev_txt = "".join(btext[max(0, bi - 3):bi])[-60:]    # context continues into the neighbouring paragraphs
        next_txt = "".join(btext[bi + 1:bi + 4])[:60]
        i = b.get("i")
        ds = drawings_in(body[i]) if isinstance(i, int) and i < len(body) else []
        objs = [x for x in flat if x[0] in ("U", "I")]
        if len(objs) != len(ds):
            unaligned += sum(1 for x in flat if x[0] == "U")
            continue
        di = 0
        for k, (kind, _node) in enumerate(flat):
            if kind not in ("U", "I"):
                continue
            if kind == "U":
                d = ds[di]
                anc = d.find("wp:anchor", NS)
                dr = Drawing(block=i, before=(prev_txt + "".join(x[1] for x in flat[:k] if x[0] == "T"))[-60:],
                             after=("".join(x[1] for x in flat[k + 1:] if x[0] == "T") + next_txt)[:60],
                             behind=anc is not None and anc.get("behindDoc") == "1",
                             anchor="anchor" if anc is not None else ("inline" if d.find("wp:inline", NS) is not None
                                                                     else "vml"),
                             prst=[g.get("prst") for g in d.findall(".//a:prstGeom", NS)],
                             group=d.find(".//wpg:wgp", NS) is not None,
                             text=" ".join((t.text or "") for t in d.iter(W + "t")).strip(),
                             xml=ET.tostring(d, encoding="unicode"))
                ext = d.find(".//wp:extent", NS)
                if ext is not None:
                    dr.cm = (int(ext.get("cx")) / 360000, int(ext.get("cy")) / 360000)
                rid = rid_of(d)
                if rid:
                    data, e = media(rid)
                    if data:
                        dr.media, dr.media_ext = data, e
                        dr.media_uses = uses.get(hashlib.sha256(data).hexdigest(), 0)
                out.append(dr)
            di += 1
    return out, unaligned


# ------------------------------------------------------------------------------------------------
# decisions
# ------------------------------------------------------------------------------------------------
def light_image(data: bytes) -> bool:
    """No dark ink (a watermark is printed light/transparent)."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:  # noqa: BLE001
        return False
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, "white")
    bg.alpha_composite(im)
    g = bg.convert("L")
    g.thumbnail((400, 400))
    px = list(g.getdata())
    return sum(1 for p in px if p < 120) <= 0.002 * len(px)


@dataclass
class Decision:
    action: str                 # remove | text | image | keep
    reason: str
    text: str = ""
    image: bytes | None = None


def decide(d: Drawing, render=None) -> Decision:
    if d.media is not None:
        if d.media_uses >= 3 and light_image(d.media):
            return Decision("remove", f"hình mờ/nhãn phát hành (watermark) lặp {d.media_uses} lần"
                                      + (" sau chữ" if d.behind else ""))
        return Decision("image", "hình gốc trong tài liệu", image=d.media)
    if not d.group and len(d.prst) == 1 and d.prst[0] in ARROWS and not d.text:
        return Decision("text", f"hình mũi tên ({d.prst[0]})", text=ARROWS[d.prst[0]])
    if render is not None:
        png = render(d)
        if png:
            return Decision("image", "hình vẽ dựng lại từ tài liệu gốc (LibreOffice)", image=png)
    return Decision("keep", "không dựng lại được hình vẽ")


def render_drawing(docx: bytes, d: Drawing, workdir: Path) -> bytes | None:
    """The drawing alone on a page (copy of the document with its body replaced by the drawing's paragraph,
    no header/footer), rendered by LibreOffice and cropped to its ink."""
    from PIL import Image, ImageOps
    if not shutil.which("soffice"):
        return None
    src = zipfile.ZipFile(io.BytesIO(docx))
    root = ET.fromstring(src.read("word/document.xml"))
    body = root.find(W + "body")
    sect = body.find(W + "sectPr")
    for child in list(body):
        body.remove(child)
    p = ET.SubElement(body, W + "p")
    r = ET.SubElement(p, W + "r")
    r.append(ET.fromstring(d.xml))
    if sect is not None:
        for ref in list(sect):
            if ref.tag in (W + "headerReference", W + "footerReference"):
                sect.remove(ref)
        body.append(sect)
    for prefix, uri in NS.items():
        ET.register_namespace(prefix, uri)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmp = Path(tmp)
        out = tmp / "one.docx"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for item in src.infolist():
                z.writestr(item, xml if item.filename == "word/document.xml" else src.read(item.filename))
        try:
            subprocess.run(["soffice", "--headless", "--norestore", "--convert-to", "png", "--outdir", str(tmp),
                            str(out)], capture_output=True, timeout=180, env={"HOME": str(tmp), "PATH": "/usr/bin:/bin"})
        except subprocess.TimeoutExpired:
            return None
        png = tmp / "one.png"
        if not png.exists():
            return None
        im = Image.open(png).convert("RGB")
        bbox = ImageOps.invert(im.convert("L")).point(lambda v: 255 if v > 24 else 0).getbbox()
        if not bbox or (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 16:
            return None
        im = ImageOps.expand(im.crop(bbox), border=4, fill="white")
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue()


# ------------------------------------------------------------------------------------------------
# placeholders of a question's content → drawings
# ------------------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or "")).lower()


def _flat_nodes(blocks, out):
    """(block-list path, index) of every inline node in reading order, with its text."""
    for b in blocks or []:
        if isinstance(b, dict) and b.get("t") == "p":
            for n in b.get("c") or []:
                out.append(n)
        elif isinstance(b, dict) and b.get("t") == "warn":
            out.append(b)
        elif isinstance(b, dict) and b.get("t") == "list":
            for it in b.get("items") or []:
                _flat_nodes(it, out)
        elif isinstance(b, dict) and b.get("t") == "table":
            for row in b.get("rows") or []:
                for cell in row:
                    _flat_nodes(cell.get("c"), out)


def content_parts(content: dict) -> list[tuple[str, list]]:
    parts = [("stem", content.get("stem")), ("solution", content.get("solution")),
             ("explanation", content.get("explanation"))]
    parts += [(f"option:{o.get('label')}", o.get("content")) for o in content.get("options") or []]
    g = content.get("group") or {}
    parts += [("group.header", g.get("header")), ("group.passage", g.get("passage"))]
    return [(k, v) for k, v in parts if v]


def match(node_before: str, node_after: str, drawings: list[Drawing]) -> list[Drawing]:
    """Drawings of the documents whose surrounding text agrees with the placeholder's. A side the source block
    leaves empty (the drawing starts or ends its paragraph) cannot contradict; at least one side must agree on
    8 characters or more."""
    a, b = _norm(node_before)[-16:], _norm(node_after)[:16]
    out = []
    for d in drawings:
        da = _norm(d.before)[-16:]
        # the stored stem starts after the question's label: the source paragraph may still hold it
        for db in dict.fromkeys([_norm(d.after)[:16], LABEL.sub("", _norm(d.after), count=1)[:16]]):
            n_a = min(len(a), len(da))
            n_b = min(len(b), len(db))
            ok_a = n_a == 0 or a[-n_a:] == da[-n_a:]
            ok_b = n_b == 0 or b[:n_b] == db[:n_b]
            if ok_a and ok_b and max(n_a, n_b) >= 8:
                out.append(d)
                break
    return out


LABEL = re.compile(r"^(?:câu|question|bài)?\d{1,3}[.:)]")


def resolve(content: dict, drawings: list[Drawing], decide_fn, store_image) -> tuple[dict, list[str], list[str], int]:
    """New content with the placeholders replaced. Returns (content, actions, unresolved reasons, images)."""
    c = copy.deepcopy(content)
    actions, problems, images = [], [], 0
    for name, blocks in content_parts(c):
        nodes: list = []
        _flat_nodes(blocks, nodes)
        texts = ["".join(n.get("v", "") for n in nodes[:k] if n.get("t") == "s") for k in range(len(nodes) + 1)]
        for k, n in enumerate(nodes):
            if not (n.get("t") == "warn" and n.get("v") == PLACEHOLDER):
                continue
            before = texts[k][-60:]
            after = "".join(x.get("v", "") for x in nodes[k + 1:] if x.get("t") == "s")[:60]
            cands = match(before, after, drawings)
            if not cands:
                problems.append(f"{name}: không tìm thấy hình tương ứng trong tài liệu gốc")
                continue
            decs = [decide_fn(d) for d in cands]
            kinds = {(x.action, x.text, hashlib.sha256(x.image).hexdigest() if x.image else None) for x in decs}
            if len(kinds) != 1:
                problems.append(f"{name}: nhiều hình khớp ngữ cảnh nhưng khác nhau")
                continue
            dec = decs[0]
            if dec.action == "keep":
                problems.append(f"{name}: {dec.reason}")
                continue
            n.clear()
            if dec.action == "remove":
                n.update({"t": "s", "v": ""})
            elif dec.action == "text":
                n.update({"t": "s", "v": dec.text})
            else:
                src, w, h = store_image(dec.image)
                n.update({"t": "img", "src": src, "inline": True, "w": w, "h": h})
                images += 1
            actions.append(f"{name}: {dec.reason}")
        # a placeholder that was a whole block (t: warn block) becomes an empty paragraph: drop those
        for i in range(len(blocks) - 1, -1, -1):
            b = blocks[i]
            if b.get("t") == "s" and not b.get("v"):
                blocks.pop(i)
    _drop_empty(c)
    return c, actions, problems, images


def _drop_empty(c: dict):
    """Empty text runs left by removed placeholders, and paragraphs left empty."""
    def clean(blocks):
        out = []
        for b in blocks or []:
            if b.get("t") == "p":
                b["c"] = [n for n in b.get("c") or [] if not (n.get("t") == "s" and n.get("v") == "")]
                if not b["c"]:
                    continue
            elif b.get("t") == "s":   # a block-level placeholder replaced by text
                if not b.get("v"):
                    continue
                b = {"t": "p", "c": [b]}
            elif b.get("t") == "img" and b.get("inline"):
                b = {"t": "img", "src": b["src"], **({"w": b["w"]} if b.get("w") else {}),
                     **({"h": b["h"]} if b.get("h") else {})}
            elif b.get("t") == "list":
                b["items"] = [clean(it) for it in b.get("items") or []]
            elif b.get("t") == "table":
                for row in b.get("rows") or []:
                    for cell in row:
                        cell["c"] = clean(cell.get("c"))
            out.append(b)
        return out
    for k in ("stem", "solution", "explanation"):
        if c.get(k):
            c[k] = clean(c[k])
    for o in c.get("options") or []:
        o["content"] = clean(o.get("content"))
    g = c.get("group")
    if g:
        g["header"] = clean(g.get("header"))
        g["passage"] = clean(g.get("passage"))


def has_placeholder(content: dict) -> bool:
    return PLACEHOLDER in json.dumps(content, ensure_ascii=False)


# ------------------------------------------------------------------------------------------------
# the batch
# ------------------------------------------------------------------------------------------------
def candidates(db, limit=None, documents=None, retry=False, questions=None) -> list[int]:
    from sqlalchemy import text
    from .remediate import ACTOR_EMAIL
    sql = f"""SELECT q.id FROM question q JOIN question_review r ON r.question_id = q.id
              JOIN question_version v ON v.id = COALESCE(r.draft_version_id, q.current_version_id)
              WHERE q.removed_upstream = false AND q.review_gate IS NULL
                AND (r.draft_version_id IS NULL AND r.status = 'none'
                     OR r.status = 'draft' AND (SELECT u.email FROM question_version dv JOIN app_user u
                                                ON u.id = dv.created_by WHERE dv.id = r.draft_version_id) = '{ACTOR_EMAIL}')
                AND r.dirty = false
                AND v.content::text LIKE '%{PLACEHOLDER}%'"""
    if questions:
        sql += f" AND q.id IN ({','.join(str(int(x)) for x in questions)})"
    if not retry:
        sql += (f" AND NOT EXISTS (SELECT 1 FROM review_event e WHERE e.question_id = q.id"
                f" AND e.action IN ('remediation_skip', 'edit') AND e.note LIKE '[{BATCH}]%')")
    if documents:
        sql += " AND q.provenance->>'document_id' = ANY(:docs)"
    sql += " ORDER BY q.provenance->>'document_id', q.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return list(db.scalars(text(sql), {"docs": documents} if documents else {}))


def _document(store, document_id: str, workdir: Path) -> tuple[bytes | None, dict | None, str]:
    """(docx bytes, normalised document, why not)."""
    doc = store.up.document(document_id)
    if doc is None:
        return None, None, "document not in the bank"
    kind = (doc.get("file_kind") or "").lower()
    if kind not in ("docx", "doc"):
        return None, None, f"source is {kind or 'unknown'}, not a Word document"
    sha = doc["blob_sha256"]
    norm = store.upstream_root / "normalized" / "docs" / sha[:2] / f"{sha}.json.gz"
    if not norm.exists():
        return None, None, "normalised document missing"
    normalized = json.load(gzip.open(norm))
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        src = store._materialize(doc, Path(tmp))
        if src is None:
            return None, None, "original document not found in source-original"
        if kind == "doc":   # the same conversion upstream used before parsing
            out = Path(tmp) / "conv"
            out.mkdir()
            shutil.copyfile(src, out / "source.doc")
            subprocess.run(["soffice", "--headless", "--norestore", "--convert-to", "docx", "--outdir", str(out),
                            str(out / "source.doc")], capture_output=True, timeout=300,
                           env={"HOME": str(out), "PATH": "/usr/bin:/bin"})
            p = out / "source.docx"
            if not p.exists():
                return None, None, ".doc could not be converted"
            return p.read_bytes(), normalized, ""
        return Path(src).read_bytes(), normalized, ""


def run(db, limit=None, apply=False, media_root=None, documents=None, retry=False, questions=None,
        workdir: str | None = None, log=print) -> dict:
    from sqlalchemy import select

    from ..models import Asset, Question, ReviewEvent
    from ..sync.media import MediaStore
    from . import content as C
    from . import service as S
    from .remediate import METHOD, Verdict, actor, check_answer, check_subject, solution_contradicts
    from .sources import default_store

    store = default_store()
    who = actor(db)
    work = Path(workdir or tempfile.gettempdir())
    stats = {"examined": 0, "resolved": 0, "drafts": 0, "promoted": 0, "unresolved": 0, "removed": 0, "texts": 0,
             "images": 0, "reasons": {}}
    qids = candidates(db, limit, documents, retry, questions)
    docs: dict[str, tuple] = {}

    def doc_for(doc_id):
        if doc_id not in docs:
            docx, normalized, why = _document(store, doc_id, work)
            if docx is None:
                docs[doc_id] = (None, None, why)
            else:
                drawings, unaligned = read_document(docx, normalized)
                docs[doc_id] = (docx, drawings, "")
                log(f"  {doc_id}: {len(drawings)} drawings behind placeholders, {unaligned} not aligned")
        return docs[doc_id]

    def store_image(data: bytes):
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        bg.alpha_composite(im)
        im = bg.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        sf = MediaStore(media_root).put_bytes(buf.getvalue(), "png")
        if db.get(Asset, sf.sha256) is None:
            db.add(Asset(sha256=sf.sha256, ext=sf.ext, mime="image/png", bytes=sf.bytes, width=im.width,
                         height=im.height, source_ref=f"{BATCH}"[:80]))
            db.commit()   # an asset row is harmless on its own; never keep a transaction open across renders
        w, h = im.width, im.height
        if w > 640:   # displayed at most column width; keep the aspect ratio
            w, h = 640, int(h * 640 / w)
        return f"{sf.sha256[:2]}/{sf.sha256}.{sf.ext}", w, h

    renders: dict[int, bytes | None] = {}

    def render_cached(dd: Drawing):
        if id(dd) not in renders:
            renders[id(dd)] = render_drawing(docs[dd.doc][0], dd, work)
        return renders[id(dd)]

    for qid in qids:
        stats["examined"] += 1
        q = db.get(Question, qid)
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)

        def unresolved(reason, record=True):
            stats["unresolved"] += 1
            key = re.sub(r"\d+", "N", reason.split(":")[0])[:60]
            stats["reasons"][key] = stats["reasons"].get(key, 0) + 1
            if apply and record:
                db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id,
                                   action="remediation_skip", changes=[], note=f"[{BATCH}] {reason}"))
                db.commit()
            log(f"  SKIP {q.external_id}: {reason}")

        doc_id = (q.provenance or {}).get("document_id")
        base_content = copy.deepcopy(v.content)
        ext_id, group_key = q.external_id, q.group_key
        db.rollback()   # no transaction stays open while documents are extracted and drawings rendered
        # the question's text may come from several documents: its occurrences (a separate answer file for the
        # solution) and the document of its shared passage
        doc_ids = [doc_id] + [o["document_id"] for o in store.up.occurrences(ext_id)]
        if group_key:
            con = store.up._con()
            if con is not None:
                try:
                    doc_ids += [x[0] for x in con.execute("SELECT document_id FROM question_group WHERE group_id = ?",
                                                         (group_key,))]
                finally:
                    con.close()
        drawings, whys = [], []
        for d_id in dict.fromkeys(x for x in doc_ids if x):
            docx, ds, why = doc_for(d_id)
            if docx is None:
                whys.append(f"{d_id}: {why}")
                continue
            for dr in ds:
                dr.doc = d_id
            drawings += ds
        if not drawings:
            unresolved("; ".join(whys) or "no drawing found in the question's documents")
            continue

        def decide_fn(d: Drawing):
            return decide(d, lambda dd: render_cached(dd))

        new, actions, problems, images = resolve(base_content, drawings, decide_fn, store_image if apply else
                                                 (lambda data: ("00/" + "0" * 64 + ".png", 1, 1)))
        if problems:
            db.rollback()
            unresolved("; ".join(sorted(set(problems)))[:300])
            continue
        if has_placeholder(new):
            db.rollback()
            unresolved("placeholder left after the rebuild")
            continue
        stats["resolved"] += 1
        stats["removed"] += sum(1 for a in actions if "watermark" in a)
        stats["texts"] += sum(1 for a in actions if "mũi tên" in a)
        stats["images"] += images
        if not apply:
            log(f"  OK   {q.external_id}: {len(actions)} object(s), {images} image(s)")
            continue
        edit = {k: new.get(k) for k in ("stem", "solution", "explanation", "options", "group") if k in new}
        note = (f"[{BATCH}] thay {len(actions)} ô “{PLACEHOLDER}” theo tài liệu Word gốc ({doc_id}): "
                + "; ".join(sorted(set(actions)))[:600])
        try:
            S.save_edit(db, q, who, edit, note=note, source_ref={"document_id": doc_id, "batch": BATCH},
                        media_root=media_root)
        except S.ReviewError as ex:
            db.rollback()
            unresolved(f"draft refused: {ex}")
            continue
        db.commit()
        stats["drafts"] += 1
        if images:
            unresolved(f"objects resolved (draft in Review Studio): {images} image(s) inserted from the source need "
                       "a reviewer's visual check")
            continue
        # placeholders that were decoration or simple shapes only: the usual checks decide
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        dims = S.dim_view(q, r, v)["status"]
        if dims.get("content") != "ok":
            S.confirm(db, q, who, "content", "ok", f"[{BATCH}] các ô không hiển thị được là hình mờ trang trí hoặc "
                      f"mũi tên đơn, đã xử lý theo tài liệu gốc {doc_id}", media_root=media_root, method=METHOD)
        db.commit()
        r = S.get_review(db, q)
        v = S.working_version(db, q, r)
        dims = S.dim_view(q, r, v)["status"]
        av = Verdict(True, "đáp án đã được xác nhận") if dims.get("answer") == "ok" else \
            check_answer(q, v.answer, v.content, store.up)
        if not av.ok:
            unresolved(f"objects resolved (draft in Review Studio); answer: {av.reason}")
            continue
        contra = solution_contradicts(v.answer, v.content) if dims.get("answer") != "ok" else None
        if contra:
            unresolved(f"objects resolved (draft in Review Studio); {contra}")
            continue
        sj = check_subject(q, v.content) if dims.get("subject") != "ok" else Verdict(True, "môn đã được xác nhận")
        if not sj.ok:
            unresolved(f"objects resolved (draft in Review Studio); subject: {sj.reason}")
            continue
        req = C.required_dims(v.content, q.editorial_states or [])
        if req.get("formula") and dims.get("formula") not in ("ok", "na"):
            unresolved("objects resolved (draft in Review Studio); formulas still to be checked")
            continue
        if C.images(v.content) or C.has_table(v.content):
            unresolved("objects resolved (draft in Review Studio); the question has a figure/table to check")
            continue
        if dims.get("answer") != "ok":
            S.confirm(db, q, who, "answer", "ok", f"[{BATCH}] {av.reason}", media_root=media_root, method=METHOD)
        if dims.get("subject") != "ok":
            S.confirm(db, q, who, "subject", "ok", f"[{BATCH}] {q.subject_code}: {sj.reason}", media_root=media_root,
                      method=METHOD)
        if dims.get("visual") not in ("ok", "na"):
            S.confirm(db, q, who, "visual", "na", f"[{BATCH}] không có hình (chỉ có hình mờ trang trí, đã bỏ)",
                      media_root=media_root, method=METHOD)
        if dims.get("source") != "ok":
            S.confirm(db, q, who, "source", "ok", f"[{BATCH}] {doc_id}", media_root=media_root, method=METHOD)
        try:
            res = S.approve(db, q, who, f"[{BATCH}] tự động theo nguồn: bỏ hình mờ trang trí / thay mũi tên theo "
                                        "tài liệu Word gốc; đáp án khớp nguồn", media_root=media_root)
        except S.ReviewError as ex:
            db.rollback()
            unresolved(f"objects resolved (draft in Review Studio); approval refused: {ex}")
            continue
        db.add(ReviewEvent(question_id=q.id, external_id=q.external_id, actor_id=who.id, action="remediation",
                           changes=[], note=f"[{BATCH}] promoted"))
        db.commit()
        stats["promoted"] += res["gate"] == "ready"
        log(f"  READY {q.external_id} served={res['served']} reasons={res['reasons']}")
    return stats
