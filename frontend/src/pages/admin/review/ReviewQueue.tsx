// Review queues: server-side filtered/sorted/paginated list, selection and bulk assignment.
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorBox, Modal, Pager, Spinner, useAsync, useToast } from "../../../components/ui";
import { get, post, qs } from "../../../lib/api";
import { TYPE_LABELS } from "../../../lib/format";
import { PageHead, StateBadge, errMsg, useUrlState } from "../common";
import { DIM_ICON, DIM_TONE, STATUS_LABEL, type QueueItem } from "./rtypes";
import { useMeta } from "./useMeta";

const SIZE = 50;
const KEYS = ["queue", "subject", "document", "page_from", "page_to", "ready", "answer", "formula", "visual", "findings",
  "assignee", "status", "modified_from", "modified_to", "q", "qtype", "served", "assignment", "sort"];
const DIM_FILTER: [string, string][] = [["pending", "Chờ xác nhận"], ["ok", "Đạt"], ["fail", "Không đạt"], ["stale", "Cần xác nhận lại"], ["na", "Không áp dụng"]];

export default function ReviewQueue() {
  const u = useUrlState({ sort: "priority" });
  const nav = useNavigate();
  const toast = useToast();
  const meta = useMeta();
  const page = Number(u.get("page") || 1);
  const filters = Object.fromEntries(KEYS.map((k) => [k, u.get(k)]).filter(([, v]) => v));
  const key = JSON.stringify(filters) + page;
  const list = useAsync(() => get<{ total: number; items: QueueItem[] }>("/api/review/queue" + qs({ ...filters, page, size: SIZE })), [key]);
  const counts = useAsync(() => get<{ queues: Record<string, number>; pending_refresh: number }>("/api/review/queue/counts"), []);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [assign, setAssign] = useState<null | "selected" | "filter">(null);
  const [q, setQ] = useState(u.get("q"));
  useEffect(() => setSel(new Set()), [key]);

  const canAssign = meta?.me.perms.includes("assign");
  const sel_ = (id: string, label: string, name: string, options: [string, string][], all = "Tất cả") => (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="input" value={u.get(name)} onChange={(e) => u.set({ [name]: e.target.value })}>
        {all && <option value="">{all}</option>}
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
  const items = list.data?.items || [];
  const allOn = items.length > 0 && items.every((i) => sel.has(i.id));

  return (
    <div>
      <PageHead title="Hàng đợi duyệt">
        <Link className="btn ghost sm" to="/admin/review">Tổng quan duyệt</Link>
      </PageHead>
      {counts.data && (
        <div className="queue-chips">
          {Object.entries(meta?.queues || {}).map(([k, l]) => (
            <button key={k} className={"chip" + (u.get("queue") === k ? " on" : "")} onClick={() => u.set({ queue: u.get("queue") === k ? "" : k })}>
              {l} <span className="muted">{(counts.data!.queues[k] || 0).toLocaleString("vi-VN")}</span>
            </button>
          ))}
          {counts.data.pending_refresh > 0 && <span className="small muted">({counts.data.pending_refresh.toLocaleString("vi-VN")} câu đang chờ kiểm tra lại tự động)</span>}
        </div>
      )}
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); u.set({ q }); }}>
        <div className="field" style={{ minWidth: 240, flex: 1 }}>
          <label htmlFor="rq-q">Tìm cq_… / nội dung</label>
          <input id="rq-q" className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="cq_… hoặc từ khoá trong đề" />
        </div>
        <div className="field" style={{ minWidth: 220 }}>
          <label htmlFor="rq-doc">Tài liệu nguồn</label>
          <input id="rq-doc" className="input" defaultValue={u.get("document")} placeholder="doc_… hoặc một phần tên tệp"
                 onBlur={(e) => u.set({ document: e.target.value.trim() })} />
        </div>
        <button className="btn" type="submit">Tìm</button>
      </form>
      <div className="toolbar">
        {sel_("rq-queue", "Hàng đợi", "queue", Object.entries(meta?.queues || {}))}
        {sel_("rq-subject", "Môn", "subject", [["_none", "(chưa gán)"], ...(meta?.subjects || []).map((s) => [s.code, s.name] as [string, string])])}
        {sel_("rq-ready", "READY", "ready", [["yes", "READY"], ["no", "Chưa READY"]])}
        {sel_("rq-answer", "Đáp án", "answer", [["has", "Có đáp án"], ["none", "Chưa có"], ["ok", "Đã xác nhận"], ["pending", "Chờ xác nhận"]])}
        {sel_("rq-formula", "Công thức", "formula", DIM_FILTER)}
        {sel_("rq-visual", "Hình", "visual", DIM_FILTER)}
        {sel_("rq-findings", "Kiểm tra tự động", "findings", [["critical", "Có lỗi nghiêm trọng"], ["any", "Có phát hiện"], ["none", "Không có"]])}
        {sel_("rq-status", "Trạng thái duyệt", "status", Object.entries(STATUS_LABEL))}
        {sel_("rq-assignee", "Người duyệt", "assignee", [["me", "Của tôi"], ["none", "Chưa giao"], ...(meta?.reviewers || []).map((r) => [String(r.id), r.name] as [string, string])])}
        {sel_("rq-type", "Dạng câu", "qtype", Object.entries(TYPE_LABELS))}
        <div className="field"><label htmlFor="rq-pf">Trang nguồn</label>
          <div className="row" style={{ gap: 4 }}>
            <input id="rq-pf" className="input" style={{ width: 70 }} type="number" min={1} defaultValue={u.get("page_from")} placeholder="từ" onBlur={(e) => u.set({ page_from: e.target.value })} />
            <input className="input" style={{ width: 70 }} type="number" min={1} defaultValue={u.get("page_to")} placeholder="đến" aria-label="Đến trang" onBlur={(e) => u.set({ page_to: e.target.value })} />
          </div></div>
        <div className="field"><label htmlFor="rq-mf">Sửa từ ngày</label>
          <input id="rq-mf" className="input" type="date" value={u.get("modified_from")} onChange={(e) => u.set({ modified_from: e.target.value })} /></div>
        <div className="field"><label htmlFor="rq-mt">đến ngày</label>
          <input id="rq-mt" className="input" type="date" value={u.get("modified_to")} onChange={(e) => u.set({ modified_to: e.target.value })} /></div>
        {sel_("rq-sort", "Sắp xếp", "sort", [["priority", "Ưu tiên cao trước"], ["oldest", "Cũ nhất trước"], ["document", "Theo tài liệu nguồn"], ["subject", "Theo môn"], ["cq_id", "Theo cq_id"], ["updated", "Sửa gần đây"]], "")}
        <button className="btn ghost sm" type="button" onClick={() => { setQ(""); nav("/admin/review/queue", { replace: true }); }}>Xoá lọc</button>
      </div>

      <div className="row between mb" style={{ flexWrap: "wrap", gap: 8 }}>
        <span className="muted small">{list.data ? `${list.data.total.toLocaleString("vi-VN")} câu` : ""}{sel.size ? ` · đã chọn ${sel.size}` : ""}</span>
        <div className="row" style={{ gap: 6 }}>
          {items.length > 0 && <button className="btn sm" onClick={() => nav(`/admin/review/q/${items[0].id}${qs(filters)}`)}>Bắt đầu duyệt ›</button>}
          {canAssign && <button className="btn secondary sm" disabled={!sel.size} onClick={() => setAssign("selected")}>Giao {sel.size || ""} câu đã chọn</button>}
          {canAssign && <button className="btn secondary sm" disabled={!list.data?.total} onClick={() => setAssign("filter")}>Giao theo bộ lọc…</button>}
        </div>
      </div>

      {list.loading ? <Spinner /> : list.error ? <ErrorBox error={list.error} /> : !items.length ? (
        <Empty title="Không có câu nào trong hàng đợi/bộ lọc này." />
      ) : (
        <>
          <div className="table-wrap">
            <table className="data review-table">
              <thead><tr>
                <th><input type="checkbox" aria-label="Chọn tất cả trên trang" checked={allOn}
                           onChange={() => setSel(allOn ? new Set() : new Set(items.map((i) => i.id)))} /></th>
                <th>Mã</th><th>Môn / dạng</th><th>Nguồn</th><th>QA</th><th>Trạng thái</th><th>Nội dung</th>
              </tr></thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => nav(`/admin/review/q/${r.id}${qs(filters)}`)}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" aria-label={`Chọn ${r.external_id}`} checked={sel.has(r.id)}
                             onChange={() => setSel((s) => { const n = new Set(s); if (n.has(r.id)) n.delete(r.id); else n.add(r.id); return n; })} />
                    </td>
                    <td><span className="mono">{r.external_id}</span><div className="small muted">ưu tiên {r.priority}</div></td>
                    <td>{r.subject || <span className="muted">—</span>}<div className="small muted">{TYPE_LABELS[r.type] || r.type}</div></td>
                    <td className="small" style={{ maxWidth: 220 }}>{r.document || "—"}<div className="muted">{r.source_page ? `tr. ${r.source_page}` : ""}{r.source_number ? ` · câu ${r.source_number}` : ""}</div></td>
                    <td className="small" style={{ whiteSpace: "nowrap" }}>
                      {["content", "answer", "formula", "visual", "subject", "source"].map((k) => {
                        const s = (r.dims || {})[k] || "pending";
                        return <span key={k} className={"dim-dot " + DIM_TONE[s]} title={`${meta?.dimensions[k]}: ${s}`}>{DIM_ICON[s]}</span>;
                      })}
                      {r.findings_critical > 0 && <div><span className="badge bad">{r.findings_critical} lỗi</span></div>}
                    </td>
                    <td><StateBadge state={r.state} /><div className="small">{STATUS_LABEL[r.status] || r.status}{r.draft ? " · nháp" : ""}</div>
                      {r.assignee && <div className="small muted">→ {r.assignee.name}</div>}
                      {r.served && <div className="small" style={{ color: "var(--ok)" }}>đang phục vụ</div>}</td>
                    <td className="small" style={{ maxWidth: 380 }}>{r.preview}
                      <div className="muted">{r.queues.filter((x) => x !== "NOT_READY").map((x) => meta?.queues[x] || x).join(" · ")}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} size={SIZE} total={list.data!.total} onPage={(p) => u.set({ page: p }, false)} />
        </>
      )}
      {assign && meta && (
        <AssignDialog mode={assign} count={assign === "selected" ? sel.size : list.data?.total || 0} reviewers={meta.reviewers}
                      queueLabel={u.get("queue") ? meta.queues[u.get("queue")] : ""} subject={u.get("subject")}
                      onClose={() => setAssign(null)}
                      onSubmit={async (reviewer, limit, label) => {
                        try {
                          const body = assign === "selected" ? { reviewer_id: reviewer, ids: [...sel], label }
                            : { reviewer_id: reviewer, filters: { ...filters, sort: u.get("sort") || "priority" }, limit, label };
                          const r = await post<{ assigned: number }>("/api/review/assign", body);
                          toast(`Đã giao ${r.assigned} câu.`, "ok");
                          setAssign(null);
                          list.reload();
                        } catch (e) { toast(errMsg(e), "error"); }
                      }} />
      )}
    </div>
  );
}

function AssignDialog({ mode, count, reviewers, queueLabel, subject, onClose, onSubmit }: {
  mode: "selected" | "filter"; count: number; reviewers: { id: number; name: string; role: string }[]; queueLabel: string; subject: string;
  onClose: () => void; onSubmit: (reviewer: number | null, limit: number, label: string) => void;
}) {
  const [rv, setRv] = useState<string>("");
  const [limit, setLimit] = useState(Math.min(count, 500));
  const [label, setLabel] = useState([subject, queueLabel, mode === "filter" ? `${Math.min(count, 500)} câu` : `${count} câu`].filter(Boolean).join(" · "));
  return (
    <Modal title={mode === "selected" ? `Giao ${count} câu đã chọn` : "Giao theo bộ lọc"} onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Huỷ</button>
      <button className="btn" disabled={rv === ""} onClick={() => onSubmit(rv === "none" ? null : Number(rv), limit, label)}>{rv === "none" ? "Bỏ giao" : "Giao việc"}</button>
    </>}>
      <div className="stack">
        <div className="field"><label htmlFor="as-rv">Người duyệt</label>
          <select id="as-rv" className="input" value={rv} onChange={(e) => setRv(e.target.value)}>
            <option value="">— chọn —</option><option value="none">(Bỏ giao)</option>
            {reviewers.map((r) => <option key={r.id} value={r.id}>{r.name} · {r.role}</option>)}
          </select></div>
        {mode === "filter" && <div className="field"><label htmlFor="as-n">Số câu (lấy theo thứ tự sắp xếp hiện tại, chỉ câu chưa giao)</label>
          <input id="as-n" className="input" type="number" min={1} max={5000} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
          <span className="small muted">Bộ lọc hiện có {count.toLocaleString("vi-VN")} câu.</span></div>}
        <div className="field"><label htmlFor="as-l">Tên đợt giao</label>
          <input id="as-l" className="input" value={label} onChange={(e) => setLabel(e.target.value)} /></div>
      </div>
    </Modal>
  );
}
