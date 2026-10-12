// Image dialog of the Review Studio: upload / paste / drag-drop, crop, rotate, resize, alt text and caption.
// Editing happens on a canvas in the browser; only the final PNG is uploaded (re-encoded by the server).
import { useCallback, useEffect, useRef, useState } from "react";
import { Modal } from "../../../components/ui";
import { api } from "../../../lib/api";
import { errMsg } from "../common";

export interface ImageResult { src: string; w: number; h: number; alt?: string; caption?: string; inline?: boolean }

type Rect = { x: number; y: number; w: number; h: number };

export async function uploadBlob(blob: Blob, name = "image.png"): Promise<{ src: string; w: number; h: number }> {
  const fd = new FormData();
  fd.append("file", blob, name);
  return api("/api/review/assets", { method: "POST", body: fd });
}

export default function ImageTool({ initial, initialFile, onDone, onClose }: {
  initial?: { src: string; alt?: string; caption?: string }; initialFile?: File; onDone: (r: ImageResult) => void;
  onClose: () => void;
}) {
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [rot, setRot] = useState(0);
  const [crop, setCrop] = useState<Rect | null>(null);
  const [scale, setScale] = useState(100);
  const [alt, setAlt] = useState(initial?.alt || "");
  const [caption, setCaption] = useState(initial?.caption || "");
  const [inline, setInline] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback((url: string) => {
    const im = new Image();
    im.onload = () => { setImg(im); setRot(0); setCrop(null); setScale(100); };
    im.onerror = () => setErr("Không đọc được ảnh.");
    im.src = url;
  }, []);
  const loadFile = useCallback((f: File | Blob | null | undefined) => {
    if (!f) return;
    if (!/^image\/(png|jpe?g|gif|webp|bmp)$/.test(f.type)) { setErr("Chỉ nhận ảnh PNG, JPG, GIF, WEBP, BMP."); return; }
    setErr(null);
    load(URL.createObjectURL(f));
  }, [load]);

  useEffect(() => { if (initial?.src) load("/media/" + initial.src); }, [initial?.src, load]);
  useEffect(() => { if (initialFile) loadFile(initialFile); }, [initialFile, loadFile]);
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const f = [...(e.clipboardData?.files || [])].find((x) => x.type.startsWith("image/"));
      if (f) { e.preventDefault(); loadFile(f); }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [loadFile]);

  // the rotated source drawn at display size; crop rectangle in display coordinates
  const rotated = useCallback((): HTMLCanvasElement | null => {
    if (!img) return null;
    const c = document.createElement("canvas");
    const sw = img.naturalWidth, sh = img.naturalHeight;
    const q = ((rot % 360) + 360) % 360;
    c.width = q % 180 ? sh : sw;
    c.height = q % 180 ? sw : sh;
    const ctx = c.getContext("2d")!;
    ctx.translate(c.width / 2, c.height / 2);
    ctx.rotate((q * Math.PI) / 180);
    ctx.drawImage(img, -sw / 2, -sh / 2);
    return c;
  }, [img, rot]);

  useEffect(() => {
    const src = rotated();
    const cv = canvas.current;
    if (!src || !cv) return;
    const maxW = 640, maxH = 420;
    const k = Math.min(1, maxW / src.width, maxH / src.height);
    cv.width = Math.round(src.width * k);
    cv.height = Math.round(src.height * k);
    const ctx = cv.getContext("2d")!;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.drawImage(src, 0, 0, cv.width, cv.height);
    if (crop) {
      ctx.fillStyle = "rgba(15,23,42,.45)";
      ctx.fillRect(0, 0, cv.width, crop.y);
      ctx.fillRect(0, crop.y + crop.h, cv.width, cv.height - crop.y - crop.h);
      ctx.fillRect(0, crop.y, crop.x, crop.h);
      ctx.fillRect(crop.x + crop.w, crop.y, cv.width - crop.x - crop.w, crop.h);
      ctx.strokeStyle = "#1f5eff";
      ctx.lineWidth = 2;
      ctx.strokeRect(crop.x, crop.y, crop.w, crop.h);
    }
  }, [rotated, crop]);

  const pos = (e: React.PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(r.width, e.clientX - r.left)), y: Math.max(0, Math.min(r.height, e.clientY - r.top)) };
  };

  const final = (): HTMLCanvasElement | null => {
    const src = rotated();
    const cv = canvas.current;
    if (!src || !cv) return null;
    const k = src.width / cv.width;
    const box = crop && crop.w > 3 && crop.h > 3 ? { x: crop.x * k, y: crop.y * k, w: crop.w * k, h: crop.h * k }
      : { x: 0, y: 0, w: src.width, h: src.height };
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round((box.w * scale) / 100));
    out.height = Math.max(1, Math.round((box.h * scale) / 100));
    const ctx = out.getContext("2d")!;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(src, box.x, box.y, box.w, box.h, 0, 0, out.width, out.height);
    return out;
  };

  const save = async () => {
    const out = final();
    if (!out) return;
    setBusy(true);
    setErr(null);
    try {
      const blob = await new Promise<Blob | null>((r) => out.toBlob(r, "image/png"));
      if (!blob) throw new Error("Không tạo được ảnh.");
      const unchanged = initial?.src && !crop && rot === 0 && scale === 100;
      const res = unchanged ? { src: initial!.src, w: out.width, h: out.height } : await uploadBlob(blob);
      onDone({ ...res, alt: alt.trim() || undefined, caption: caption.trim() || undefined, inline });
    } catch (e) {
      setErr(errMsg(e));
      setBusy(false);
    }
  };

  const dims = (() => { const o = img ? final() : null; return o ? `${o.width}×${o.height}px` : ""; })();

  return (
    <Modal title={initial ? "Sửa hình" : "Thêm hình"} wide onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose} disabled={busy}>Huỷ</button>
      <button className="btn" onClick={save} disabled={!img || busy}>{busy ? "Đang tải lên…" : "Chèn hình"}</button>
    </>}>
      {!img ? (
        <div className="dropzone" onDragOver={(e) => e.preventDefault()}
             onDrop={(e) => { e.preventDefault(); loadFile(e.dataTransfer.files[0]); }}
             onClick={() => fileInput.current?.click()} role="button" tabIndex={0}>
          <div style={{ fontSize: 32 }} aria-hidden>🖼️</div>
          <div><strong>Kéo thả ảnh vào đây</strong>, dán từ clipboard (Ctrl+V) hoặc bấm để chọn tệp.</div>
          <div className="small muted">PNG, JPG, GIF, WEBP · tối đa 8 MB. Để lấy hình từ tài liệu gốc, dùng “Cắt hình từ nguồn” ở khung bên trái.</div>
          <input ref={fileInput} type="file" accept="image/*" hidden onChange={(e) => loadFile(e.target.files?.[0])} />
        </div>
      ) : (
        <div className="img-tool">
          <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
            <button className="btn secondary sm" onClick={() => setRot((r) => r - 90)}>⟲ Xoay trái</button>
            <button className="btn secondary sm" onClick={() => setRot((r) => r + 90)}>⟳ Xoay phải</button>
            <button className="btn secondary sm" onClick={() => setCrop(null)} disabled={!crop}>Bỏ vùng cắt</button>
            <button className="btn ghost sm" onClick={() => { setImg(null); setCrop(null); }}>Chọn ảnh khác</button>
            <span className="small muted">Kéo trên ảnh để chọn vùng cắt · {dims}</span>
          </div>
          <canvas ref={canvas} className="img-canvas"
                  onPointerDown={(e) => { (e.target as Element).setPointerCapture(e.pointerId); drag.current = pos(e); setCrop(null); }}
                  onPointerMove={(e) => {
                    if (!drag.current) return;
                    const p = pos(e), s = drag.current;
                    setCrop({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) });
                  }}
                  onPointerUp={() => { drag.current = null; }} />
          <div className="grid cols-3 mt">
            <div className="field"><label htmlFor="img-scale">Kích thước ({scale}%)</label>
              <input id="img-scale" type="range" min={10} max={200} step={5} value={scale} onChange={(e) => setScale(Number(e.target.value))} /></div>
            <div className="field"><label htmlFor="img-alt">Mô tả (alt)</label>
              <input id="img-alt" className="input" value={alt} onChange={(e) => setAlt(e.target.value)} placeholder="vd: Đồ thị hàm số" /></div>
            <div className="field"><label htmlFor="img-cap">Chú thích</label>
              <input id="img-cap" className="input" value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="(không bắt buộc)" /></div>
          </div>
          <label className="small"><input type="checkbox" checked={inline} onChange={(e) => setInline(e.target.checked)} /> Chèn trong dòng (hình nhỏ cạnh chữ)</label>
        </div>
      )}
      {err && <div className="alert error mt">{err}</div>}
    </Modal>
  );
}
