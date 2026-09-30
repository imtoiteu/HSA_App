import { useState } from "react";
import { Link } from "react-router-dom";
import { ErrorBox, Spinner, useAsync } from "../components/ui";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import { clock, dateTime, num, pct } from "../lib/format";
import type { Blueprint, Catalog, SessionSummary } from "../lib/types";
import { ExamCard, StartExam } from "./Exams";

interface Dash {
  in_progress: SessionSummary[];
  recent: SessionSummary[];
  submitted_total: number;
  accuracy: number | null;
  answered_total: number;
  by_subject: { code: string; name: string; color: string | null; questions: number; correct: number; attempted: number; accuracy: number | null }[];
  bookmarks: number;
}

export function SessionRow({ s }: { s: SessionSummary }) {
  const done = s.status === "submitted";
  const left = s.deadline_at ? (new Date(s.deadline_at).getTime() - Date.now()) / 1000 : null;
  return (
    <Link to={done ? `/ket-qua/${s.id}` : `/lam-bai/${s.id}`} className="card flat card-link row between" style={{ padding: 14 }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{s.title}</div>
        <div className="muted small">
          {s.mode === "exam" ? "Đề thi thử" : "Luyện tập"} · {dateTime(done ? s.submitted_at : s.started_at)}
          {!done && ` · đã làm ${s.answered}/${s.total}`}
          {!done && left !== null && left > 0 && ` · còn ${clock(left)}`}
        </div>
      </div>
      {done ? (
        <span className="badge brand" style={{ fontSize: ".9rem" }}>
          {s.scaled != null ? num(s.scaled) : `${num(s.score)}/${num(s.max_score)}`}
        </span>
      ) : <span className="btn sm">Làm tiếp</span>}
    </Link>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const d = useAsync(() => get<Dash>("/api/me/dashboard"), []);
  const c = useAsync(() => get<Catalog>("/api/catalog"), []);
  const [open, setOpen] = useState<Blueprint | null>(null);
  if (d.loading || c.loading) return <Spinner />;
  if (d.error || c.error) return <div className="container page"><ErrorBox error={d.error || c.error} /></div>;
  const dash = d.data!, cat = c.data!;
  return (
    <div className="container page">
      <h1>Chào {user?.display_name}! 👋</h1>
      <p className="muted">Hôm nay bạn muốn luyện gì?</p>
      {cat.site.announcement && <div className="alert info mb">{cat.site.announcement}</div>}

      <div className="grid cols-4">
        <div className="card stat"><span className="v">{dash.submitted_total}</span><span className="k">Bài đã hoàn thành</span></div>
        <div className="card stat"><span className="v">{dash.answered_total.toLocaleString("vi-VN")}</span><span className="k">Câu đã làm</span></div>
        <div className="card stat"><span className="v">{pct(dash.accuracy)}</span><span className="k">Tỉ lệ đúng</span></div>
        <Link to="/cau-hoi-da-luu" className="card stat card-link"><span className="v">{dash.bookmarks}</span><span className="k">Câu đã lưu</span></Link>
      </div>

      {dash.in_progress.length > 0 && (
        <section className="mt-lg">
          <h2>Đang làm dở</h2>
          <div className="stack">{dash.in_progress.map((s) => <SessionRow key={s.id} s={s} />)}</div>
        </section>
      )}

      <section className="mt-lg">
        <div className="row between"><h2>Luyện nhanh theo môn</h2><Link to="/luyen-tap">Tuỳ chỉnh bài luyện →</Link></div>
        <div className="grid cols-4">
          {cat.subjects.filter((s) => s.available > 0).map((s) => {
            const st = dash.by_subject.find((x) => x.code === s.code);
            return (
              <Link key={s.code} to={`/luyen-tap?mon=${s.code}`} className="card card-link" style={{ borderTop: `4px solid ${s.color || "#94a3b8"}` }}>
                <strong>{s.short_name || s.name}</strong>
                <div className="muted small">{s.available.toLocaleString("vi-VN")} câu</div>
                {st && st.attempted > 0 && (
                  <>
                    <div className="progress mt"><span style={{ width: pct(st.accuracy), background: s.color || undefined }} /></div>
                    <div className="muted small">Đúng {pct(st.accuracy)} · {st.attempted} câu</div>
                  </>
                )}
              </Link>
            );
          })}
        </div>
      </section>

      <section className="mt-lg">
        <div className="row between"><h2>Đề thi thử</h2><Link to="/de-thi">Tất cả đề →</Link></div>
        <div className="grid cols-2">{cat.blueprints.slice(0, 4).map((bp) => <ExamCard key={bp.id} bp={bp} onOpen={setOpen} />)}</div>
      </section>

      <section className="mt-lg">
        <div className="row between"><h2>Kết quả gần đây</h2><Link to="/lich-su">Xem lịch sử →</Link></div>
        {dash.recent.length ? <div className="stack">{dash.recent.map((s) => <SessionRow key={s.id} s={s} />)}</div>
          : <div className="card empty">Bạn chưa hoàn thành bài nào. <Link to="/luyen-tap">Bắt đầu luyện tập</Link></div>}
      </section>
      {open && <StartExam bp={open} onClose={() => { setOpen(null); c.reload(); }} />}
    </div>
  );
}
