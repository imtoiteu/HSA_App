import { useMemo, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { AnswerInput, Passage, Solution } from "../components/QuestionBody";
import { ReportDialog } from "../components/ReportDialog";
import { RichContent } from "../components/RichContent";
import { ErrorBox, Icon, Spinner, useAsync, useToast } from "../components/ui";
import { del, get, put } from "../lib/api";
import { dateTime, duration, num } from "../lib/format";
import type { SessionData, SessionItem } from "../lib/types";

const OUTCOME: Record<string, [string, string]> = {
  correct: ["Đúng", "ok"],
  incorrect: ["Sai", "bad"],
  partial: ["Đúng một phần", "warn"],
  unanswered: ["Bỏ trống", ""],
  ungraded: ["Tự đánh giá", "warn"],
};

type Filter = "all" | "wrong" | "blank" | "right" | "flag";

export default function Result() {
  const { id = "" } = useParams();
  const { data, error, loading } = useAsync(() => get<SessionData>(`/api/sessions/${id}`), [id]);
  const [filter, setFilter] = useState<Filter>("all");
  const [items, setItems] = useState<SessionItem[] | null>(null);
  const [report, setReport] = useState<number | null>(null);
  const [limit, setLimit] = useState(20); // typeset the review progressively (150-question exams)
  const toast = useToast();
  const list = items ?? data?.items ?? [];

  const filtered = useMemo(() => list.filter((it) => {
    if (filter === "wrong") return it.outcome === "incorrect" || it.outcome === "partial";
    if (filter === "blank") return it.outcome === "unanswered";
    if (filter === "right") return it.outcome === "correct";
    if (filter === "flag") return it.flagged;
    return true;
  }), [list, filter]);

  if (loading) return <Spinner />;
  if (error) return <div className="container page"><ErrorBox error={error} /></div>;
  if (!data) return null;
  if (data.status === "in_progress") return <Navigate to={`/lam-bai/${id}`} replace />;

  const r = data.result;
  const c = r?.counts;
  const ratio = data.max_score ? (data.score ?? 0) / data.max_score : 0;
  const deg = Math.round(Math.max(0, ratio) * 360);
  const scaled = r?.scaled != null ? `${num(r.scaled)}` : null;

  const toggleBookmark = async (it: SessionItem) => {
    try {
      if (it.bookmarked) await del(`/api/bookmarks/${it.question_ref}`);
      else await put(`/api/bookmarks/${it.question_ref}`, {});
      setItems(list.map((x) => (x.question_ref === it.question_ref ? { ...x, bookmarked: !it.bookmarked } : x)));
      toast(it.bookmarked ? "Đã bỏ lưu." : "Đã lưu câu hỏi.", "ok");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="container page">
      <div className="row between mb">
        <div>
          <div className="muted small">{data.mode === "exam" ? "Kết quả bài thi" : "Kết quả luyện tập"}</div>
          <h1 style={{ marginBottom: 4 }}>{data.title}</h1>
          <div className="muted small">
            Nộp lúc {dateTime(data.submitted_at)}{data.submit_reason === "timeout" ? " (tự động khi hết giờ)" : ""}
            {r?.duration_seconds != null && <> · Thời gian làm: {duration(r.duration_seconds)}</>}
          </div>
        </div>
        <div className="row no-print">
          <Link to="/luyen-tap" className="btn secondary">Luyện tiếp</Link>
          {data.blueprint_id && <Link to="/de-thi" className="btn">Làm đề khác</Link>}
        </div>
      </div>

      {data.status === "abandoned" && <div className="alert warn mb">Bài làm này đã bị huỷ, không có kết quả.</div>}

      {c && (
        <div className="card pad-lg">
          <div className="score-hero">
            <div className="score-ring" style={{ background: `conic-gradient(var(--brand) ${deg}deg, #e8edf6 ${deg}deg)` }}
                 aria-label={`Điểm ${num(data.score)} trên ${num(data.max_score)}`}>
              <div className="inner">
                <div>
                  <div className="big">{scaled ?? num(data.score)}</div>
                  <div className="of">/ {r?.scale_to ? num(r.scale_to) : num(data.max_score)} điểm</div>
                </div>
              </div>
            </div>
            <div className="counts">
              <div className="count-box ok"><div className="v">{c.correct}</div>Câu đúng</div>
              <div className="count-box bad"><div className="v">{c.incorrect + c.partial}</div>Câu sai{c.partial ? ` (${c.partial} đúng một phần)` : ""}</div>
              <div className="count-box skip"><div className="v">{c.unanswered}</div>Bỏ trống</div>
              <div className="count-box grey"><div className="v">{c.ungraded}</div>Tự đánh giá</div>
            </div>
          </div>
          {scaled && <p className="muted small mt">Điểm thô {num(data.score)}/{num(data.max_score)} được quy đổi sang thang {num(r!.scale_to)}.</p>}
          {r && r.self_assessed && (r.self_assessed.correct + r.self_assessed.incorrect > 0) && (
            <p className="muted small">Câu tự đánh giá: {r.self_assessed.correct} đúng, {r.self_assessed.incorrect} sai (không tính vào điểm).</p>
          )}
          {r && r.sections.length > 1 && (
            <div className="table-wrap mt">
              <table className="data">
                <thead><tr><th>Phần thi</th><th>Điểm</th><th>Đúng</th><th>Sai</th><th>Bỏ trống</th></tr></thead>
                <tbody>
                  {r.sections.map((s) => (
                    <tr key={s.key}>
                      <td>{s.title}</td>
                      <td><strong>{num(s.score)}</strong>/{num(s.max_score)}</td>
                      <td>{s.counts.correct}</td>
                      <td>{s.counts.incorrect + s.counts.partial}</td>
                      <td>{s.counts.unanswered}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {data.allow_review && data.status === "submitted" ? (
        <div className="card mt-lg">
          <div className="row between">
            <h2 style={{ margin: 0 }}>Xem lại bài làm</h2>
            <div className="seg" role="tablist">
              {([["all", "Tất cả"], ["wrong", "Câu sai"], ["blank", "Bỏ trống"], ["right", "Câu đúng"], ["flag", "Đã đánh dấu"]] as [Filter, string][]).map(([k, v]) => (
                <button key={k} role="tab" aria-selected={filter === k} className={filter === k ? "on" : ""} onClick={() => { setFilter(k); setLimit(20); }}>{v}</button>
              ))}
            </div>
          </div>
          {filtered.length === 0 && <div className="empty">Không có câu nào trong mục này.</div>}
          {filtered.slice(0, limit).map((it) => {
            const [label, cls] = OUTCOME[it.outcome || "unanswered"] || ["", ""];
            const groupPositions = it.question.group ? list.filter((x) => x.question.group?.key === it.question.group!.key).map((x) => x.position) : undefined;
            const firstOfGroup = !groupPositions || groupPositions[0] === it.position || filter !== "all";
            return (
              <div className="review-item" key={it.position} id={`cau-${it.position}`}>
                {it.question.group && firstOfGroup && <Passage group={it.question.group} positions={groupPositions} lang={it.question.language} />}
                <div className="q-head">
                  <span className="q-num">Câu {it.position}</span>
                  <span className={"badge " + cls}>{label}</span>
                  {it.flagged && <span className="badge warn">Đã đánh dấu</span>}
                  <div className="q-actions no-print">
                    <button className={"icon-btn" + (it.bookmarked ? " flag-btn on" : "")} title="Lưu câu hỏi" aria-pressed={!!it.bookmarked} onClick={() => toggleBookmark(it)}><Icon name="bookmark" /></button>
                    <button className="icon-btn" title="Báo lỗi" onClick={() => setReport(it.question_ref)}><Icon name="report" /></button>
                  </div>
                </div>
                <RichContent blocks={it.question.stem} />
                <AnswerInput input={it.question.input} options={it.question.options} response={it.response} disabled onChange={() => {}}
                             reveal={{ answer: it.answer, response: it.response }} />
                <Solution q={it} answer={it.answer} answerDisplay={it.answer_display} />
              </div>
            );
          })}
          {filtered.length > limit && (
            <div className="center mt">
              <button className="btn secondary" onClick={() => setLimit((l) => l + 20)}>
                Xem thêm {Math.min(20, filtered.length - limit)} câu ({filtered.length - limit} câu còn lại)
              </button>
            </div>
          )}
        </div>
      ) : data.status === "submitted" ? (
        <div className="alert info mt">Đề thi này không cho xem lại đáp án.</div>
      ) : null}
      {report !== null && <ReportDialog questionRef={report} sessionId={id} onClose={() => setReport(null)} />}
    </div>
  );
}
