import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, qs } from "../../lib/api";
import { STATE_LABELS, TYPE_LABELS } from "../../lib/format";
import { Empty, ErrorBox, Pager, Spinner, useAsync } from "../../components/ui";
import { PageHead, ServedBadge, StateBadge, useUrlState } from "./common";

interface QRow {
  id: number; external_id: string; bank: string; subject: string | null; type: string; state: string; state_source: string;
  served: boolean; eligible: boolean; reasons: string[]; override: string | null; scoring_mode: string; removed: boolean; preview: string;
  source_subject: string | null; inferred_subject: string | null; inference_confidence: string | null;
  subject_source: string; has_answer: boolean;
}
const SUBJECT_SOURCE: Record<string, string> = {
  original: "gốc", upstream_effective: "phân loại nguồn", inferred: "suy luận", admin: "admin", unclassified: "chưa phân loại",
};
const RAW_SUBJECTS: [string, string][] = [
  ["_none", "(trống)"], ["math", "math"], ["literature_language", "literature_language"], ["english", "english"],
  ["physics", "physics"], ["chemistry", "chemistry"], ["biology", "biology"], ["history", "history"],
  ["geography", "geography"], ["science", "science"], ["logic_reasoning", "logic_reasoning"],
];
const SIZE = 25;
// URL booleans: "yes"/"no" from the selects, "true"/"false" from drill-down links (dashboard, reconciliation, QA queues)
const boolParam = (v: string) => (v === "yes" || v === "true" ? "true" : v === "no" || v === "false" ? "false" : undefined);
const SPECIAL: Record<string, string> = {
  upstream_subject: "Môn theo nguồn", free_pool: "Thuộc tập câu Miễn phí", subject_mismatch: "Lệch môn so với nguồn",
  ready_upstream: "READY ở nguồn", classification_review: "Cần xem lại môn", active_bank: "Ngân hàng đang hoạt động",
};

export default function Questions() {
  const u = useUrlState();
  const nav = useNavigate();
  const page = Number(u.get("page") || 1);
  const [q, setQ] = useState(u.get("q"));
  const subjects = useAsync(() => get<{ items: { code: string; name: string }[] }>("/api/admin/subjects"), []);
  const banks = useAsync(() => get<{ items: { code: string; name: string }[] }>("/api/admin/banks"), []);
  const params = {
    q: u.get("q"), subject: u.get("subject"), state: u.get("state"), served: boolParam(u.get("served")),
    qtype: u.get("qtype"), bank: u.get("bank"), reason: u.get("reason"), reported: boolParam(u.get("reported")),
    override: boolParam(u.get("override")), source_subject: u.get("source_subject"),
    inferred_subject: u.get("inferred_subject"), subject_source: u.get("subject_source"),
    has_answer: boolParam(u.get("has_answer")), has_formula: boolParam(u.get("has_formula")),
    has_image: boolParam(u.get("has_image")), eligible: boolParam(u.get("eligible")),
    scoring_mode: u.get("scoring_mode"), upstream_subject: u.get("upstream_subject"),
    classification_review: boolParam(u.get("classification_review")), has_table: boolParam(u.get("has_table")),
    cognitive_level: u.get("cognitive_level"), free_pool: boolParam(u.get("free_pool")),
    subject_mismatch: boolParam(u.get("subject_mismatch")), ready_upstream: boolParam(u.get("ready_upstream")),
    active_bank: boolParam(u.get("active_bank")), sort: u.get("sort") || undefined, page, size: SIZE,
  };
  const special = Object.keys(SPECIAL).filter((k) => u.get(k));
  const key = JSON.stringify(params);
  const list = useAsync(() => get<{ total: number; items: QRow[] }>("/api/admin/questions" + qs(params)), [key]);

  const sel = (id: string, label: string, name: string, options: [string, string][]) => (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="input" onChange={(e) => u.set({ [name]: e.target.value })}
              value={options.some((o) => o[0] === "yes") ? ({ true: "yes", false: "no" } as Record<string, string>)[u.get(name)] ?? u.get(name) : u.get(name)}>
        <option value="">Tất cả</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );

  return (
    <div>
      <PageHead title="Câu hỏi" />
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); u.set({ q }); }}>
        <div className="field" style={{ minWidth: 260, flex: 1 }}>
          <label htmlFor="q-search">Tìm theo mã cq_… hoặc nội dung</label>
          <input id="q-search" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="cq_3fa91c0d22e4b810 hoặc từ khoá" />
        </div>
        <button className="btn" type="submit">Tìm</button>
      </form>
      <div className="toolbar">
        {sel("f-subject", "Môn", "subject", [["_none", "(chưa gán)"], ...(subjects.data?.items || []).map((s) => [s.code, s.name] as [string, string])])}
        {sel("f-state", "Trạng thái biên tập", "state", Object.entries(STATE_LABELS))}
        {sel("f-served", "Phục vụ", "served", [["yes", "Đang phục vụ"], ["no", "Không phục vụ"]])}
        {sel("f-type", "Dạng câu", "qtype", Object.entries(TYPE_LABELS))}
        {sel("f-bank", "Ngân hàng", "bank", (banks.data?.items || []).map((b) => [b.code, b.name] as [string, string]))}
        {sel("f-reported", "Có báo lỗi", "reported", [["yes", "Có báo lỗi mở"]])}
        {sel("f-override", "Ghi đè admin", "override", [["yes", "Có ghi đè"], ["no", "Không ghi đè"]])}
        {sel("f-eligible", "Đủ điều kiện (chính sách)", "eligible", [["yes", "Đủ điều kiện"], ["no", "Không đủ"]])}
        {sel("f-ssource", "Nguồn môn hiệu lực", "subject_source", Object.entries(SUBJECT_SOURCE))}
        {sel("f-orig", "Môn gốc (ngân hàng)", "source_subject", RAW_SUBJECTS)}
        {sel("f-inf", "Môn suy luận", "inferred_subject", RAW_SUBJECTS)}
        {sel("f-answer", "Đáp án", "has_answer", [["yes", "Có đáp án"], ["no", "Chưa có đáp án"]])}
        {sel("f-mode", "Chấm điểm", "scoring_mode", [["auto", "Tự động"], ["self_check", "Tự đánh giá"], ["none", "Không chấm"]])}
        {sel("f-formula", "Công thức", "has_formula", [["yes", "Có công thức"], ["no", "Không"]])}
        {sel("f-image", "Hình ảnh", "has_image", [["yes", "Có hình"], ["no", "Không"]])}
        {sel("f-table", "Bảng", "has_table", [["yes", "Có bảng"], ["no", "Không"]])}
        {sel("f-upsub", "Môn theo nguồn", "upstream_subject", (subjects.data?.items || []).map((s) => [s.code, s.name] as [string, string]))}
        {sel("f-review", "Xem lại môn (nguồn)", "classification_review", [["yes", "SUBJECT_CLASSIFICATION_REVIEW"], ["no", "Không"]])}
        {sel("f-level", "Mức độ nhận thức", "cognitive_level", [["_none", "(chưa có)"], ["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"]])}
        {sel("f-sort", "Sắp xếp", "sort", [["subject", "Theo môn"], ["state", "Theo trạng thái"], ["type", "Theo dạng"], ["-updated", "Đồng bộ mới nhất"]])}
        <div className="field">
          <label htmlFor="f-reason">Lý do loại</label>
          <input id="f-reason" className="input" defaultValue={u.get("reason")} placeholder="vd: group_context_missing"
                 onBlur={(e) => u.set({ reason: e.target.value.trim() })} />
        </div>
        <button className="btn ghost sm" type="button" onClick={() => { setQ(""); nav("/admin/cau-hoi", { replace: true }); }}>Xoá lọc</button>
      </div>
      {special.length > 0 && (
        <div className="alert info mb small">Bộ lọc từ trang đối soát: {special.map((k) => `${SPECIAL[k]} = ${u.get(k)}`).join(" · ")}
          {" "}<button className="btn ghost sm" onClick={() => u.set(Object.fromEntries(special.map((k) => [k, ""])))}>Bỏ</button></div>
      )}
      {list.data && <div className="muted small mb">{list.data.total.toLocaleString("vi-VN")} câu hỏi</div>}
      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !list.data?.items.length ? (
        <Empty title="Không có câu hỏi phù hợp." />
      ) : (
        <>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Mã</th><th>Môn</th><th>Dạng</th><th>Trạng thái</th><th>Phục vụ</th><th>Lý do</th><th>Nội dung</th></tr></thead>
              <tbody>
                {list.data.items.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => nav(`/admin/cau-hoi/${r.id}`)}>
                    <td><span className="mono">{r.external_id}</span>{r.bank !== "hsa" && <div className="small muted">{r.bank}</div>}</td>
                    <td>{r.subject || <span className="muted">—</span>}
                      <div className="small muted">{SUBJECT_SOURCE[r.subject_source] || r.subject_source}
                        {r.subject_source === "inferred" && r.inference_confidence ? ` (${r.inference_confidence})` : ""}</div></td>
                    <td className="small">{TYPE_LABELS[r.type] || r.type}</td>
                    <td><StateBadge state={r.state} />{r.state_source === "upstream_manifest" && <div className="small muted">biên tập</div>}</td>
                    <td><ServedBadge served={r.served} />{r.override && <div><span className="badge brand">ghi đè: {r.override}</span></div>}
                      {r.removed && <div><span className="badge bad">đã gỡ</span></div>}</td>
                    <td className="small">{r.reasons.slice(0, 3).join(", ")}{r.reasons.length > 3 ? "…" : ""}</td>
                    <td className="small" style={{ maxWidth: 380 }}>{r.preview}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} size={SIZE} total={list.data.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
    </div>
  );
}
