import { Link } from "react-router-dom";
import { get } from "../../lib/api";
import { dateTime, num, pct, STATE_LABELS, vnd } from "../../lib/format";
import { ErrorBox, Spinner, useAsync } from "../../components/ui";
import { PageHead } from "./common";

interface Dash {
  bank: { total: number; ready_to_serve: number; eligible: number; served: number; excluded: number; by_state: Record<string, number>;
    unclassified: number; subject_review: number; formula_review: number; visual_review: number; answer_linking: number; open_reports: number };
  users: { total: number; students: number; admins: number; pro_active: number; free: number; pro_expired: number; new_24h: number;
    new_7d: number; new_30d: number; active_7d: number; active_30d: number };
  practice: { sessions: number; sessions_30d: number; answered: number; answered_by_subject: Record<string, number>; sessions_by_plan: Record<string, number> };
  exams: { attempts: number; submitted: number; in_progress: number; abandoned: number; paid_attempts: number; free_attempts: number;
    attempts_30d: number; completion_rate: number | null };
  payments: { pending: number; paid: number; expired: number; cancelled: number; failed: number; refunded: number; revenue_vnd: number;
    revenue_30d_vnd: number; revenue_pro_vnd: number; revenue_exam_vnd: number; unmatched_transactions: number; bank_configured: boolean;
    account_holder_configured: boolean; payments_enabled: boolean };
  activity: { day: string; mode: string; n: number }[];
  config: { free_questions_per_subject: number; pro_price_vnd: number | null; pro_duration_days: number | null; pro_active: boolean;
    mock_exams: { paid_enabled: boolean; default_price_vnd: number; pro_includes_paid_exams: boolean } };
  last_sync: { id: number; status: string; finished_at: string | null } | null;
}

function K({ k, v, to, tone, sub }: { k: string; v: number | string; to?: string; tone?: string; sub?: string }) {
  const inner = <><span className="v">{typeof v === "number" ? num(v, 0) : v}</span><span className="k">{k}</span>{sub && <span className="k"> · {sub}</span>}</>;
  return to ? <Link to={to} className={"kpi " + (tone || "")}>{inner}</Link> : <div className={"kpi " + (tone || "")}>{inner}</div>;
}

export default function Overview() {
  const r = useAsync(() => get<Dash>("/api/admin/dashboard"), []);
  if (r.loading) return <Spinner />;
  if (r.error || !r.data) return <ErrorBox error={r.error} />;
  const d = r.data;
  const days = new Map<string, { exam: number; practice: number }>();
  for (const x of d.activity) {
    const e = days.get(x.day) || { exam: 0, practice: 0 };
    if (x.mode === "exam") e.exam += x.n; else e.practice += x.n;
    days.set(x.day, e);
  }
  const maxDay = Math.max(1, ...[...days.values()].map((x) => x.exam + x.practice));
  const p = d.payments;
  return (
    <div>
      <PageHead title="Tổng quan vận hành">
        <button className="btn ghost sm" onClick={r.reload}>Làm mới</button>
      </PageHead>

      {(!p.bank_configured || !p.account_holder_configured || !p.payments_enabled) && (
        <div className="alert warn mb">
          {!p.payments_enabled ? "Thanh toán đang tắt. " : ""}
          {!p.bank_configured ? "Chưa cấu hình tài khoản nhận tiền – học sinh chưa thể mua Pro hoặc đề thi. "
            : !p.account_holder_configured ? "Chưa nhập tên chủ tài khoản nhận tiền. " : ""}
          <Link to="/admin/cai-dat?tab=payment">Mở cài đặt thanh toán →</Link>
        </div>
      )}

      <section className="dash-section">
        <h2>Cấu hình kinh doanh hiện tại</h2>
        <div className="kpi-grid">
          <K k="Câu miễn phí mỗi môn" v={d.config.free_questions_per_subject} to="/admin/cai-dat?tab=practice" />
          <K k={`Giá Pro / ${d.config.pro_duration_days ?? "?"} ngày${d.config.pro_active ? "" : " (tạm ngừng)"}`} v={vnd(d.config.pro_price_vnd)} to="/admin/goi" />
          <K k="Giá đề thi mặc định" v={vnd(d.config.mock_exams.default_price_vnd)} to="/admin/cai-dat?tab=mock_exams"
             sub={d.config.mock_exams.paid_enabled ? "đang bán" : "tắt bán"} />
          <K k="Pro bao gồm đề có phí" v={d.config.mock_exams.pro_includes_paid_exams ? "Có" : "Không"} to="/admin/cai-dat?tab=mock_exams" />
        </div>
      </section>

      <section className="dash-section">
        <h2>Ngân hàng câu hỏi</h2>
        <div className="kpi-grid">
          <K k="Câu đã nhập" v={d.bank.total} to="/admin/cau-hoi" />
          <K k="READY_TO_SERVE" v={d.bank.ready_to_serve} to="/admin/cau-hoi?state=READY_TO_SERVE" />
          <K k="Đủ điều kiện" v={d.bank.eligible} to="/admin/cau-hoi?eligible=true" />
          <K k="Đang phục vụ" v={d.bank.served} tone="ok" to="/admin/cau-hoi?served=true" />
          <K k="Không phục vụ" v={d.bank.excluded} to="/admin/cau-hoi?served=false" />
          <K k="Chưa phân loại môn" v={d.bank.unclassified} to="/admin/cau-hoi?subject=general" />
          <K k="Cần xem lại môn" v={d.bank.subject_review} to="/admin/hang-doi-qa" />
          <K k="Cần duyệt công thức" v={d.bank.formula_review} to="/admin/cau-hoi?state=NEEDS_FORMULA_REVIEW" />
          <K k="Cần duyệt hình" v={d.bank.visual_review} to="/admin/cau-hoi?state=NEEDS_VISUAL_REVIEW" />
          <K k="Cần liên kết đáp án" v={d.bank.answer_linking} to="/admin/cau-hoi?state=NEEDS_ANSWER_LINKING" />
          <K k="Báo lỗi đang mở" v={d.bank.open_reports} tone={d.bank.open_reports ? "warn" : ""} to="/admin/bao-loi" />
        </div>
        <div className="small muted mt">Trạng thái: {Object.entries(d.bank.by_state).map(([s, n]) => `${STATE_LABELS[s] || s} ${num(n, 0)}`).join(" · ")}
          {d.last_sync && <> · đồng bộ gần nhất #{d.last_sync.id} {d.last_sync.status} {dateTime(d.last_sync.finished_at)}</>} · <Link to="/admin/doi-soat-mon">Đối soát theo môn →</Link></div>
      </section>

      <section className="dash-section">
        <h2>Người dùng</h2>
        <div className="kpi-grid">
          <K k="Tổng tài khoản" v={d.users.total} to="/admin/nguoi-dung" />
          <K k="Gói Miễn phí" v={d.users.free} to="/admin/nguoi-dung?plan=free" />
          <K k="Pro đang hiệu lực" v={d.users.pro_active} tone="ok" to="/admin/nguoi-dung?plan=pro" />
          <K k="Pro đã hết hạn" v={d.users.pro_expired} to="/admin/nguoi-dung?plan=expired" />
          <K k="Mới 24 giờ / 7 ngày" v={`${d.users.new_24h} / ${d.users.new_7d}`} />
          <K k="Hoạt động 7 / 30 ngày" v={`${d.users.active_7d} / ${d.users.active_30d}`} />
        </div>
      </section>

      <div className="grid cols-2">
        <section className="dash-section">
          <h2>Luyện tập</h2>
          <div className="kpi-grid">
            <K k="Bài luyện tập" v={d.practice.sessions} sub={`${d.practice.sessions_30d} trong 30 ngày`} />
            <K k="Câu đã trả lời" v={d.practice.answered} />
            <K k="Bài của gói Miễn phí" v={d.practice.sessions_by_plan.FREE || 0} />
            <K k="Bài của gói Pro" v={d.practice.sessions_by_plan.PRO || 0} />
          </div>
          <div className="small muted mt">Câu đã trả lời theo môn: {Object.entries(d.practice.answered_by_subject).map(([k, n]) => `${k} ${num(n, 0)}`).join(" · ") || "—"}
            {d.practice.sessions_by_plan["?"] ? ` · ${d.practice.sessions_by_plan["?"]} bài tạo trước khi ghi nhận gói` : ""}</div>
        </section>
        <section className="dash-section">
          <h2>Thi thử</h2>
          <div className="kpi-grid">
            <K k="Lượt thi" v={d.exams.attempts} sub={`${d.exams.attempts_30d} trong 30 ngày`} />
            <K k="Đã nộp" v={d.exams.submitted} sub={`hoàn thành ${pct(d.exams.completion_rate)}`} />
            <K k="Lượt miễn phí / có phí" v={`${d.exams.free_attempts} / ${d.exams.paid_attempts}`} />
            <K k="Đang làm / bỏ dở" v={`${d.exams.in_progress} / ${d.exams.abandoned}`} />
          </div>
        </section>
      </div>

      <section className="dash-section">
        <h2>Thanh toán</h2>
        <div className="kpi-grid">
          <K k="Chờ thanh toán" v={p.pending} tone={p.pending ? "warn" : ""} to="/admin/don-hang?status=pending" />
          <K k="Đã thanh toán" v={p.paid} tone="ok" to="/admin/don-hang?status=paid" />
          <K k="Hết hạn" v={p.expired} to="/admin/don-hang?status=expired" />
          <K k="Huỷ / thất bại" v={`${p.cancelled} / ${p.failed}`} to="/admin/don-hang?status=failed" />
          <K k="Hoàn tiền" v={p.refunded} to="/admin/don-hang?status=refunded" />
          <K k="Giao dịch cần đối soát" v={p.unmatched_transactions} tone={p.unmatched_transactions ? "bad" : ""} to="/admin/giao-dich" />
          <K k="Doanh thu" v={vnd(p.revenue_vnd)} sub={`30 ngày ${vnd(p.revenue_30d_vnd)}`} />
          <K k="Doanh thu Pro" v={vnd(p.revenue_pro_vnd)} />
          <K k="Doanh thu đề thi" v={vnd(p.revenue_exam_vnd)} />
        </div>
      </section>

      <section className="dash-section">
        <h2>Hoạt động 30 ngày</h2>
        {days.size === 0 ? <div className="muted small">Chưa có hoạt động.</div> : (
          <div className="card">
            <div className="row" style={{ alignItems: "flex-end", gap: 3, height: 120 }} aria-label="Số bài làm mỗi ngày">
              {[...days.entries()].map(([day, x]) => (
                <div key={day} title={`${day}: ${x.practice} luyện tập, ${x.exam} thi thử`} style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", height: "100%" }}>
                  <div style={{ height: `${(x.exam / maxDay) * 100}%`, background: "var(--accent)" }} />
                  <div style={{ height: `${(x.practice / maxDay) * 100}%`, background: "var(--brand)" }} />
                </div>
              ))}
            </div>
            <div className="small muted mt"><span style={{ color: "var(--brand)" }}>■</span> luyện tập · <span style={{ color: "var(--accent)" }}>■</span> thi thử</div>
          </div>
        )}
      </section>
    </div>
  );
}
