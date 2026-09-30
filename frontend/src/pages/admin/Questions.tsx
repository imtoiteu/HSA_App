import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, qs } from "../../lib/api";
import { STATE_LABELS, TYPE_LABELS } from "../../lib/format";
import { Empty, ErrorBox, Pager, Spinner, useAsync } from "../../components/ui";
import { PageHead, ServedBadge, StateBadge, useUrlState } from "./common";

interface QRow {
  id: number; external_id: string; bank: string; subject: string | null; type: string; state: string; state_source: string;
  served: boolean; eligible: boolean; reasons: string[]; override: string | null; scoring_mode: string; removed: boolean; preview: string;
}
const SIZE = 25;
const boolParam = (v: string) => (v === "yes" ? "true" : v === "no" ? "false" : undefined);

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
    override: boolParam(u.get("override")), page, size: SIZE,
  };
  const key = JSON.stringify(params);
  const list = useAsync(() => get<{ total: number; items: QRow[] }>("/api/admin/questions" + qs(params)), [key]);

  const sel = (id: string, label: string, name: string, options: [string, string][]) => (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="input" value={u.get(name)} onChange={(e) => u.set({ [name]: e.target.value })}>
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
        <div className="field">
          <label htmlFor="f-reason">Lý do loại</label>
          <input id="f-reason" className="input" defaultValue={u.get("reason")} placeholder="vd: group_context_missing"
                 onBlur={(e) => u.set({ reason: e.target.value.trim() })} />
        </div>
        <button className="btn ghost sm" type="button" onClick={() => { setQ(""); nav("/admin/cau-hoi", { replace: true }); }}>Xoá lọc</button>
      </div>
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
                    <td>{r.subject || <span className="muted">—</span>}</td>
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
