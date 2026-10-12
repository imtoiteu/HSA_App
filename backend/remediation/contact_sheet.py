"""Contact sheets for visual review: per question, the stem excerpt and its figures (as students see them)."""
import sys, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Question
from app.content.render import plain_text
from app.review.content import images

ids = Path(sys.argv[1]).read_text().split()
out = Path(sys.argv[2])
per = int(sys.argv[3]) if len(sys.argv) > 3 else 8
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
db = SessionLocal()
W, TILE_H = 1300, 330
for page in range(0, len(ids), per):
    chunk = ids[page:page + per]
    sheet = Image.new("RGB", (W, TILE_H * len(chunk)), "white")
    d = ImageDraw.Draw(sheet)
    for i, ext in enumerate(chunk):
        q = db.scalar(select(Question).where(Question.external_id == ext))
        c = q.current_version.content
        y0 = i * TILE_H
        d.rectangle([0, y0, W - 1, y0 + TILE_H - 1], outline="#999")
        d.text((8, y0 + 6), f"[{page + i + 1}] {ext} · {q.subject_code} · key {q.current_version.answer['labels']}", fill="#c00", font=font)
        txt = plain_text(c["stem"])[:420].replace("\n", " ")
        for k, line in enumerate(textwrap.wrap(txt, 70)[:9]):
            d.text((8, y0 + 30 + k * 19), line, fill="black", font=font)
        opts = " | ".join(o["label"] + ") " + plain_text(o["content"])[:40] for o in c["options"])
        for k, line in enumerate(textwrap.wrap(opts, 70)[:4]):
            d.text((8, y0 + 210 + k * 19), line, fill="#333", font=font)
        x = 660
        for src in images(c)[:3]:
            try:
                raw = Image.open(f"/data/media/{src}").convert("RGBA")
                im = Image.new("RGB", raw.size, "white"); im.paste(raw, mask=raw.split()[3])
            except Exception:
                d.text((x, y0 + 30), "MISSING " + src[:20], fill="red", font=font)
                continue
            k = min((TILE_H - 20) / im.height, 300 / im.width, 3.0)  # upscale small figures ×3 at most
            im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))))
            sheet.paste(im, (x, y0 + 10))
            x += im.width + 10
            if x > W - 50:
                break
    sheet.save(out / f"sheet_{page // per + 1:02d}.png")
print("sheets:", (len(ids) + per - 1) // per)
