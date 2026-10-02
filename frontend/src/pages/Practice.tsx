import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PlanBanner, UpgradeState } from "../components/PlanBanner";
import { ErrorBox, Spinner, useAsync } from "../components/ui";
import { ApiError, get, post } from "../lib/api";
import { TYPE_LABELS } from "../lib/format";
import type { Catalog, SessionSummary } from "../lib/types";

const COUNTS = [10, 20, 30, 50];
const TIMES = [0, 15, 30, 45, 60, 90];
const SOURCES: [string, string, string][] = [
  ["all", "Ngẫu nhiên", "Chọn ngẫu nhiên từ ngân hàng câu hỏi đã kiểm duyệt"],
  ["unseen", "Câu chưa làm", "Ưu tiên câu bạn chưa gặp"],
  ["wrong", "Câu từng làm sai", "Ôn lại những câu bạn đã trả lời sai"],
  ["bookmarks", "Câu đã lưu", "Luyện lại các câu bạn đã lưu"],
];
const TYPES = ["single_choice", "numeric_response", "short_response", "true_false_statements", "error_identification"];

export default function Practice() {
  const { data: cat, error, loading } = useAsync(() => get<Catalog>("/api/catalog"), []);
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [subjects, setSubjects] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [count, setCount] = useState(20);
  const [minutes, setMinutes] = useState(0);
  const [feedback, setFeedback] = useState<"immediate" | "end">("immediate");
  const [source, setSource] = useState("all");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    const s = params.get("mon");
    if (s) setSubjects([s]);
    const src = params.get("nguon");
    if (src) setSource(src);
  }, [params]);

  if (loading) return <Spinner />;
  if (error || !cat) return <div className="container page"><ErrorBox error={error} /></div>;

  const free = cat.access.plan === "FREE";
  const limitErr = err instanceof ApiError && err.code === "free_limit" ? err : null;
  const toggle = (arr: string[], v: string, set: (x: string[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const available = cat.subjects.filter((s) => s.available > 0);
  const pool = subjects.length ? available.filter((s) => subjects.includes(s.code)).reduce((a, s) => a + s.available, 0)
    : available.reduce((a, s) => a + s.available, 0);

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ session: SessionSummary }>("/api/sessions", {
        subjects, types, count, time_limit_minutes: minutes || null, feedback, source,
      });
      nav(`/lam-bai/${r.session.id}`);
    } catch (e) {
      setErr(e);
      setBusy(false);
    }
  };

  return (
    <div className="container narrow page">
      <h1>Luyện tập</h1>
      <p className="muted">Tạo một bài luyện tập theo ý bạn. Chỉ các câu hỏi đã được kiểm duyệt đáp án mới được dùng.</p>
      <PlanBanner access={cat.access} />

      <div className="card pad-lg stack" style={{ gap: 22 }}>
        <section>
          <div className="label mb">1. Nguồn câu hỏi</div>
          <div className="grid cols-2">
            {SOURCES.map(([k, t, d]) => (
              <button key={k} type="button" className={"card flat card-link"} aria-pressed={source === k} onClick={() => setSource(k)}
                      style={{ textAlign: "left", cursor: "pointer", borderColor: source === k ? "var(--brand)" : undefined,
                               background: source === k ? "var(--brand-50)" : undefined, padding: 14 }}>
                <strong>{t}</strong>
                <div className="muted small">{d}</div>
              </button>
            ))}
          </div>
        </section>

        <section>
          <div className="row between mb">
            <div className="label">2. Môn học</div>
            <button className="btn ghost sm" onClick={() => setSubjects([])}>{subjects.length ? "Bỏ chọn (tất cả môn)" : "Đang chọn: tất cả môn"}</button>
          </div>
          <div className="chip-group">
            {available.map((s) => (
              <button key={s.code} type="button" className={"chip" + (subjects.includes(s.code) ? " on" : "")}
                      aria-pressed={subjects.includes(s.code)} onClick={() => toggle(subjects, s.code, setSubjects)}>
                <span className="dot" style={{ background: s.color || "#94a3b8" }} />
                {s.short_name || s.name}{" "}
                <small title={free ? `Gói Miễn phí: ${s.available} / ${s.total} câu của môn` : undefined}>
                  {s.available.toLocaleString("vi-VN")}{free && s.total > s.available ? <span className="muted"> / {s.total.toLocaleString("vi-VN")}</span> : null}
                </small>
              </button>
            ))}
          </div>
          {!cat.topics_enabled && (
            <div className="hint mt">Luyện theo chuyên đề sẽ mở khi ngân hàng câu hỏi được phân loại chuyên đề đầy đủ.</div>
          )}
        </section>

        <section>
          <div className="label mb">3. Dạng câu hỏi <span className="muted small">(bỏ trống = mọi dạng)</span></div>
          <div className="chip-group">
            {TYPES.map((t) => (
              <button key={t} type="button" className={"chip" + (types.includes(t) ? " on" : "")} aria-pressed={types.includes(t)}
                      onClick={() => toggle(types, t, setTypes)}>{TYPE_LABELS[t]}</button>
            ))}
          </div>
        </section>

        <section className="grid cols-2">
          <div>
            <div className="label mb">4. Số câu</div>
            <div className="seg">
              {COUNTS.filter((n) => n <= cat.practice.max_questions).map((n) => (
                <button key={n} className={count === n ? "on" : ""} onClick={() => setCount(n)}>{n}</button>
              ))}
            </div>
          </div>
          <div>
            <div className="label mb">5. Thời gian</div>
            <select className="input" value={minutes} onChange={(e) => setMinutes(+e.target.value)} aria-label="Giới hạn thời gian">
              {TIMES.map((m) => <option key={m} value={m}>{m ? `${m} phút` : "Không giới hạn"}</option>)}
            </select>
          </div>
        </section>

        <section>
          <div className="label mb">6. Cách xem đáp án</div>
          <div className="seg">
            <button className={feedback === "immediate" ? "on" : ""} onClick={() => setFeedback("immediate")}>Xem ngay sau mỗi câu</button>
            <button className={feedback === "end" ? "on" : ""} onClick={() => setFeedback("end")}>Xem khi nộp bài</button>
          </div>
        </section>

        {limitErr ? <UpgradeState limit={limitErr.data.limit as number} message={limitErr.message} /> : <ErrorBox error={err} />}
        <div className="row between">
          <span className="muted small">
            {free ? "Câu bạn được luyện" : "Kho câu phù hợp"}: khoảng {pool.toLocaleString("vi-VN")} câu
          </span>
          <button className="btn lg" onClick={start} disabled={busy || pool === 0}>{busy ? "Đang tạo bài…" : "Bắt đầu luyện tập"}</button>
        </div>
      </div>
    </div>
  );
}
