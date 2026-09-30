import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorBox, Modal, Spinner, useAsync, useToast } from "../../components/ui";
import { get, post, put } from "../../lib/api";
import { dateTime, num, TYPE_LABELS } from "../../lib/format";
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

// ------------------------------------------------------------------------------------------ manual question
const EMPTY_Q = { external_id: "", type: "single_choice", subject: "math", status: "READY_TO_SERVE", stem_md: "",
  options: "A. \nB. \nC. \nD. ", answer: "", solution_md: "" };

export function ManualQuestion({ code, onClose, onSaved }: { code: string; onClose: () => void; onSaved: () => void }) {
  const subjects = useAsync(() => get<{ items: { code: string; name: string }[] }>("/api/admin/subjects"), []);
  const [f, setF] = useState(EMPTY_Q);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ id: number; served: boolean; reasons: string[] } | null>(null);
  const toast = useToast();
  const choice = ["single_choice", "multiple_choice", "error_identification"].includes(f.type);
  const record = () => {
    const rec: Record<string, unknown> = { external_id: f.external_id.trim(), type: f.type, subject: f.subject, status: f.status,
      stem_md: f.stem_md, solution_md: f.solution_md || undefined };
    if (choice) {
      rec.options = f.options.split("\n").map((l) => l.match(/^\s*([A-H])[.)]\s*(.*)$/)).filter(Boolean)
        .map((m) => ({ label: m![1], md: m![2] }));
      const labels = f.answer.toUpperCase().split(/[\s,;]+/).filter(Boolean);
      if (labels.length) rec.answer = { kind: "labels", labels };
    } else if (f.answer.trim()) {
      const v = Number(f.answer.replace(",", "."));
      rec.answer = f.type === "numeric_response" && !Number.isNaN(v) ? { kind: "numeric", value: v, text: f.answer.trim() }
        : { kind: "text", text: f.answer.trim() };
    }
    return rec;
  };
  const save = async () => {
    setBusy(true);
    try {
      const r = await post<{ id: number; served: boolean; reasons: string[]; created: boolean }>(`/api/admin/banks/${code}/questions`, record());
      setRes(r); toast(r.created ? "Đã tạo câu hỏi." : "Đã cập nhật (phiên bản mới nếu nội dung đổi).", "ok"); onSaved();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  return (
    <Modal title={`Thêm/sửa câu hỏi — ngân hàng ${code}`} wide onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Đóng</button>
      <button className="btn" onClick={save} disabled={busy || !f.external_id.trim() || !f.stem_md.trim()}>Lưu câu hỏi</button>
    </>}>
      <p className="muted small">Cú pháp: <span className="mono">**đậm**</span>, <span className="mono">*nghiêng*</span>,{" "}
        <span className="mono">{"{{tex:\\frac{1}{2}}}"}</span> cho công thức, <span className="mono">&lt;sub&gt;</span>/<span className="mono">&lt;sup&gt;</span>, dòng trống = đoạn mới.
        Cùng mã sẽ cập nhật câu cũ (tạo phiên bản mới, bài thi đã làm không đổi).</p>
      <div className="grid cols-4">
        <div className="field"><label htmlFor="mq-id">Mã câu (ổn định)</label>
          <input id="mq-id" className="input" value={f.external_id} onChange={(e) => setF({ ...f, external_id: e.target.value })} /></div>
        <div className="field"><label htmlFor="mq-type">Dạng</label>
          <select id="mq-type" className="input" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
            {["single_choice", "multiple_choice", "numeric_response", "short_response", "true_false_statements", "error_identification", "constructed_response"]
              .map((t) => <option key={t} value={t}>{TYPE_LABELS[t]}</option>)}</select></div>
        <div className="field"><label htmlFor="mq-subj">Môn</label>
          <select id="mq-subj" className="input" value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })}>
            {(subjects.data?.items || []).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}</select></div>
        <div className="field"><label htmlFor="mq-status">Trạng thái</label>
          <select id="mq-status" className="input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
            {["READY_TO_SERVE", "NEEDS_REVIEW", "NEEDS_ANSWER_LINKING"].map((s) => <option key={s}>{s}</option>)}</select></div>
      </div>
      <div className="field mt"><label htmlFor="mq-stem">Đề bài</label>
        <textarea id="mq-stem" className="input" value={f.stem_md} onChange={(e) => setF({ ...f, stem_md: e.target.value })} /></div>
      {choice && <div className="field mt"><label htmlFor="mq-opts">Phương án (mỗi dòng “A. nội dung”)</label>
        <textarea id="mq-opts" className="input" value={f.options} onChange={(e) => setF({ ...f, options: e.target.value })} /></div>}
      <div className="grid cols-2 mt">
        <div className="field"><label htmlFor="mq-ans">Đáp án {choice ? "(nhãn, vd: B hoặc A,C)" : "(giá trị)"}</label>
          <input id="mq-ans" className="input" value={f.answer} onChange={(e) => setF({ ...f, answer: e.target.value })} /></div>
        <div className="field"><label htmlFor="mq-sol">Lời giải</label>
          <textarea id="mq-sol" className="input" value={f.solution_md} onChange={(e) => setF({ ...f, solution_md: e.target.value })} /></div>
      </div>
      {res && <div className={"alert mt " + (res.served ? "ok" : "warn")}>
        {res.served ? "Câu hỏi đang được phục vụ." : `Chưa phục vụ: ${res.reasons.join(", ")}`}{" "}
        <Link to={`/admin/cau-hoi/${res.id}`}>Xem trước</Link></div>}
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
