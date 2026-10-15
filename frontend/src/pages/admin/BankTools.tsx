import { useState } from "react";
import { Empty, ErrorBox, Modal, Spinner, useAsync, useToast } from "../../components/ui";
import { get, post, put } from "../../lib/api";
import { dateTime, num } from "../../lib/format";
import { errMsg } from "./common";

export interface BankFull {
  id: number; code: string; name: string; source_kind: string; description: string | null; version: string | null;
  source_uri: string | null; settings: Record<string, unknown>; is_active: boolean; created_at: string;
  last_synced_at: string | null; questions: number; served: number; served_by_subject: Record<string, number>;
  deferred_documents: number; compatible_blueprints: { id: number; code: string; name: string }[];
}

// ------------------------------------------------------------------------------------------ bank editor
export function BankEditor({ bank, onClose, onSaved }: { bank: BankFull | null; onClose: () => void; onSaved: () => void }) {
  const isNew = !bank;
  const [f, setF] = useState({
    code: bank?.code || "", name: bank?.name || "", description: bank?.description || "", version: bank?.version || "",
    is_active: bank?.is_active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const save = async () => {
    setBusy(true);
    try {
      if (isNew) await post("/api/admin/banks", { ...f, description: f.description || null, version: f.version || null, source_kind: "manual" });
      else await put(`/api/admin/banks/${bank!.code}`, { name: f.name, description: f.description || null,
        version: f.version || null, is_active: f.is_active, settings: bank!.settings || {} });
      toast("Đã lưu ngân hàng.", "ok");
      onSaved();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  return (
    <Modal title={isNew ? "Tạo ngân hàng câu hỏi" : `Ngân hàng ${bank!.code}`} onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Huỷ</button>
      <button className="btn" onClick={save} disabled={busy || !f.name || (isNew && !f.code)}>Lưu</button>
    </>}>
      <div className="stack">
        {isNew && <div className="field"><label htmlFor="b-code">Mã (chữ thường, số, gạch dưới)</label>
          <input id="b-code" className="input" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></div>}
        <div className="field"><label htmlFor="b-name">Tên</label>
          <input id="b-name" className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></div>
        <div className="field"><label htmlFor="b-desc">Mô tả</label>
          <textarea id="b-desc" className="input" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
        <div className="field"><label htmlFor="b-ver">Phiên bản</label>
          <input id="b-ver" className="input" value={f.version} disabled={bank?.source_kind === "hsa_upstream"}
                 onChange={(e) => setF({ ...f, version: e.target.value })} />
          {bank?.source_kind === "hsa_upstream" && <span className="hint">Phiên bản của ngân hàng HSA là commit nguồn, cập nhật khi đồng bộ.</span>}</div>
        {!isNew && <label className="check"><input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
          Đang hoạt động (bỏ chọn: không phục vụ câu nào của ngân hàng này)</label>}
      </div>
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------ deferred documents
export function DeferredDocs({ code, onClose }: { code: string; onClose: () => void }) {
  const d = useAsync(() => get<{ items: { external_id: string; path: string | null; status: string; pages: number | null }[] }>(
    `/api/admin/banks/${code}/documents`), [code]);
  return (
    <Modal title="Tài liệu nguồn chưa trích xuất được câu hỏi" wide onClose={onClose}>
      <p className="muted small">Các tệp PDF dạng ảnh quét, không có lớp chữ. Ngân hàng nguồn hoãn lại để OCR có nhận dạng công thức
        (NEEDS_MATH_AWARE_OCR). Không có câu hỏi nào từ các tệp này được phục vụ.</p>
      {d.loading ? <Spinner /> : d.error ? <ErrorBox error={d.error} /> : !d.data?.items.length ? <Empty title="Không có." /> : (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Tệp</th><th>Số trang</th><th>Trạng thái</th></tr></thead>
          <tbody>{d.data.items.map((x) => (
            <tr key={x.external_id}><td className="small">{x.path || x.external_id}</td><td>{x.pages ?? "—"}</td>
              <td><span className="badge warn">{x.status}</span></td></tr>
          ))}</tbody>
        </table></div>
      )}
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------ reconciliation
interface AuditReport {
  generated_at: string; bank: string; reconciliation: Record<string, unknown>; requested_by?: string;
  app: { effective_subject: Record<string, number>; subject_without_inference: Record<string, number>; served_by_subject: Record<string, number>;
    excluded_by_primary_cause: Record<string, number>; subject_source: Record<string, number> };
}

export function AuditPanel() {
  const a = useAsync(() => get<{ report: AuditReport | null; queued: boolean }>("/api/admin/audit/question-bank"), []);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const queue = async (deep: boolean) => {
    setBusy(true);
    try {
      await post(`/api/admin/audit/question-bank${deep ? "?deep=true" : ""}`);
      toast("Đã xếp lịch kiểm tra đối soát — bộ lập lịch sẽ chạy trong vài phút.", "ok"); a.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const r = a.data?.report;
  const subj = r ? Object.keys({ ...r.app.subject_without_inference, ...r.app.effective_subject }) : [];
  return (
    <div className="card">
      <div className="row between">
        <h3 style={{ margin: 0 }}>Đối soát ngân hàng câu hỏi</h3>
        <div className="row">
          {a.data?.queued && <span className="badge warn">đang chờ chạy</span>}
          <button className="btn secondary sm" disabled={busy} onClick={() => queue(false)}>Kiểm tra nhanh</button>
          <button className="btn secondary sm" disabled={busy} onClick={() => queue(true)}>Kiểm tra sâu (so nội dung)</button>
        </div>
      </div>
      {a.loading ? <Spinner /> : a.error ? <ErrorBox error={a.error} /> : !r || !r.reconciliation ? (
        <Empty title="Chưa có báo cáo đối soát." />
      ) : (
        <>
          <p className="muted small mt">Báo cáo lúc {dateTime(r.generated_at)}{r.requested_by ? ` · yêu cầu bởi ${r.requested_by}` : ""}</p>
          <div className="table-wrap"><table className="data">
            <tbody>{Object.entries(r.reconciliation).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td className="mono">{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v ?? "—")}</td></tr>
            ))}</tbody>
          </table></div>
          <h4 className="mt">Môn học: trước / sau khi áp dụng phân loại lần 2</h4>
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Môn</th><th>Chỉ theo môn gốc</th><th>Hiệu lực (có suy luận)</th><th>Đang phục vụ</th></tr></thead>
            <tbody>{subj.map((s) => (
              <tr key={s}><td>{s}</td><td>{num(r.app.subject_without_inference[s] || 0, 0)}</td>
                <td>{num(r.app.effective_subject[s] || 0, 0)}</td><td>{num(r.app.served_by_subject[s] || 0, 0)}</td></tr>
            ))}</tbody>
          </table></div>
          <h4 className="mt">Không phục vụ theo nguyên nhân chính</h4>
          <div className="chip-group">{Object.entries(r.app.excluded_by_primary_cause).map(([k, v]) => (
            <span key={k} className="badge">{k}: {num(v, 0)}</span>))}</div>
        </>
      )}
    </div>
  );
}
