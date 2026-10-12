// Read-only source viewer: one page image at a time (rendered and cached by the server on demand), zoom, pan,
// page navigation, lazy thumbnails and "cut a figure from the source".
import { useCallback, useEffect, useRef, useState } from "react";
import { api, get } from "../../../lib/api";
import { copyText, errMsg } from "../common";
import type { SourceInfo } from "./rtypes";

type Rect = { x: number; y: number; w: number; h: number };

export default function SourceViewer({ qid, info, stemText, onCrop, canEdit }: {
  qid: number; info: SourceInfo | null; stemText: string; canEdit: boolean;
  onCrop: (r: { src: string; w: number; h: number; source_ref: Record<string, unknown> }) => void;
}) {
  const [docIdx, setDocIdx] = useState(0);
  const docs = info?.documents || [];
  const doc = docs[docIdx];
  const [cache, setCache] = useState(doc?.cache || null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [estimated, setEstimated] = useState<string | null>(null);
  const [cropMode, setCropMode] = useState(false);
  const [rect, setRect] = useState<Rect | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const drag = useRef<{ x: number; y: number; sl?: number; st?: number } | null>(null);

  const occ = info?.occurrences.find((o) => o.document_id === doc?.document_id);

  useEffect(() => { setDocIdx(0); }, [qid]);
  useEffect(() => {
    setCache(doc?.cache || null);
    setRect(null);
    setCropMode(false);
    setEstimated(null);
    const first = occ?.pages?.[0];
    setPage(first || 1);
  }, [doc?.document_id, qid]); // eslint-disable-line react-hooks/exhaustive-deps

  // prepare + poll until the worker has the document ready
  useEffect(() => {
    if (!doc) return;
    let alive = true;
    let t: number | undefined;
    const poll = async () => {
      try {
        const st = cache && cache.status !== "none" ? await get<typeof cache>(`/api/review/sources/${doc.document_id}`)
          : await api<typeof cache>(`/api/review/sources/${doc.document_id}/prepare`, { method: "POST", body: {} });
        if (!alive) return;
        setCache(st);
        if (st && (st.status === "queued" || st.status === "preparing")) t = window.setTimeout(poll, 3000);
      } catch (e) {
        if (alive) setMsg(errMsg(e));
      }
    };
    if (!cache || cache.status === "queued" || cache.status === "preparing" || cache.status === "none") poll();
    return () => { alive = false; window.clearTimeout(t); };
  }, [doc?.document_id, cache?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  // no page metadata (DOCX/DOC sources): estimate the page from the question text
  useEffect(() => {
    if (!doc || cache?.status !== "ready" || occ?.pages?.length || !stemText.trim()) return;
    get<{ pages: { page: number; score: number }[] }>(`/api/review/sources/${doc.document_id}/locate?q=${encodeURIComponent(stemText.slice(0, 400))}`)
      .then((r) => {
        if (r.pages.length) { setPage(r.pages[0].page); setEstimated(`trang ước tính theo nội dung (khớp ${Math.round(r.pages[0].score * 100)}%)`); }
        else setEstimated("không định vị được trang tự động");
      }).catch(() => {});
  }, [doc?.document_id, cache?.status, occ?.pages?.length, stemText]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setLoaded(false); }, [page, doc?.document_id]);

  const pages = cache?.pages || 0;
  const go = useCallback((p: number) => { if (pages) setPage(Math.max(1, Math.min(pages, p))); setRect(null); }, [pages]);
  const url = (p: number, dpi: number) => `/api/review/sources/${doc!.document_id}/page/${p}?dpi=${dpi}`;
  const ref = doc ? `${doc.path || doc.document_id} · trang ${page}${occ?.source_question_number ? ` · câu ${occ.source_question_number}` : ""} · ${doc.document_id}` : "";

  const rel = (e: React.PointerEvent) => {
    const r = imgRef.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };
  const doCrop = async () => {
    if (!rect || !doc) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api(`/api/review/questions/${qid}/crop`, { method: "POST", body: { document_id: doc.document_id, page, ...rect } });
      onCrop(r);
      setRect(null);
      setCropMode(false);
      setMsg("Đã tạo hình từ nguồn và chèn vào ô đang soạn.");
    } catch (e) {
      setMsg(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  if (!info) return <div className="src-pane"><div className="muted small">Đang tải nguồn…</div></div>;
  const prov = info.provenance;
  return (
    <div className="src-pane">
      <div className="src-head">
        <div className="src-title">
          <strong>Nguồn</strong>
          {!info.available && <span className="badge bad" title="Không tìm thấy tài liệu nguồn trong ngân hàng">SOURCE_UNAVAILABLE</span>}
          {docs.length > 1 && (
            <select className="input sm" value={docIdx} onChange={(e) => setDocIdx(Number(e.target.value))} aria-label="Chọn tài liệu nguồn">
              {docs.map((d, i) => <option key={d.document_id} value={i}>{i === 0 ? "★ " : ""}{(d.path || d.document_id).split("▸").pop()?.trim().slice(-60)}</option>)}
            </select>
          )}
        </div>
        <dl className="src-meta small">
          <dt>Tài liệu</dt><dd title={doc?.path || prov.document_path || ""}>{(doc?.path || prov.document_path || "—").split("▸").pop()?.trim()}</dd>
          <dt>Trang</dt><dd>{occ?.pages?.length ? occ.pages.join(", ") : estimated || "—"}</dd>
          <dt>Câu số</dt><dd>{occ?.source_question_number ?? prov.source_question_number ?? "—"}{occ?.section ? ` · ${occ.section}` : ""}</dd>
          <dt>Trích xuất</dt><dd>{occ?.extraction_method || doc?.extraction_method || prov.extraction_method || "—"}{doc?.file_kind ? ` · ${doc.file_kind}` : ""}</dd>
          <dt>Mã nguồn</dt><dd className="mono" title={doc?.blob_sha256 || ""}>{doc?.document_id || prov.document_id || "—"}{doc?.blob_sha256 ? ` · sha ${doc.blob_sha256.slice(0, 12)}…` : ""}</dd>
        </dl>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <button className="btn ghost sm" onClick={() => copyText(ref)} disabled={!doc}>Sao chép tham chiếu</button>
          {doc && cache?.status === "ready" && <a className="btn ghost sm" href={url(page, 160)} target="_blank" rel="noreferrer">Mở trang nguồn</a>}
          {occ?.pages?.length ? <button className="btn ghost sm" onClick={() => go(occ.pages[0])}>Về trang của câu</button> : null}
        </div>
      </div>

      {!doc ? (
        <div className="alert warn">Câu hỏi không có thông tin tài liệu nguồn (SOURCE_UNAVAILABLE). Không có xem trước nguồn; không suy đoán nguồn.</div>
      ) : cache?.status === "ready" ? (
        <>
          <div className="src-nav">
            <button className="btn secondary sm" onClick={() => go(page - 1)} disabled={page <= 1} aria-label="Trang trước">‹</button>
            <input className="input sm page-input" type="number" min={1} max={pages} value={page} aria-label="Trang"
                   onChange={(e) => go(Number(e.target.value) || 1)} />
            <span className="small muted">/ {pages}</span>
            <button className="btn secondary sm" onClick={() => go(page + 1)} disabled={page >= pages} aria-label="Trang sau">›</button>
            <span className="tb-sep" />
            <button className="btn secondary sm" onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} aria-label="Thu nhỏ">−</button>
            <span className="small">{Math.round(zoom * 100)}%</span>
            <button className="btn secondary sm" onClick={() => setZoom((z) => Math.min(3, z + 0.25))} aria-label="Phóng to">+</button>
            <button className="btn ghost sm" onClick={() => setZoom(1)}>Vừa khung</button>
            {canEdit && <button className={"btn sm " + (cropMode ? "" : "secondary")} onClick={() => { setCropMode((c) => !c); setRect(null); }}
                                title="Kéo chọn một vùng trên trang để tạo hình cho câu hỏi">✂ Cắt hình từ nguồn</button>}
          </div>
          {cropMode && <div className="small muted">Kéo chuột trên trang để chọn vùng, rồi bấm “Tạo hình”.
            {rect && <button className="btn sm ok" style={{ marginLeft: 8 }} onClick={doCrop} disabled={busy}>{busy ? "Đang cắt…" : "Tạo hình từ vùng chọn"}</button>}</div>}
          <div ref={box} className={"src-view" + (cropMode ? " crop" : "")}
               onPointerDown={(e) => {
                 if (cropMode) { if (!imgRef.current) return; (e.target as Element).setPointerCapture(e.pointerId); const p = rel(e); drag.current = p; setRect(null); return; }
                 drag.current = { x: e.clientX, y: e.clientY, sl: box.current!.scrollLeft, st: box.current!.scrollTop };
               }}
               onPointerMove={(e) => {
                 const d = drag.current;
                 if (!d) return;
                 if (cropMode) { const p = rel(e); setRect({ x: Math.min(d.x, p.x), y: Math.min(d.y, p.y), w: Math.abs(p.x - d.x), h: Math.abs(p.y - d.y) }); return; }
                 box.current!.scrollLeft = (d.sl || 0) - (e.clientX - d.x);
                 box.current!.scrollTop = (d.st || 0) - (e.clientY - d.y);
               }}
               onPointerUp={() => { drag.current = null; }}>
            <div className="src-page" style={{ width: `${zoom * 100}%` }}>
              {!loaded && <div className="src-loading">Đang hiển thị trang {page}…</div>}
              <img ref={imgRef} src={url(page, zoom > 1.4 ? 220 : 160)} alt={`Trang ${page} của tài liệu nguồn`} draggable={false}
                   onLoad={() => setLoaded(true)} onError={() => { setLoaded(true); setMsg("Không hiển thị được trang này."); }} />
              {rect && <div className="crop-rect" style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }} />}
            </div>
          </div>
          <div className="src-thumbs" aria-label="Các trang">
            {Array.from({ length: Math.min(pages, 41) }, (_, i) => Math.max(1, Math.min(pages - Math.min(pages, 41) + 1, page - 20)) + i).map((p) => (
              <button key={p} className={"thumb" + (p === page ? " on" : "") + (occ?.pages?.includes(p) ? " hit" : "")} onClick={() => go(p)} title={`Trang ${p}`}>
                <img src={url(p, 48)} loading="lazy" alt="" />
                <span>{p}</span>
              </button>
            ))}
          </div>
        </>
      ) : cache && (cache.status === "unavailable" || cache.status === "failed") ? (
        <div className="alert warn">SOURCE_UNAVAILABLE — {cache.detail || "không lấy được tài liệu gốc."} Thông tin nguồn ở trên vẫn là dữ liệu thật của ngân hàng.</div>
      ) : (
        <div className="src-loading big">Đang chuẩn bị tài liệu gốc (giải nén / chuyển đổi)… {cache?.status === "preparing" ? "đang xử lý" : "đang xếp hàng"}</div>
      )}
      {msg && <div className="small mt muted" role="status">{msg}</div>}
    </div>
  );
}
