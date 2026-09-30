import { Link } from "react-router-dom";
import { get } from "../../lib/api";
import { dateTime, num, pct, STATE_LABELS, vnd } from "../../lib/format";
import { ErrorBox, Spinner, useAsync } from "../../components/ui";
import { PageHead, StateBadge } from "./common";

interface OverviewData {
  users: { total: number; new_24h: number; active_24h: number };
  sessions: { total: number; submitted: number; in_progress: number; last_24h: number; per_day: { day: string; mode: string; n: number }[] };
  questions: { total: number; by_state: Record<string, number>; served: number; served_by_subject: Record<string, number>; overridden: number };
  reports: { open: number };
  payments: { revenue_vnd: number; revenue_30d_vnd: number; paid_orders: number; pending_orders: number; unmatched_transactions: number };
  last_sync: { id: number; status: string; started_at: string; finished_at: string | null; stats: Record<string, number> } | null;
}
interface ExamStats {
  blueprints: { id: number; name: string; submitted: number; avg_ratio: number | null }[];
  practice: { submitted: number; avg_ratio: number | null };
}

function Stat({ k, v, sub, to }: { k: string; v: string | number; sub?: string; to?: string }) {
  const body = (
    <div className="card stat">
      <span className="k">{k}</span>
      <span className="v">{typeof v === "number" ? num(v, 0) : v}</span>
      {sub && <span className="small muted">{sub}</span>}
    </div>
  );
  return to ? <Link to={to} style={{ color: "inherit", textDecoration: "none" }}>{body}</Link> : body;
}

export default function Overview() {
  const ov = useAsync(() => get<OverviewData>("/api/admin/overview"), []);
  const ex = useAsync(() => get<ExamStats>("/api/admin/stats/exams"), []);
  if (ov.loading) return <Spinner />;
  if (ov.error || !ov.data) return <ErrorBox error={ov.error} />;
  const d = ov.data;
  const days = new Map<string, { exam: number; practice: number }>();
  for (const r of d.sessions.per_day) {
    const x = days.get(r.day) || { exam: 0, practice: 0 };
    if (r.mode === "exam") x.exam += r.n; else x.practice += r.n;
    days.set(r.day, x);
  }
  const maxDay = Math.max(1, ...[...days.values()].map((x) => x.exam + x.practice));
  const maxSubj = Math.max(1, ...Object.values(d.questions.served_by_subject));
  return (
    <div className="stack">
      <PageHead title="Tổng quan" />
      <div className="grid cols-4">
        <Stat k="Người dùng" v={d.users.total} sub={`+${d.users.new_24h} mới · ${d.users.active_24h} hoạt động 24h`} to="/admin/nguoi-dung" />
        <Stat k="Lượt làm bài" v={d.sessions.total} sub={`${d.sessions.submitted} đã nộp · ${d.sessions.in_progress} đang làm`} />
        <Stat k="Câu đang phục vụ" v={d.questions.served} sub={`trên ${num(d.questions.total, 0)} câu · ${d.questions.overridden} ghi đè`} to="/admin/cau-hoi?served=yes" />
        <Stat k="Báo lỗi mở" v={d.reports.open} to="/admin/bao-loi" />
        <Stat k="Doanh thu" v={vnd(d.payments.revenue_vnd)} sub={`30 ngày: ${vnd(d.payments.revenue_30d_vnd)}`} />
        <Stat k="Đơn đã thanh toán" v={d.payments.paid_orders} to="/admin/don-hang?status=paid" />
        <Stat k="Đơn chờ thanh toán" v={d.payments.pending_orders} to="/admin/don-hang?status=pending" />
        <Stat k="Giao dịch cần đối soát" v={d.payments.unmatched_transactions} to="/admin/giao-dich?status=unmatched" />
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Câu hỏi theo trạng thái biên tập</h3>
          <div className="table-wrap"><table className="data"><tbody>
            {Object.entries(d.questions.by_state).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
              <tr key={s}><td><Link to={`/admin/cau-hoi?state=${s}`}><StateBadge state={s} /></Link></td>
                <td className="small muted">{STATE_LABELS[s] ? s : ""}</td><td style={{ textAlign: "right" }}>{num(n, 0)}</td></tr>
            ))}
          </tbody></table></div>
        </div>
        <div className="card">
          <h3>Câu đang phục vụ theo môn</h3>
          <div className="stack" style={{ gap: 8 }}>
            {Object.entries(d.questions.served_by_subject).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
              <div key={s}>
                <div className="row between small"><span>{s || "(chưa gán)"}</span><span>{num(n, 0)}</span></div>
                <div className="progress"><span style={{ width: `${(n / maxSubj) * 100}%` }} /></div>
              </div>
            ))}
            {!Object.keys(d.questions.served_by_subject).length && <div className="muted small">Chưa có câu nào được phục vụ.</div>}
          </div>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Lượt làm bài 30 ngày</h3>
          {days.size === 0 ? <div className="muted small">Chưa có dữ liệu.</div> : (
            <div className="stack" style={{ gap: 6 }}>
              {[...days.entries()].map(([day, x]) => (
                <div key={day} className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                  <span className="small mono" style={{ width: 90 }}>{day}</span>
                  <div style={{ flex: 1, display: "flex", height: 12, background: "#eef2f7", borderRadius: 6, overflow: "hidden" }}>
                    <span style={{ width: `${(x.exam / maxDay) * 100}%`, background: "var(--brand)" }} title={`Thi thử: ${x.exam}`} />
                    <span style={{ width: `${(x.practice / maxDay) * 100}%`, background: "#93b1ff" }} title={`Luyện tập: ${x.practice}`} />
                  </div>
                  <span className="small" style={{ width: 40, textAlign: "right" }}>{x.exam + x.practice}</span>
                </div>
              ))}
              <div className="small muted">■ <span style={{ color: "var(--brand)" }}>Thi thử</span> · ■ <span style={{ color: "#93b1ff" }}>Luyện tập</span></div>
            </div>
          )}
        </div>
        <div className="card">
          <h3>Đồng bộ gần nhất</h3>
          {d.last_sync ? (
            <dl className="kv">
              <dt>Lần chạy</dt><dd>#{d.last_sync.id} · {d.last_sync.status}</dd>
              <dt>Bắt đầu</dt><dd>{dateTime(d.last_sync.started_at)}</dd>
              <dt>Kết thúc</dt><dd>{dateTime(d.last_sync.finished_at)}</dd>
              <dt>Đã xem / thay đổi</dt><dd>{num(d.last_sync.stats?.seen ?? 0, 0)} / {num(d.last_sync.stats?.content_changed ?? 0, 0)}</dd>
              <dt>Phiên bản mới</dt><dd>{num(d.last_sync.stats?.versions_created ?? 0, 0)}</dd>
            </dl>
          ) : <div className="muted small">Chưa đồng bộ lần nào.</div>}
          <Link to="/admin/ngan-hang" className="btn secondary sm mt">Quản lý đồng bộ</Link>
        </div>
      </div>

      <div className="card">
        <h3>Thống kê đề thi</h3>
        {ex.loading ? <Spinner /> : ex.error ? <ErrorBox error={ex.error} /> : ex.data && (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Đề</th><th>Lượt nộp</th><th>Tỉ lệ điểm TB</th></tr></thead>
            <tbody>
              {ex.data.blueprints.map((b) => (
                <tr key={b.id}><td><Link to={`/admin/de-thi/${b.id}`}>{b.name}</Link></td><td>{b.submitted}</td><td>{pct(b.avg_ratio)}</td></tr>
              ))}
              <tr><td><em>Luyện tập tự do</em></td><td>{ex.data.practice.submitted}</td><td>{pct(ex.data.practice.avg_ratio)}</td></tr>
            </tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
