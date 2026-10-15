import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorBox, Modal, Spinner, useAsync, useToast } from "../../components/ui";
import { get, post, put } from "../../lib/api";
import { dateTime, num, TYPE_LABELS } from "../../lib/format";
import { errMsg } from "./common";
import { RichContent } from "../../components/RichContent";
import { AnswerValue } from "../../components/QuestionBody";
import type { Answer, Block } from "../../lib/types";
import { ANSWER_HINT, buildRecord, CHOICE_TYPES, EMPTY_FORM, formProblems, MANUAL_TYPES, type ManualForm } from "./manualQuestion";

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
type Preview = {
  id: number | null; external_id: string; served: boolean; reasons: string[]; editorial_state: string; subject: string | null;
  created: boolean; dry_run: boolean; answer: Answer | null;
  content: { stem: Block[]; options: { label: string; content: Block[] }[]; solution: Block[] | null } | null;
};
const MANUAL_BANK = { code: "thu_cong", name: "Câu hỏi nhập thủ công",
  description: "Câu hỏi do quản trị viên soạn trực tiếp trong trang quản trị." };

/** Admin → "Thêm câu hỏi": a new question in a manual bank (never overwrites an existing one; edit it in Review
 * Studio). Without `code` the admin picks the bank (a manual bank is offered when there is none). */
export function ManualQuestion({ code, onClose, onSaved }: { code?: string; onClose: () => void; onSaved: () => void }) {
  const subjects = useAsync(() => get<{ items: { code: string; name: string }[] }>("/api/admin/subjects"), []);
  const banks = useAsync(() => get<{ items: BankFull[] }>("/api/admin/banks"), []);
  const manualBanks = (banks.data?.items || []).filter((b) => b.source_kind !== "hsa_upstream");
  const [bank, setBank] = useState(code || "");
  const target = bank || code || manualBanks[0]?.code || "";
  const [f, setF] = useState<ManualForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [saved, setSaved] = useState<Preview | null>(null);
  const toast = useToast();
  const choice = CHOICE_TYPES.includes(f.type);
  const problems = formProblems(f);
  const set = (p: Partial<ManualForm>) => { setF({ ...f, ...p }); setPreview(null); };

  const createBank = async () => {
    setBusy(true);
    try {
      await post("/api/admin/banks", { ...MANUAL_BANK, source_kind: "manual", is_active: true });
      toast(`Đã tạo ngân hàng “${MANUAL_BANK.name}”.`, "ok"); setBank(MANUAL_BANK.code); banks.reload(); onSaved();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const send = async (dry: boolean) => {
    setBusy(true);
    try {
      const r = await post<Preview>(`/api/admin/banks/${encodeURIComponent(target)}/questions?mode=create${dry ? "&dry_run=true" : ""}`, buildRecord(f));
      if (dry) setPreview(r);
      else { setSaved(r); setPreview(r); toast(`Đã tạo câu hỏi ${r.external_id}.`, "ok"); onSaved(); }
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const again = () => { setF({ ...EMPTY_FORM, type: f.type, subject: f.subject, status: f.status }); setSaved(null); setPreview(null); };

  if (banks.loading) return <Modal title="Thêm câu hỏi" onClose={onClose}><Spinner /></Modal>;
  if (!code && manualBanks.length === 0) {
    return (
      <Modal title="Thêm câu hỏi" onClose={onClose} actions={<>
        <button className="btn secondary" onClick={onClose}>Đóng</button>
        <button className="btn" disabled={busy} onClick={createBank}>Tạo ngân hàng “{MANUAL_BANK.name}”</button>
      </>}>
        <p>Ngân hàng HSA được đồng bộ từ nguồn nên không nhận câu nhập tay. Câu hỏi tự soạn được lưu trong một ngân hàng
          thủ công riêng (mã <span className="mono">{MANUAL_BANK.code}</span>); câu đạt chuẩn được phục vụ cho luyện tập
          và đề thi như mọi câu khác, và có thể tắt cả ngân hàng bất cứ lúc nào.</p>
      </Modal>
    );
  }
  const s = saved;
  return (
    <Modal title={`Thêm câu hỏi mới — ngân hàng ${target}`} wide onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Đóng</button>
      {s ? <button className="btn" onClick={again}>Thêm câu khác</button> : <>
        <button className="btn secondary" onClick={() => send(true)} disabled={busy || problems.length > 0 || !target}>Xem trước</button>
        <button className="btn" onClick={() => send(false)} disabled={busy || problems.length > 0 || !target}>Lưu câu hỏi</button>
      </>}
    </>}>
      <p className="muted small">Cú pháp: <span className="mono">**đậm**</span>, <span className="mono">*nghiêng*</span>,{" "}
        <span className="mono">{"{{tex:\\frac{1}{2}}}"}</span> cho công thức, <span className="mono">&lt;sub&gt;</span>/<span className="mono">&lt;sup&gt;</span>, dòng trống = đoạn mới.
        Hình, bảng và công thức phức tạp: lưu câu rồi bổ sung trong Review Studio.</p>
      <fieldset disabled={!!s} style={{ border: 0, padding: 0, margin: 0 }}>
      <div className="grid cols-4">
        {!code && <div className="field"><label htmlFor="mq-bank">Ngân hàng</label>
          <select id="mq-bank" className="input" value={target} onChange={(e) => { setBank(e.target.value); setPreview(null); }}>
            {manualBanks.map((b) => <option key={b.code} value={b.code}>{b.name}</option>)}</select></div>}
        <div className="field"><label htmlFor="mq-id">Mã câu</label>
          <input id="mq-id" className="input" value={f.external_id} placeholder="để trống: tự sinh" onChange={(e) => set({ external_id: e.target.value })} /></div>
        <div className="field"><label htmlFor="mq-type">Dạng</label>
          <select id="mq-type" className="input" value={f.type} onChange={(e) => set({ type: e.target.value })}>
            {MANUAL_TYPES.map((t) => <option key={t} value={t}>{TYPE_LABELS[t] || t}</option>)}</select></div>
        <div className="field"><label htmlFor="mq-subj">Môn</label>
          <select id="mq-subj" className="input" value={f.subject} onChange={(e) => set({ subject: e.target.value })}>
            {(subjects.data?.items || []).map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}</select></div>
        <div className="field"><label htmlFor="mq-status">Trạng thái</label>
          <select id="mq-status" className="input" value={f.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="READY_TO_SERVE">Sẵn sàng phục vụ</option>
            <option value="NEEDS_REVIEW">Chờ duyệt (chưa phục vụ)</option></select></div>
      </div>
      <div className="field mt"><label htmlFor="mq-stem">Đề bài{f.type === "true_false_statements" ? " (các mệnh đề a), b), c)… mỗi mệnh đề một đoạn)" : ""}</label>
        <textarea id="mq-stem" className="input" rows={4} value={f.stem_md} onChange={(e) => set({ stem_md: e.target.value })} /></div>
      {choice && <div className="field mt"><label htmlFor="mq-opts">Phương án (mỗi dòng “A. nội dung”)</label>
        <textarea id="mq-opts" className="input" rows={4} value={f.options} onChange={(e) => set({ options: e.target.value })} /></div>}
      <div className="grid cols-2 mt">
        <div className="field"><label htmlFor="mq-ans">Đáp án ({ANSWER_HINT[f.type] || "giá trị"})</label>
          <input id="mq-ans" className="input" value={f.answer} onChange={(e) => set({ answer: e.target.value })} /></div>
        <div className="field"><label htmlFor="mq-sol">Lời giải</label>
          <textarea id="mq-sol" className="input" value={f.solution_md} onChange={(e) => set({ solution_md: e.target.value })} /></div>
      </div>
      </fieldset>
      {problems.length > 0 && !s && <div className="alert warn mt">{problems.map((p) => <div key={p}>{p}</div>)}</div>}
      {preview && preview.content && (
        <div className="card mt" aria-label="Xem trước">
          <div className="row between"><strong>{preview.dry_run ? "Xem trước (chưa lưu)" : `Đã lưu: ${preview.external_id}`}</strong>
            <span className={"badge " + (preview.served ? "ok" : "warn")}>{preview.served ? "Sẽ được phục vụ" : "Chưa phục vụ"}</span></div>
          <RichContent blocks={preview.content.stem} />
          {preview.content.options.length > 0 && <ol className="plain mt">{preview.content.options.map((o) => (
            <li key={o.label} className="row" style={{ alignItems: "baseline", gap: 6 }}><strong>{o.label}.</strong> <RichContent blocks={o.content} /></li>))}</ol>}
          <div className="small mt">Đáp án: <strong><AnswerValue a={preview.answer} /></strong></div>
          {preview.content.solution && <div className="small mt"><em>Lời giải:</em> <RichContent blocks={preview.content.solution} /></div>}
          {!preview.served && <div className="small muted mt">Lý do: <span className="mono">{preview.reasons.join(", ") || preview.editorial_state}</span></div>}
        </div>
      )}
      {s && s.id && <div className="row mt" style={{ gap: 8 }}>
        <Link className="btn secondary sm" to={`/admin/cau-hoi/${s.id}`}>Xem chi tiết</Link>
        <Link className="btn secondary sm" to={`/admin/review/q/${s.id}`}>Mở trong Review Studio (hình, bảng, công thức)</Link>
      </div>}
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
