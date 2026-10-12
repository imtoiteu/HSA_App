// Review overview: bank progress, queues, productivity (no ranking), my work, assignments.
import { Link } from "react-router-dom";
import { ErrorBox, Spinner, useAsync } from "../../../components/ui";
import { get } from "../../../lib/api";
import { PageHead } from "../common";
import { useMeta } from "./useMeta";

interface Stats {
  total: number; upstream_ready: number; ready: number; ready_gained: number; serving: number; not_ready: number; blocked: number;
  approved_week: number; reviewed_today: number; reviewed_7d: number; avg_per_day_7d: number;
  per_day: { day: string; n: number }[]; by_reviewer_7d: { name: string; questions: number }[];
  queues: Record<string, number>; pending_refresh: number; findings: Record<string, number>;
}
interface Mine { assigned: number; in_progress: number; reviewed: number; returned: number; remaining: number; reviewed_today: number }

const n = (x: number | undefined) => (x ?? 0).toLocaleString("vi-VN");
const MAIN = ["NEEDS_FORMULA_REVIEW", "NEEDS_ANSWER_LINKING", "NEEDS_VISUAL_REVIEW", "SUBJECT_REVIEW", "UNCLASSIFIED", "GENERAL_REVIEW",
  "ANSWER_CONFLICT", "INCOMPLETE", "VALIDATOR", "UPSTREAM_CHANGED", "DRAFT", "RETURNED"];

export default function ReviewDashboard() {
  const meta = useMeta();
  const st = useAsync(() => get<Stats>("/api/review/stats"), []);
  const me = useAsync(() => get<Mine>("/api/review/me"), []);
  const asg = useAsync(() => get<{ items: { id: number; label: string; reviewer: { name: string }; total: number; done: number; still_assigned: number; created_at: string }[] }>("/api/review/assignments"), []);
  if (st.loading) return <Spinner />;
  if (st.error || !st.data) return <ErrorBox error={st.error} />;
  const s = st.data;
  const pct = s.total ? Math.round((s.ready / s.total) * 1000) / 10 : 0;
  const maxDay = Math.max(1, ...s.per_day.map((d) => d.n));
  return (
    <div>
      <PageHead title="Review Studio">
        <Link className="btn" to="/admin/review/queue">Mở hàng đợi duyệt</Link>
      </PageHead>
      <div className="grid cols-4 mb">
        <div className="stat"><div className="v">{n(s.total)}</div><div className="l">Tổng câu canonical</div></div>
        <div className="stat"><div className="v">{n(s.ready)}</div><div className="l">READY ({pct}%) · nguồn {n(s.upstream_ready)}</div></div>
        <div className="stat"><div className="v">+{n(s.ready_gained)}</div><div className="l">READY nhờ duyệt · +{n(s.approved_week)} phê duyệt tuần này</div></div>
        <div className="stat"><div className="v">{n(s.serving)}</div><div className="l">Đang phục vụ · chưa READY {n(s.not_ready)}</div></div>
      </div>
      <div className="progress mb" aria-label={`READY ${pct}%`}><div style={{ width: `${pct}%` }} /></div>

      <div className="grid cols-2">
        <section className="card">
          <h3>Việc của tôi</h3>
          {me.data ? (
            <div className="mini-grid">
              <div className="stat sm"><div className="v">{n(me.data.assigned)}</div><div className="l">Được giao</div></div>
              <div className="stat sm"><div className="v">{n(me.data.in_progress)}</div><div className="l">Đang sửa (nháp)</div></div>
              <div className="stat sm"><div className="v">{n(me.data.reviewed_today)}</div><div className="l">Đã duyệt hôm nay</div></div>
              <div className="stat sm"><div className="v">{n(me.data.remaining)}</div><div className="l">Còn lại</div></div>
              <div className="stat sm"><div className="v">{n(me.data.returned)}</div><div className="l">Bị trả lại</div></div>
              <div className="stat sm"><div className="v">{n(me.data.reviewed)}</div><div className="l">Đã xong</div></div>
            </div>
          ) : <Spinner />}
          <div className="row mt" style={{ gap: 6 }}>
            <Link className="btn sm" to="/admin/review/queue?assignee=me">Câu được giao cho tôi</Link>
            <Link className="btn secondary sm" to="/admin/review/queue?assignee=me&status=returned">Bị trả lại</Link>
          </div>
          <p className="small muted mt">Vai trò: {meta?.me.role_label || meta?.me.role}.</p>
        </section>
        <section className="card">
          <h3>Tiến độ duyệt</h3>
          <div className="mini-grid">
            <div className="stat sm"><div className="v">{n(s.reviewed_today)}</div><div className="l">Câu đã xử lý hôm nay</div></div>
            <div className="stat sm"><div className="v">{n(s.reviewed_7d)}</div><div className="l">7 ngày qua</div></div>
            <div className="stat sm"><div className="v">{s.avg_per_day_7d}</div><div className="l">Trung bình/ngày</div></div>
          </div>
          <div className="bars mt" aria-label="Số câu xử lý mỗi ngày (30 ngày)">
            {s.per_day.map((d) => <div key={d.day} title={`${d.day}: ${d.n}`} style={{ height: `${(d.n / maxDay) * 100}%` }} />)}
          </div>
          {s.by_reviewer_7d.length > 0 && (
            <table className="data small mt"><thead><tr><th>Người duyệt (7 ngày)</th><th>Câu</th></tr></thead>
              <tbody>{[...s.by_reviewer_7d].sort((a, b) => a.name.localeCompare(b.name)).map((r) => <tr key={r.name}><td>{r.name}</td><td>{n(r.questions)}</td></tr>)}</tbody></table>
          )}
        </section>
      </div>

      <section className="card mt">
        <h3>Hàng đợi</h3>
        <div className="grid cols-4">
          {MAIN.map((k) => (
            <Link key={k} className="stat sm link" to={`/admin/review/queue?queue=${k}`}>
              <div className="v">{n(s.queues[k])}</div><div className="l">{meta?.queues[k] || k}</div>
            </Link>
          ))}
        </div>
        {s.pending_refresh > 0 && <p className="small muted mt">{n(s.pending_refresh)} câu đang chờ bộ kiểm tra tự động (chạy nền mỗi 5 phút).</p>}
      </section>

      {Object.keys(s.findings).length > 0 && (
        <section className="card mt">
          <h3>Phát hiện của bộ kiểm tra tự động (đang mở)</h3>
          <table className="data small"><tbody>
            {Object.entries(s.findings).sort((a, b) => b[1] - a[1]).map(([code, c]) => (
              <tr key={code}><td>{meta?.validators[code] || code}</td><td className="mono">{code}</td><td>{n(c)}</td></tr>
            ))}
          </tbody></table>
        </section>
      )}

      <section className="card mt">
        <h3>Đợt giao việc</h3>
        {asg.data?.items.length ? (
          <table className="data small"><thead><tr><th>Đợt</th><th>Người duyệt</th><th>Số câu</th><th>Đã xong</th><th>Ngày giao</th></tr></thead>
            <tbody>{asg.data.items.map((a) => (
              <tr key={a.id}><td><Link to={`/admin/review/queue?assignment=${a.id}`}>{a.label}</Link></td><td>{a.reviewer.name}</td>
                <td>{n(a.total)}</td><td>{n(a.done)}</td><td>{a.created_at.slice(0, 10)}</td></tr>
            ))}</tbody></table>
        ) : <div className="muted small">Chưa có đợt giao việc. Vào hàng đợi, lọc rồi bấm “Giao theo bộ lọc…”.</div>}
      </section>
    </div>
  );
}
