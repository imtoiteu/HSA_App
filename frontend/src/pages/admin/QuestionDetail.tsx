import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get, post } from "../../lib/api";
import { dateTime, REPORT_LABELS, TYPE_LABELS } from "../../lib/format";
import type { Answer, Block, Group, InputSpec, OptionView } from "../../lib/types";
import { ErrorBox, Modal, Spinner, useAsync, useToast } from "../../components/ui";
import { RichContent } from "../../components/RichContent";
import { AnswerInput, Passage, Solution } from "../../components/QuestionBody";
import { errMsg, Json, PageHead, ServedBadge, StateBadge } from "./common";

interface Content {
  type: string; language?: string; input: InputSpec; group?: Group | null; stem: Block[];
  options: { label: string; content: Block[] }[]; solution?: Block[] | null; explanation?: Block[] | null;
  shuffle_safe?: boolean; assets?: string[];
}
interface Version { id: number; version_no: number; content: Content; answer: Answer | null; content_hash: string; created_at?: string; source_revision?: string }
interface Report { id: number; category: string; message: string | null; status: string; admin_note: string | null; created_at: string; session_id: string | null; question_version_id: number | null }
interface Correction { id: number; field: string; old_value: string | null; new_value: string; evidence: string | null; note: string | null; status: string; created_at: string; report_id: number | null }
interface Detail {
  id: number; external_id: string; bank: string; bank_name: string; type: string; subject: string | null; source_subject: string | null;
  topic: string | null; subtopic: string | null; cognitive_level: string | null; language: string | null; group_key: string | null;
  exam_systems: string[]; review_status: string | null; review_flags: string[]; answer_source_type: string | null;
  editorial_state: string; editorial_states: string[]; state_source: string; state_notes: string[]; scoring_mode: string;
  content_flags: string[]; content_flag_labels: Record<string, string>; policy_eligible: boolean; policy_reasons: string[];
  served: boolean; override: string | null; override_note: string | null; override_at: string | null; removed_upstream: boolean;
  provenance: Record<string, unknown>; first_synced_at: string; last_synced_at: string; content_changed_at: string | null;
  current_version: Version | null;
  versions: { id: number; version_no: number; content_hash: string; created_at: string; source_revision: string | null }[];
  usage: Record<string, number>; reports: Report[]; corrections: Correction[];
}

export function QuestionPreview({ content, answer }: { content: Content; answer: Answer | null }) {
  const opts: OptionView[] = content.options.map((o) => ({ key: o.label, display: o.label, content: o.content }));
  return (
    <div>
      {content.group && <Passage group={content.group} lang={content.language} />}
      <RichContent blocks={content.stem} />
      <AnswerInput input={content.input} options={opts} response={null} onChange={() => {}} disabled reveal={{ answer, response: null }} />
      <Solution q={content} answer={answer} />
    </div>
  );
}

const FIELDS = ["stem", ..."ABCDEFGH".split("").map((l) => `option:${l}`), "answer", "solution", "explanation", "subject", "topic", "status", "other"];

function CorrectionForm({ qid, reports, onDone }: { qid: number; reports: Report[]; onDone: () => void }) {
  const [f, setF] = useState({ field: "stem", new_value: "", old_value: "", evidence: "", note: "", report_id: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const toast = useToast();
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      await post(`/api/admin/questions/${qid}/corrections`, {
        field: f.field, new_value: f.new_value, old_value: f.old_value || null, evidence: f.evidence || null,
        note: f.note || null, report_id: f.report_id ? Number(f.report_id) : null,
      });
      toast("Đã ghi đề xuất sửa.", "ok");
      setF({ ...f, new_value: "", old_value: "", evidence: "", note: "" });
      onDone();
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <div className="stack">
      <div className="row">
        <div className="field"><label htmlFor="c-field">Trường</label>
          <select id="c-field" className="input" value={f.field} onChange={(e) => setF({ ...f, field: e.target.value })}>
            {FIELDS.map((x) => <option key={x} value={x}>{x}</option>)}
          </select></div>
        <div className="field"><label htmlFor="c-rep">Báo lỗi liên quan</label>
          <select id="c-rep" className="input" value={f.report_id} onChange={(e) => setF({ ...f, report_id: e.target.value })}>
            <option value="">(không)</option>
            {reports.map((r) => <option key={r.id} value={r.id}>#{r.id} {REPORT_LABELS[r.category]}</option>)}
          </select></div>
      </div>
      <div className="field"><label htmlFor="c-new">Giá trị mới (hsa-md; công thức dạng {"{{tex:...}}"})</label>
        <textarea id="c-new" className="input" value={f.new_value} onChange={(e) => setF({ ...f, new_value: e.target.value })} /></div>
      <div className="field"><label htmlFor="c-old">Giá trị cũ (tuỳ chọn)</label>
        <textarea id="c-old" className="input" value={f.old_value} onChange={(e) => setF({ ...f, old_value: e.target.value })} /></div>
      <div className="field"><label htmlFor="c-ev">Bằng chứng (tài liệu nguồn + trang/vị trí)</label>
        <input id="c-ev" className="input" value={f.evidence} onChange={(e) => setF({ ...f, evidence: e.target.value })} /></div>
      <div className="field"><label htmlFor="c-note">Ghi chú</label>
        <input id="c-note" className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></div>
      {err && <div className="error-text">{err}</div>}
      <div><button className="btn" disabled={busy || !f.new_value.trim()} onClick={submit}>{busy ? "Đang lưu…" : "Ghi đề xuất sửa"}</button></div>
      <div className="small muted">Đề xuất sửa không thay đổi ngân hàng gốc; chúng được xuất sang HSA-question-bank để biên tập viên xác nhận.</div>
    </div>
  );
}

export default function QuestionDetail() {
  const { id } = useParams();
  const d = useAsync(() => get<Detail>(`/api/admin/questions/${id}`), [id]);
  const [showJson, setShowJson] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [ver, setVer] = useState<Version | null>(null);
  const toast = useToast();
  if (d.loading) return <Spinner />;
  if (d.error || !d.data) return <ErrorBox error={d.error} />;
  const q = d.data;

  const override = async (action: "enable" | "disable" | "clear") => {
    setBusy(true);
    try {
      await post(`/api/admin/questions/${q.id}/override`, { action, note: note || null });
      toast(action === "clear" ? "Đã bỏ ghi đè." : action === "enable" ? "Đã bật phục vụ." : "Đã tắt phục vụ.", "ok");
      setNote(""); d.reload();
    } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };
  const openVersion = async (vid: number) => {
    try { setVer(await get<Version>(`/api/admin/questions/${q.id}/versions/${vid}`)); } catch (e) { toast(errMsg(e), "error"); }
  };
  const prov = q.provenance || {};

  return (
    <div className="stack">
      <div className="small"><Link to="/admin/cau-hoi">← Danh sách câu hỏi</Link></div>
      <PageHead title={q.external_id}>
        <StateBadge state={q.editorial_state} />
        <ServedBadge served={q.served} />
        {q.removed_upstream && <span className="badge bad">Đã gỡ khỏi nguồn</span>}
      </PageHead>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1.4fr) minmax(0, 1fr)" }}>
        <div className="stack">
          <div className="card">
            <div className="row between mb">
              <h3 style={{ margin: 0 }}>Xem trước (như học sinh thấy)</h3>
              <button className="btn ghost sm" onClick={() => setShowJson((s) => !s)}>{showJson ? "Ẩn JSON" : "JSON thô"}</button>
            </div>
            {q.current_version ? (showJson ? <Json value={{ content: q.current_version.content, answer: q.current_version.answer }} />
              : <QuestionPreview content={q.current_version.content} answer={q.current_version.answer} />)
              : <div className="muted">Chưa có nội dung.</div>}
          </div>
          <div className="card">
            <h3>Đề xuất sửa</h3>
            {q.corrections.length > 0 && (
              <div className="table-wrap mb"><table className="data">
                <thead><tr><th>#</th><th>Trường</th><th>Giá trị mới</th><th>Trạng thái</th><th>Ngày</th></tr></thead>
                <tbody>{q.corrections.map((c) => (
                  <tr key={c.id}><td>{c.id}</td><td className="mono">{c.field}</td><td className="small">{c.new_value.slice(0, 200)}</td>
                    <td><span className="badge">{c.status}</span></td><td className="small">{dateTime(c.created_at)}</td></tr>
                ))}</tbody>
              </table></div>
            )}
            <CorrectionForm qid={q.id} reports={q.reports} onDone={d.reload} />
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <h3>Phục vụ học sinh</h3>
            <dl className="kv">
              <dt>Đủ điều kiện</dt><dd>{q.policy_eligible ? "Có" : "Không"}</dd>
              <dt>Lý do loại</dt><dd>{q.policy_reasons.length ? q.policy_reasons.join(", ") : "—"}</dd>
              <dt>Kiểm tra nội dung</dt><dd>{q.content_flags.length ? q.content_flags.map((f) => q.content_flag_labels[f] || f).join("; ") : "—"}</dd>
              <dt>Chấm điểm</dt><dd>{q.scoring_mode}</dd>
              <dt>Ghi đè</dt><dd>{q.override ? `${q.override} (${dateTime(q.override_at)})` : "—"}{q.override_note && <div className="small muted">{q.override_note}</div>}</dd>
            </dl>
            <div className="field mt"><label htmlFor="ov-note">Ghi chú ghi đè</label>
              <input id="ov-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Lý do bật/tắt" /></div>
            <div className="row mt">
              <button className="btn ok sm" disabled={busy} onClick={() => override("enable")}>Bật phục vụ</button>
              <button className="btn danger sm" disabled={busy} onClick={() => override("disable")}>Tắt phục vụ</button>
              {q.override && <button className="btn secondary sm" disabled={busy} onClick={() => override("clear")}>Theo chính sách</button>}
            </div>
          </div>
          <div className="card">
            <h3>Thông tin</h3>
            <dl className="kv">
              <dt>Ngân hàng</dt><dd>{q.bank_name} <span className="muted small">({q.bank})</span></dd>
              <dt>Môn</dt><dd>{q.subject || "—"} <span className="muted small">nguồn: {q.source_subject ?? "null"}</span></dd>
              <dt>Chủ đề</dt><dd>{[q.topic, q.subtopic].filter(Boolean).join(" › ") || "—"}</dd>
              <dt>Dạng</dt><dd>{TYPE_LABELS[q.type] || q.type}</dd>
              <dt>Trạng thái</dt><dd>{q.editorial_states.map((s) => <span key={s} style={{ marginRight: 4 }}><StateBadge state={s} /></span>)}
                <div className="small muted">nguồn trạng thái: {q.state_source}</div></dd>
              <dt>Ghi chú biên tập</dt><dd className="small">{q.state_notes.join("; ") || "—"}</dd>
              <dt>Đáp án nguồn</dt><dd>{q.answer_source_type || "—"}</dd>
              <dt>Review</dt><dd>{q.review_status || "—"} <div className="small muted">{q.review_flags.join(", ")}</div></dd>
              <dt>Nhóm</dt><dd className="mono">{q.group_key || "—"}</dd>
              <dt>Kỳ thi</dt><dd>{q.exam_systems.join(", ") || "—"}</dd>
              <dt>Đồng bộ</dt><dd className="small">lần đầu {dateTime(q.first_synced_at)}<br />gần nhất {dateTime(q.last_synced_at)}<br />đổi nội dung {dateTime(q.content_changed_at)}</dd>
            </dl>
          </div>
          <div className="card">
            <h3>Nguồn gốc</h3>
            <dl className="kv">
              {["document_path", "exam_name", "exam_system", "year", "section", "source_question_number", "document_id",
                "representative_occurrence_id", "extraction_method", "document_role", "n_occurrences", "answer_confidence", "overlay"].map((k) => (
                <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd className="small">{prov[k] == null ? "—" : String(prov[k])}</dd></div>
              ))}
              <dt>render_issues</dt><dd className="small">{Array.isArray(prov.render_issues) && prov.render_issues.length ? (prov.render_issues as string[]).join("; ") : "—"}</dd>
            </dl>
          </div>
          <div className="card">
            <h3>Phiên bản ({q.versions.length})</h3>
            <div className="table-wrap"><table className="data"><tbody>
              {q.versions.map((v) => (
                <tr key={v.id} className="clickable" onClick={() => openVersion(v.id)}>
                  <td>v{v.version_no}{q.current_version?.id === v.id && <span className="badge brand" style={{ marginLeft: 6 }}>hiện tại</span>}</td>
                  <td className="mono small">{v.content_hash.slice(0, 12)}</td><td className="small">{dateTime(v.created_at)}</td>
                </tr>
              ))}
            </tbody></table></div>
          </div>
          <div className="card">
            <h3>Thống kê sử dụng</h3>
            {Object.keys(q.usage).length ? <dl className="kv">{Object.entries(q.usage).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>))}</dl> : <div className="muted small">Chưa được dùng.</div>}
          </div>
          <div className="card">
            <h3>Báo lỗi ({q.reports.length})</h3>
            {q.reports.length === 0 ? <div className="muted small">Không có.</div> : q.reports.map((r) => (
              <div key={r.id} className="small" style={{ borderTop: "1px solid var(--line)", padding: "8px 0" }}>
                <div className="row"><span className="badge warn">{REPORT_LABELS[r.category]}</span><span className="badge">{r.status}</span>
                  <span className="muted">{dateTime(r.created_at)}</span></div>
                {r.message && <div>{r.message}</div>}
                {r.admin_note && <div className="muted">Ghi chú: {r.admin_note}</div>}
              </div>
            ))}
            <Link to="/admin/bao-loi" className="small">Quản lý báo lỗi →</Link>
          </div>
        </div>
      </div>
      {ver && (
        <Modal wide title={`Phiên bản v${ver.version_no}`} onClose={() => setVer(null)}>
          <div className="small muted mb">hash {ver.content_hash} · {dateTime(ver.created_at)} · {ver.source_revision}</div>
          <QuestionPreview content={ver.content} answer={ver.answer} />
        </Modal>
      )}
    </div>
  );
}
