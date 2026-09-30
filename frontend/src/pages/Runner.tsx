import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnswerInput, Passage, Solution } from "../components/QuestionBody";
import { ReportDialog } from "../components/ReportDialog";
import { RichContent } from "../components/RichContent";
import { Confirm, ErrorBox, Icon, Spinner, useToast } from "../components/ui";
import { ApiError, api, del, get, post, put } from "../lib/api";
import { clock } from "../lib/format";
import type { Response, SessionData, SessionItem } from "../lib/types";

type Change = { position: number; response?: Response; flagged?: boolean; time_ms?: number };
type SaveState = "saved" | "saving" | "pending" | "offline" | "error";

const pendingKey = (id: string) => `hsa:pending:${id}`;
const isAnswered = (r: Response) =>
  !!r && ((r.labels && r.labels.length > 0) || (r.values && r.values.some((v) => v !== null)) ||
    (typeof r.value === "string" ? r.value.trim() !== "" : r.value != null) || (r.text != null && r.text.trim() !== ""));

function loadPending(id: string): Record<number, Change> {
  try {
    return JSON.parse(localStorage.getItem(pendingKey(id)) || "{}");
  } catch {
    return {};
  }
}

export default function Runner() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const [data, setData] = useState<SessionData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pos, setPos] = useState(1);
  const [saveState, setSaveState] = useState<SaveState>("saved");
  const [confirm, setConfirm] = useState<"submit" | "section" | null>(null);
  const [busy, setBusy] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [report, setReport] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const pending = useRef<Record<number, Change>>({});
  const offset = useRef(0);
  const shownAt = useRef(Date.now());
  const flushing = useRef(false);
  const submitting = useRef(false);

  // ---------------------------------------------------------------- load
  const load = useCallback(async () => {
    try {
      const s = await get<SessionData>(`/api/sessions/${id}`);
      if (s.status !== "in_progress") {
        localStorage.removeItem(pendingKey(id));
        nav(`/ket-qua/${id}`, { replace: true });
        return;
      }
      offset.current = new Date(s.server_now).getTime() - Date.now();
      // re-apply answers that were not yet acknowledged by the server (offline / closed tab)
      pending.current = loadPending(id);
      const items = s.items.map((it) => {
        const p = pending.current[it.position];
        if (!p) return it;
        return { ...it, ...(p.response !== undefined ? { response: p.response } : {}), ...(p.flagged !== undefined ? { flagged: p.flagged } : {}) };
      });
      setData({ ...s, items });
      setPos((cur) => (items.some((i) => i.position === cur) ? cur : items.find((i) => i.section_index === s.current_section)?.position ?? items[0]?.position ?? 1));
      if (Object.keys(pending.current).length) setSaveState("pending");
    } catch (e) {
      setError(e);
    }
  }, [id, nav]);

  useEffect(() => { load(); }, [load]);

  // ---------------------------------------------------------------- saving
  const persistPending = () => {
    try { localStorage.setItem(pendingKey(id), JSON.stringify(pending.current)); } catch { /* storage full/blocked */ }
  };

  const takeTime = useCallback(() => {
    const t = Date.now() - shownAt.current;
    shownAt.current = Date.now();
    if (t > 800 && t < 3_600_000) {
      const c = pending.current[pos] || { position: pos };
      c.time_ms = (c.time_ms || 0) + t;
      pending.current[pos] = c;
    }
  }, [pos]);

  const flush = useCallback(async (keepalive = false) => {
    if (flushing.current) return;
    const changes = Object.values(pending.current);
    if (!changes.length) { setSaveState("saved"); return; }
    flushing.current = true;
    setSaveState("saving");
    const sent = { ...pending.current };
    try {
      const r = await api<{ status: string; deadline_at: string | null; server_now: string; current_section: number }>(
        `/api/sessions/${id}/answers`, { method: "PATCH", body: { changes }, keepalive });
      for (const [k, v] of Object.entries(sent)) {
        if (pending.current[+k] === v) delete pending.current[+k];
      }
      persistPending();
      offset.current = new Date(r.server_now).getTime() - Date.now();
      setSaveState(Object.keys(pending.current).length ? "pending" : "saved");
      setData((d) => (d && (d.deadline_at !== r.deadline_at || d.current_section !== r.current_section)) ? { ...d, deadline_at: r.deadline_at } : d);
      if (r.status !== "in_progress" || (data && r.current_section !== data.current_section)) load();
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 0) setSaveState("offline");
      else if (err.status === 409) {
        // time is up, section changed or the item is locked: server state wins
        for (const k of Object.keys(sent)) delete pending.current[+k];
        persistPending();
        setSaveState("saved");
        toast(err.message, "error");
        load();
      } else setSaveState("error");
    } finally {
      flushing.current = false;
    }
  }, [id, toast, load, data]);

  const change = (c: Omit<Change, "time_ms">) => {
    const prev = pending.current[c.position] || { position: c.position };
    pending.current[c.position] = { ...prev, ...c };
    persistPending();
    setSaveState("pending");
    setData((d) => d && {
      ...d,
      items: d.items.map((it) => it.position === c.position
        ? { ...it, ...(c.response !== undefined ? { response: c.response } : {}), ...(c.flagged !== undefined ? { flagged: c.flagged } : {}) }
        : it),
    });
  };

  // debounce answer saves; periodic flush for time tracking; flush when leaving
  useEffect(() => {
    if (saveState !== "pending") return;
    const t = setTimeout(() => flush(), 900);
    return () => clearTimeout(t);
  }, [saveState, flush, data]);
  useEffect(() => {
    const t = setInterval(() => { takeTime(); flush(); }, 20000);
    const onHide = () => { if (document.visibilityState === "hidden") { takeTime(); flush(true); } };
    const onOnline = () => flush();
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("online", onOnline);
    window.addEventListener("pagehide", onHide);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("pagehide", onHide);
    };
  }, [flush, takeTime]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (Object.keys(pending.current).length) { e.preventDefault(); e.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  // ---------------------------------------------------------------- timer
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const remaining = data?.deadline_at ? (new Date(data.deadline_at).getTime() - (now + offset.current)) / 1000 : null;

  const doSubmit = useCallback(async (auto = false) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    try {
      takeTime();
      await flush();
      await post(`/api/sessions/${id}/submit`);
      localStorage.removeItem(pendingKey(id));
      if (auto) toast("Hết giờ! Bài làm đã được nộp tự động.", "info");
      nav(`/ket-qua/${id}`, { replace: true });
    } catch (e) {
      toast((e as Error).message, "error");
      submitting.current = false;
      setBusy(false);
    }
  }, [flush, id, nav, takeTime, toast]);

  const nextSection = useCallback(async (auto = false) => {
    setBusy(true);
    try {
      takeTime();
      await flush();
      if (!auto) await post(`/api/sessions/${id}/next-section`);
      setConfirm(null);
      await load();
      if (auto) toast("Hết giờ phần thi. Chuyển sang phần tiếp theo.", "info");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }, [flush, id, load, takeTime, toast]);

  useEffect(() => {
    if (remaining === null || !data || remaining > 0 || submitting.current || busy) return;
    const lastSection = data.current_section >= data.sections.length - 1;
    if (data.timing === "per_section" && !lastSection) nextSection(true);
    else doSubmit(true);
  }, [remaining, data, doSubmit, nextSection, busy]);

  // ---------------------------------------------------------------- navigation
  const visible = useMemo(() => {
    if (!data) return [] as SessionItem[];
    return data.timing === "per_section" ? data.items.filter((i) => i.section_index === data.current_section) : data.items;
  }, [data]);
  const idx = visible.findIndex((i) => i.position === pos);
  const item = visible[idx] ?? visible[0];
  const go = useCallback((p: number) => {
    takeTime();
    setPos(p);
    setPaletteOpen(false);
    window.scrollTo({ top: 0 });
  }, [takeTime]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey || confirm || report !== null) return;
      if (e.key === "ArrowRight" && idx < visible.length - 1) go(visible[idx + 1].position);
      else if (e.key === "ArrowLeft" && idx > 0) go(visible[idx - 1].position);
      else if (item && item.question.input.kind === "choice" && !item.checked && /^[a-hA-H]$/.test(e.key)) {
        const opt = item.question.options.find((o) => o.display === e.key.toUpperCase());
        if (opt && !(item.question.input as any).multi) {
          const cur = item.response?.labels?.[0];
          change({ position: item.position, response: cur === opt.key ? null : { labels: [opt.key] } });
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ---------------------------------------------------------------- per-item actions
  const check = async () => {
    if (!item) return;
    setBusy(true);
    try {
      await flush();
      const r = await post<SessionItem>(`/api/sessions/${id}/check/${item.position}`);
      setData((d) => d && { ...d, items: d.items.map((it) => (it.position === r.position ? { ...it, ...r } : it)) });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const selfAssess = async (verdict: "correct" | "incorrect") => {
    if (!item) return;
    try {
      await post(`/api/sessions/${id}/self-assess/${item.position}`, { verdict });
      setData((d) => d && { ...d, items: d.items.map((it) => (it.position === item.position ? { ...it, self_assessment: verdict } : it)) });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const toggleBookmark = async () => {
    if (!item) return;
    try {
      if (item.bookmarked) await del(`/api/bookmarks/${item.question_ref}`);
      else await put(`/api/bookmarks/${item.question_ref}`, {});
      setData((d) => d && { ...d, items: d.items.map((it) => (it.question_ref === item.question_ref ? { ...it, bookmarked: !item.bookmarked } : it)) });
      toast(item.bookmarked ? "Đã bỏ lưu câu hỏi." : "Đã lưu câu hỏi để ôn lại.", "ok");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  if (error) return <div className="container page"><ErrorBox error={error} /><Link to="/tong-quan" className="btn mt">Về trang tổng quan</Link></div>;
  if (!data || !item) return <Spinner />;

  const answered = visible.filter((i) => isAnswered(i.response)).length;
  const flagged = visible.filter((i) => i.flagged).length;
  const section = data.sections[item.section_index];
  const groupPositions = item.question.group
    ? data.items.filter((i) => i.question.group?.key === item.question.group!.key).map((i) => i.position) : undefined;
  const lastSection = data.current_section >= data.sections.length - 1;
  const perSectionNext = data.timing === "per_section" && !lastSection;
  const locked = item.checked;
  const revealed = item.checked && (item.answer !== undefined);

  return (
    <div className="runner">
      <div className="runner-top">
        <Link to="/tong-quan" className="icon-btn" aria-label="Thoát (bài làm được lưu)" title="Thoát — bài làm được lưu tự động"><Icon name="left" /></Link>
        <div className="title">
          {data.title}
          {data.sections.length > 1 && <span className="muted small"> · {section?.title}</span>}
        </div>
        <span className={"save-state" + (saveState === "error" || saveState === "offline" ? " err" : "")} aria-live="polite">
          {{ saved: "✓ Đã lưu", saving: "Đang lưu…", pending: "Đang lưu…", offline: "Mất kết nối – sẽ lưu lại", error: "Lỗi lưu – đang thử lại" }[saveState]}
        </span>
        {remaining !== null && (
          <span className={"timer" + (remaining < 300 ? " low" : "")} role="timer" aria-label="Thời gian còn lại">
            <Icon name="clock" size={18} />{clock(remaining)}
          </span>
        )}
        <button className="btn sm palette-toggle secondary" onClick={() => setPaletteOpen((o) => !o)} aria-expanded={paletteOpen}>
          <Icon name="grid" size={16} /> {answered}/{visible.length}
        </button>
      </div>

      <div className="runner-body">
        <article className="q-card" aria-labelledby="qnum">
          {saveState === "offline" && (
            <div className="alert warn mb" role="status">
              Mất kết nối mạng. Câu trả lời vẫn được giữ trên máy này và sẽ tự lưu khi có mạng trở lại.
            </div>
          )}
          {item.question.group && <Passage group={item.question.group} positions={groupPositions} lang={item.question.language} />}
          <div className="q-head">
            <span className="q-num" id="qnum">Câu {item.position}</span>
            {item.scoring_mode === "self_check" && <span className="badge warn" title="Câu này bạn tự đối chiếu với đáp án">Tự đánh giá</span>}
            <div className="q-actions">
              <button className={"icon-btn flag-btn" + (item.flagged ? " on" : "")} title="Đánh dấu xem lại" aria-pressed={item.flagged}
                      onClick={() => change({ position: item.position, flagged: !item.flagged })}><Icon name="flag" /></button>
              <button className={"icon-btn" + (item.bookmarked ? " flag-btn on" : "")} title="Lưu câu hỏi" aria-pressed={!!item.bookmarked} onClick={toggleBookmark}><Icon name="bookmark" /></button>
              <button className="icon-btn" title="Báo lỗi câu hỏi" onClick={() => setReport(item.question_ref)}><Icon name="report" /></button>
            </div>
          </div>
          <RichContent blocks={item.question.stem} />
          <AnswerInput input={item.question.input} options={item.question.options} response={item.response}
                       disabled={locked} onChange={(r) => change({ position: item.position, response: r })}
                       reveal={revealed ? { answer: item.answer, response: item.response } : undefined} />

          {data.feedback === "immediate" && !item.checked && (
            <div className="row mt">
              <button className="btn secondary" onClick={check} disabled={busy}>
                {isAnswered(item.response) ? "Kiểm tra đáp án" : "Xem đáp án & lời giải"}
              </button>
            </div>
          )}
          {revealed && (
            <>
              {item.outcome && item.scoring_mode === "auto" && (
                <div className={"alert mt " + (item.outcome === "correct" ? "ok" : item.outcome === "unanswered" ? "info" : "error")}>
                  {item.outcome === "correct" ? "Chính xác! 🎉" : item.outcome === "unanswered" ? "Bạn chưa trả lời câu này." : "Chưa đúng."}
                </div>
              )}
              <Solution q={item} answer={item.answer} answerDisplay={item.answer_display} />
              {item.scoring_mode === "self_check" && (
                <div className="row mt">
                  <span className="label">Bạn tự đánh giá:</span>
                  <button className={"btn sm " + (item.self_assessment === "correct" ? "ok" : "secondary")} onClick={() => selfAssess("correct")}>Mình làm đúng</button>
                  <button className={"btn sm " + (item.self_assessment === "incorrect" ? "danger" : "secondary")} onClick={() => selfAssess("incorrect")}>Mình làm sai</button>
                </div>
              )}
            </>
          )}
        </article>

        <aside className={"side" + (paletteOpen ? " open" : "")} onClick={(e) => { if (e.target === e.currentTarget) setPaletteOpen(false); }}>
          <div className="palette-card">
            <div className="row between mb">
              <strong>Danh sách câu</strong>
              <span className="muted small">{answered}/{visible.length} đã làm{flagged ? ` · ${flagged} đánh dấu` : ""}</span>
            </div>
            {data.sections.map((sec) => {
              const its = data.items.filter((i) => i.section_index === sec.index);
              if (!its.length && sec.state !== "locked") return null;
              return (
                <div className="palette-sec" key={sec.index}>
                  {data.sections.length > 1 && (
                    <div className="sec-title">{sec.title} {sec.state === "done" ? "· đã nộp" : sec.state === "locked" ? "· chưa mở" : ""}</div>
                  )}
                  {sec.state === "locked" ? <div className="muted small">{sec.count} câu · mở khi bạn hoàn thành phần trước</div> : (
                    <div className="palette">
                      {its.map((i) => {
                        const cls = ["pal"];
                        if (isAnswered(i.response)) cls.push("answered");
                        if (i.flagged) cls.push("flagged");
                        if (i.position === item.position) cls.push("current");
                        const lockedSec = data.timing === "per_section" && i.section_index !== data.current_section;
                        if (lockedSec) cls.push("locked");
                        if (i.checked && i.outcome && i.scoring_mode === "auto") cls.push(i.outcome);
                        return (
                          <button key={i.position} className={cls.join(" ")} disabled={lockedSec}
                                  aria-label={`Câu ${i.position}${isAnswered(i.response) ? ", đã trả lời" : ""}${i.flagged ? ", đánh dấu" : ""}`}
                                  aria-current={i.position === item.position} onClick={() => go(i.position)}>
                            {i.position}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
            <div className="legend">
              <span><i style={{ background: "var(--brand)", borderColor: "var(--brand)" }} />Đã làm</span>
              <span><i />Chưa làm</span>
              <span><i style={{ background: "var(--flag)", borderColor: "var(--flag)", borderRadius: "50%" }} />Đánh dấu</span>
            </div>
            <button className="btn block mt accent" onClick={() => setConfirm(perSectionNext ? "section" : "submit")} disabled={busy}>
              {perSectionNext ? "Nộp phần này" : "Nộp bài"}
            </button>
          </div>
          <div className="muted small" style={{ padding: "0 4px" }}>Phím tắt: ← → chuyển câu, A–D chọn đáp án.</div>
        </aside>
      </div>

      <div className="runner-bottom">
        <div className="inner">
          <button className="btn secondary" disabled={idx <= 0} onClick={() => go(visible[idx - 1].position)}><Icon name="left" size={18} /> Câu trước</button>
          <div className="grow center muted small">Câu {idx + 1}/{visible.length}</div>
          {idx < visible.length - 1 ? (
            <button className="btn" onClick={() => go(visible[idx + 1].position)}>Câu tiếp <Icon name="right" size={18} /></button>
          ) : (
            <button className="btn accent" onClick={() => setConfirm(perSectionNext ? "section" : "submit")} disabled={busy}>
              {perSectionNext ? "Nộp phần này" : "Nộp bài"}
            </button>
          )}
        </div>
      </div>

      {confirm && (
        <Confirm title={confirm === "section" ? "Nộp phần thi này?" : "Nộp bài?"} busy={busy}
                 confirmText={confirm === "section" ? "Nộp và sang phần tiếp" : "Nộp bài"} onClose={() => setConfirm(null)}
                 onConfirm={() => (confirm === "section" ? nextSection() : doSubmit())}>
          <p>Bạn đã trả lời <strong>{answered}/{visible.length}</strong> câu{flagged ? <>, còn <strong>{flagged}</strong> câu đang đánh dấu</> : null}.</p>
          {answered < visible.length && <div className="alert warn">Còn {visible.length - answered} câu chưa trả lời.</div>}
          <p className="muted small mt">
            {confirm === "section" ? "Sau khi nộp, bạn không thể quay lại sửa phần này." : "Sau khi nộp, bạn không thể sửa câu trả lời."}
          </p>
        </Confirm>
      )}
      {report !== null && <ReportDialog questionRef={report} sessionId={id} onClose={() => setReport(null)} />}
    </div>
  );
}
