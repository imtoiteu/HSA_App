"""Content-addressed media store: <root>/<sha[:2]>/<sha>.<ext>.

Files are hard-linked from the source when on the same filesystem (zero extra space; the upstream
store is content-addressed and never rewritten in place), otherwise copied. Vector formats that
browsers cannot display (WMF/EMF) are converted to PNG; the PNG is its own asset with
`derived_from` pointing at the original.
"""
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
        "svg": "image/svg+xml"}
WEB_EXT = set(MIME)
NORMALIZE_EXT = {"jpeg": "jpg", "jfif": "jpg"}


@dataclass
class StoredFile:
    sha256: str
    ext: str
    bytes: int
    width: int | None
    height: int | None
    derived_from: str | None = None

    @property
    def rel_path(self):
        return f"{self.sha256[:2]}/{self.sha256}.{self.ext}"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def image_size(p: Path):
    try:
        from PIL import Image
        with Image.open(p) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


def sniff_ext(p: Path) -> str | None:
    with open(p, "rb") as f:
        head = f.read(16)
    if head.startswith(b"\x89PNG"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"GIF8":
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"\xd7\xcd\xc6\x9a" or head[:2] in (b"\x01\x00", b"\x02\x00"):
        return "wmf"
    if head[:4] == b"\x01\x00\x00\x00" and b" EMF" in open(p, "rb").read(64):
        return "emf"
    if head.lstrip().startswith(b"<svg") or head.lstrip().startswith(b"<?xml"):
        return "svg"
    return None


class MediaStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_of(self, sha: str, ext: str) -> Path:
        return self.root / sha[:2] / f"{sha}.{ext}"

    def put(self, src: Path, ext: str | None = None, sha: str | None = None) -> StoredFile:
        src = Path(src)
        ext = (ext or src.suffix.lstrip(".")).lower()
        ext = NORMALIZE_EXT.get(ext, ext)
        sha = sha or sha256_file(src)
        dst = self.path_of(sha, ext)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
            try:
                os.link(src, tmp)
            except OSError:
                shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o644)
            os.replace(tmp, dst)
        wh = image_size(dst) if ext in WEB_EXT and ext != "svg" else None
        return StoredFile(sha, ext, dst.stat().st_size, wh[0] if wh else None, wh[1] if wh else None)

    def put_bytes(self, data: bytes, ext: str, derived_from: str | None = None) -> StoredFile:
        sha = hashlib.sha256(data).hexdigest()
        dst = self.path_of(sha, ext)
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + f".tmp{os.getpid()}")
            tmp.write_bytes(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, dst)
        wh = image_size(dst)
        return StoredFile(sha, ext, len(data), wh[0] if wh else None, wh[1] if wh else None, derived_from)


def convert_vector_to_png(src: Path, density: int = 150) -> bytes | None:
    """WMF/EMF → PNG with ImageMagick (libwmf). Runs under `nice`; returns None on failure."""
    with tempfile.TemporaryDirectory(prefix="hsa_vec_") as d:
        out = Path(d) / "out.png"
        cmd = ["nice", "-n", "15", "convert", "-density", str(density), f"{src}[0]", "-background", "white",
               "-flatten", "-trim", "+repage", "-bordercolor", "white", "-border", "6", str(out)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as ex:
            log.warning("vector conversion failed for %s: %s", src, ex)
            return None
        return out.read_bytes() if out.exists() and out.stat().st_size > 0 else None


def audit_image(path: Path):
    """Port of upstream editorial.audit_image: returns (issues, info)."""
    from PIL import Image
    issues, info = [], {}
    try:
        im = Image.open(path)
        im.load()
    except Exception as ex:  # noqa: BLE001
        return ["unreadable"], {"error": str(ex)}
    w, h = im.size
    info["size"] = [w, h]
    g = im.convert("L")
    small = g.copy()
    small.thumbnail((300, 300))
    px = list(small.getdata())
    n = len(px) or 1
    dark = sum(1 for v in px if v < 150) / n
    faint = sum(1 for v in px if 150 <= v < 245) / n
    if dark < 0.002 and faint > 0.005:
        issues.append("watermark_like")
    if dark < 0.0005 and faint < 0.005:
        issues.append("mostly_blank")
    if w < 60 and h < 60:
        issues.append("tiny_glyph_image")
    elif max(w, h) < 250:
        issues.append("low_resolution")
    bbox = g.point(lambda v: 255 if v < 235 else 0).getbbox()
    if bbox:
        content = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if content / (w * h) < 0.35:
            issues.append("excessive_whitespace")
            info["bbox"] = bbox
    if h > 1400 and 1.25 < h / max(1, w) < 1.6:
        issues.append("page_screenshot")
    return issues, info


def trimmed_png_bytes(path: Path, bbox, border: int = 6) -> bytes | None:
    from io import BytesIO

    from PIL import Image, ImageOps
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            cropped = bg.convert("RGB").crop(bbox)
            cropped = ImageOps.expand(cropped, border=border, fill="white")
            buf = BytesIO()
            cropped.save(buf, "PNG", optimize=True)
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
