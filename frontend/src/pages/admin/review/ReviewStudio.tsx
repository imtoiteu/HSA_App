// Review Studio: source (left, read-only) beside the editable question (right); QA checklist, student preview,
// history/compare/restore, validator findings, on-demand AI; Save / Save & Next / Approve & Next with shortcuts.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AnswerInput, Passage, Solution, partsFromContent } from "../../../components/QuestionBody";
import { RichContent } from "../../../components/RichContent";
import { ErrorBox, Modal, Spinner, useToast } from "../../../components/ui";
import { ApiError, get, post, put, qs } from "../../../lib/api";
import { STATE_LABELS, TYPE_LABELS } from "../../../lib/format";
import type { Answer, Block } from "../../../lib/types";
import { StateBadge, copyText, errMsg } from "../common";
import BlockEditor, { type EditorHandle } from "./BlockEditor";
import { collectTex } from "./blockdom";
import PartsEditor, { type PartEdit } from "./PartsEditor";
import SourceViewer from "./SourceViewer";
import { texChecks } from "./tex";
import { DIM_ICON, DIM_KEYS, DIM_TONE, STATUS_LABEL, type ContentDoc, type Detail, type DimState, type Meta, type SourceInfo } from "./rtypes";
import { useMeta } from "./useMeta";

type Edit = {
  type: string; stem: Block[]; options: { label: string; content: Block[] }[]; answer: Answer | null;
  solution: Block[]; explanation: Block[]; group: ContentDoc["group"]; applyGroup: boolean; parts: PartEdit[];
};
const FILTER_KEYS = ["queue", "subject", "document", "page_from", "page_to", "ready", "answer", "formula", "visual", "findings",
  "assignee", "status", "modified_from", "modified_to", "q", "qtype", "served", "assignment", "sort"];
const LETTERS = "ABCDEFGH";

function fromDetail(d: Detail): Edit {
  const c = d.working.content;
  return { type: c.type, stem: c.stem || [], options: (c.options || []).map((o) => ({ ...o })), answer: d.working.answer,
           solution: c.solution || [], explanation: c.explanation || [], group: c.group ? { ...c.group } : null, applyGroup: true,
           parts: (c.parts || []).map((p) => ({ ...p })) };
}

function isTyping(t: EventTarget | null) {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || !!el.closest?.("math-field, .modal"));
}

export default function ReviewStudio() {
  const { id } = useParams();
  const qid = Number(id);
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const toast = useToast();
  const meta = useMeta();
  const [d, setD] = useState<Detail | null>(null);
  const [src, setSrc] = useState<SourceInfo | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [tab, setTab] = useState<"edit" | "preview" | "history" | "findings" | "ai">("edit");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [sourceRef, setSourceRef] = useState<Record<string, unknown> | null>(null);
  const [nb, setNb] = useState<{ prev: number | null; next: number | null; key: unknown[] | null; in_filter: boolean } | null>(null);
  const [failDim, setFailDim] = useState<string | null>(null);
  const [showKeys, setShowKeys] = useState(false);
  const focused = useRef<EditorHandle | null>(null);
  const stemRef = useRef<EditorHandle | null>(null);

  const filters = useMemo(() => Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k) || ""]).filter(([, v]) => v)), [sp]);
  const filterQs = qs(filters);
  const perms = new Set(meta?.me.perms || []);
  const canEdit = perms.has("edit"), canConfirm = perms.has("confirm"), canApprove = perms.has("approve");

  const load = useCallback(async () => {
    setErr(null);
    try {
      const det = await get<Detail>(`/api/review/questions/${qid}`);
      setD(det);
      setEdit(fromDetail(det));
      setSourceRef(null);
      setNote("");
    } catch (e) {
      setErr(e);
    }
  }, [qid]);

  useEffect(() => { setD(null); setSrc(null); load(); }, [load]);
  useEffect(() => { get<SourceInfo>(`/api/review/questions/${qid}/source`).then(setSrc).catch(() => setSrc({ available: false, status: "SOURCE_UNAVAILABLE", representative_occurrence_id: null, occurrences: [], documents: [], provenance: {} })); }, [qid]);
  useEffect(() => {
    get(`/api/review/queue/neighbors${qs({ ...filters, id: qid })}`).then(setNb).catch(() => setNb(null));
  }, [qid, filterQs]); // eslint-disable-line react-hooks/exhaustive-deps

  const base = d ? fromDetail(d) : null;
  const changed = useMemo(() => {
    if (!edit || !base) return {} as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    if (edit.type !== base.type) out.type = edit.type;
    if (JSON.stringify(edit.stem) !== JSON.stringify(base.stem)) out.stem = edit.stem;
    if (JSON.stringify(edit.options) !== JSON.stringify(base.options)) out.options = edit.options;
    if (JSON.stringify(edit.solution) !== JSON.stringify(base.solution)) out.solution = edit.solution;
    if (JSON.stringify(edit.explanation) !== JSON.stringify(base.explanation)) out.explanation = edit.explanation;
    if (edit.type === "multi_part" && (JSON.stringify(edit.parts) !== JSON.stringify(base.parts) || edit.type !== base.type)) out.parts = edit.parts;
    if (JSON.stringify(edit.answer) !== JSON.stringify(base.answer)) { if (edit.answer) out.answer = edit.answer; else out.clear_answer = true; }
    if (JSON.stringify(edit.group) !== JSON.stringify(base.group)) {
      if (edit.group) { out.group = edit.group; out.apply_group = edit.applyGroup; } else out.detach_group = true;
    }
    return out;
  }, [edit, d]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = Object.keys(changed).length > 0;

  useEffect(() => {
    const onBefore = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", onBefore);
    return () => window.removeEventListener("beforeunload", onBefore);
  }, [dirty]);

  const save = async (): Promise<boolean> => {
    if (!dirty) return true;
    if (!canEdit) { toast("Vai trò của bạn không được sửa nội dung.", "error"); return false; }
    setBusy(true);
    try {
      const texs = collectTex([changed.stem, changed.options, changed.solution, changed.explanation, changed.group].filter(Boolean)
        .map((x) => (Array.isArray(x) ? x : [x])));
      // options are {label, content}: walk their content too
      ((changed.options as Edit["options"]) || []).forEach((o) => collectTex(o.content, texs));
      // items of a multi-part question: prompts, options, solutions and formula keys
      ((changed.parts as PartEdit[]) || []).forEach((p) => {
        collectTex(p.prompt, texs);
        collectTex(p.solution || [], texs);
        (p.options || []).forEach((o) => collectTex(o.content, texs));
      });
      const r = await put<{ changed: boolean; changes?: string[]; group_updated?: number }>(`/api/review/questions/${qid}`,
        { ...changed, note: note.trim() || undefined, source_ref: sourceRef || undefined, tex_checks: texChecks(texs) });
      toast(r.changed ? `Đã lưu bản nháp (${(r.changes || []).join(", ")})${r.group_updated ? ` · cập nhật ${r.group_updated} câu cùng nhóm` : ""}` : "Không có thay đổi.", "ok");
      await load();
      return true;
    } catch (e) {
      toast(errMsg(e), "error");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const goto = (target: number | null) => {
    if (!target) { toast("Đã hết câu trong hàng đợi/bộ lọc hiện tại.", "info"); return; }
    nav(`/admin/review/q/${target}${filterQs}`);
  };
  // "next" is computed from the sort key the question had when it was opened, so a question that just left the
  // queue (or changed priority) does not make the reviewer skip or repeat items
  const next = async () => {
    const key = nb?.key ? JSON.stringify(nb.key) : undefined;
    const n = await get<NonNullable<typeof nb>>(`/api/review/queue/neighbors${qs({ ...filters, id: qid, key })}`).catch(() => nb);
    goto(n?.next ?? null);
  };
  const saveNext = async () => { if (await save()) await next(); };
  const approveNext = async () => {
    if (!(await save())) return;
    setBusy(true);
    try {
      const r = await post<{ gate: string | null; served: boolean; reasons: string[] }>(`/api/review/questions/${qid}/approve`, { note: note.trim() || undefined });
      toast(r.served ? "Đã phê duyệt — câu hỏi READY và đang được phục vụ." : `Đã phê duyệt. Chưa phục vụ: ${r.reasons.join(", ")}`, r.served ? "ok" : "info");
      await next();
    } catch (e) {
      toast(errMsg(e), "error");
      await load();
    } finally {
      setBusy(false);
    }
  };
  const confirm = async (dim: string, state: DimState | null, dimNote?: string) => {
    if (!canConfirm) { toast("Vai trò của bạn không xác nhận QA.", "error"); return; }
    if (dirty) { toast("Hãy lưu thay đổi trước khi xác nhận QA (xác nhận gắn với nội dung đã lưu).", "error"); return; }
    try {
      await post(`/api/review/questions/${qid}/confirm`, { dim, state, note: dimNote });
      await load();
    } catch (e) {
      toast(errMsg(e), "error");
    }
  };
  const action = async (path: string, body: unknown, okText: string) => {
    setBusy(true);
    try { await post(path, body); toast(okText, "ok"); await load(); } catch (e) { toast(errMsg(e), "error"); } finally { setBusy(false); }
  };

  // keyboard: Ctrl+Enter save & next, Ctrl+S save, Ctrl+Shift+Enter approve & next; letters toggle QA when not typing
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  keyRef.current = (e: KeyboardEvent) => {
    if (!d) return;
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (e.shiftKey && canApprove) approveNext(); else saveNext(); return; }
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) { e.preventDefault(); save(); return; }
    if (isTyping(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
    const dim = DIM_KEYS[e.key.toLowerCase()];
    if (dim) { e.preventDefault(); confirm(dim, d.dims[dim].state === "ok" ? null : "ok"); return; }
    if (e.key === "ArrowRight" || e.key === "j") { e.preventDefault(); goto(nb?.next ?? null); }
    if (e.key === "ArrowLeft" || e.key === "k") { e.preventDefault(); goto(nb?.prev ?? null); }
    if (e.key === "?") setShowKeys(true);
    if (e.key === "p") setTab((t) => (t === "preview" ? "edit" : "preview"));
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keyRef.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  if (err) return <div><ErrorBox error={err} /><Link to={`/admin/review/queue${filterQs}`}>← Hàng đợi</Link></div>;
  if (!d || !edit || !meta) return <Spinner />;

  const stemText = (d.preview.stem || []).map((b) => (b.t === "p" ? b.c.map((n) => (n.t === "s" ? n.v : n.t === "m" ? ` ${n.tex} ` : " ")).join("") : "")).join(" ");
  const up = (patch: Partial<Edit>) => setEdit((e) => (e ? { ...e, ...patch } : e));
  const onCrop = (r: { src: string; w: number; h: number; source_ref: Record<string, unknown> }) => {
    (focused.current || stemRef.current)?.insertImage({ src: r.src, w: r.w, h: r.h });
    setSourceRef(r.source_ref);
  };
  const dimsOrder = ["content", "answer", "formula", "visual", "subject", "source"];
  const blockers = dimsOrder.filter((k) => !["ok", "na"].includes(d.dims[k].state));

  return (
    <div className="studio">
      <div className="studio-top">
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <Link to={`/admin/review/queue${filterQs}`} className="btn ghost sm">← Hàng đợi</Link>
          <span className="mono" title="Mã câu hỏi">{d.external_id}</span>
          <button className="btn ghost sm" onClick={() => copyText(d.external_id)} title="Sao chép cq_id">⧉</button>
          <StateBadge state={d.editorial_state} />
          <span className={"badge " + (d.ready ? "ok" : "")}>{d.ready ? "READY" : "Chưa READY"}</span>
          <span className={"badge " + (d.served ? "ok" : "")}>{d.served ? "Đang phục vụ" : "Không phục vụ"}</span>
          <span className={"badge " + (d.review.status === "draft" ? "warn" : "brand")} title={d.review.draft_version_id ? "Học sinh vẫn thấy phiên bản đang phục vụ cho đến khi bản nháp được phê duyệt" : ""}>{STATUS_LABEL[d.review.status] || d.review.status}</span>
          {d.review.draft_version_id && d.review.status !== "draft" && <span className="badge warn" title="Có bản nháp chưa phê duyệt; học sinh vẫn thấy phiên bản đang phục vụ">Có bản nháp</span>}
          {d.gate === "blocked" && <span className="badge bad">Bị chặn bởi người duyệt</span>}
          {d.review.upstream_changed && <span className="badge warn">Nguồn đã thay đổi</span>}
          {d.review.assignee && <span className="small muted">Giao cho: {d.review.assignee.name}</span>}
        </div>
        <div className="row studio-actions" style={{ gap: 6 }}>
          {nb && !nb.in_filter && <span className="small muted" title="Câu này không còn thuộc bộ lọc hiện tại">ngoài bộ lọc</span>}
          <button className="btn secondary sm" onClick={() => goto(nb?.prev ?? null)} disabled={!nb?.prev || busy}>‹ Trước</button>
          <button className="btn secondary sm" onClick={save} disabled={!dirty || busy || !canEdit}>Lưu</button>
          <button className="btn sm" onClick={saveNext} disabled={busy} title="Ctrl+Enter">Lưu & tiếp ›</button>
          {canApprove && <button className="btn ok sm" onClick={approveNext} disabled={busy || blockers.length > 0}
                                 title={blockers.length ? "Còn tiêu chí QA chưa xác nhận: " + blockers.map((k) => d.dims[k].label).join(", ") : "Ctrl+Shift+Enter"}>
            Phê duyệt & tiếp ›</button>}
          <button className="btn ghost sm" onClick={() => setShowKeys(true)} title="Phím tắt (?)">⌨</button>
        </div>
      </div>

      <div className="studio-split">
        <SourceViewer qid={qid} info={src} stemText={stemText} onCrop={onCrop} canEdit={canEdit} />

        <div className="studio-right">
          <section className="qa-panel" aria-label="Kiểm tra chất lượng">
            <div className="row between">
              <strong>Kiểm tra QA</strong>
              <span className="small muted">Phím: A đáp án · F công thức · V hình · S môn · C nội dung · N nguồn</span>
            </div>
            <div className="qa-dims">
              {dimsOrder.map((k) => {
                const x = d.dims[k];
                return (
                  <div key={k} className={"qa-dim " + DIM_TONE[x.state]} title={x.by ? `${x.state} · ${x.by} · ${x.at?.slice(0, 16).replace("T", " ")}${x.note ? " · " + x.note : ""}` : x.state === "stale" ? "Đã xác nhận cho nội dung cũ — cần xác nhận lại" : ""}>
                    <span className="qa-icon" aria-hidden>{DIM_ICON[x.state]}</span>
                    <span className="qa-label">{x.label}{!x.required && <span className="small muted"> (không bắt buộc)</span>}</span>
                    <span className="qa-btns">
                      <button className={"tb sm" + (x.state === "ok" ? " on" : "")} disabled={!canConfirm} onClick={() => confirm(k, x.state === "ok" ? null : "ok")} aria-label={`${x.label}: đạt`}>✓</button>
                      <button className={"tb sm" + (x.confirmed === "na" ? " on" : "")} disabled={!canConfirm} onClick={() => confirm(k, x.confirmed === "na" ? null : "na")} title="Không áp dụng" aria-label={`${x.label}: không áp dụng`}>–</button>
                      <button className={"tb sm" + (x.state === "fail" ? " on bad" : "")} disabled={!canConfirm} onClick={() => (x.state === "fail" ? confirm(k, null) : setFailDim(k))} aria-label={`${x.label}: không đạt`}>✗</button>
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="small muted">
              READY chỉ khi: mọi tiêu chí ✓ hoặc không áp dụng, không còn lỗi nghiêm trọng, người phê duyệt duyệt.
              {d.policy_reasons.length > 0 && <> Chưa phục vụ vì: <span className="mono">{d.policy_reasons.join(", ")}</span>.</>}
              {d.used_in_attempts > 0 && <> Đã dùng trong {d.used_in_attempts} lượt làm bài (lịch sử giữ nguyên phiên bản cũ).</>}
            </div>
          </section>

          <div className="tabs" role="tablist">
            {([["edit", "Biên tập"], ["preview", "Xem như học sinh"], ["history", `Lịch sử (${d.history.length})`],
               ["findings", `Tự kiểm tra (${d.findings.filter((f) => f.status === "open").length})`], ["ai", "✨ AI hỗ trợ"]] as const).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>

          {tab === "edit" && (
            <Editor d={d} edit={edit} up={up} meta={meta} canEdit={canEdit} focused={focused} stemRef={stemRef}
                    onSubject={async (code, topic, n) => action(`/api/review/questions/${qid}/subject`, { subject_code: code || null, topic, note: n }, "Đã cập nhật môn/chủ đề.")} />
          )}
          {tab === "preview" && <StudentPreview d={d} edit={edit} dirty={dirty} />}
          {tab === "history" && <History d={d} canEdit={canEdit} onRestore={(vid, n) => action(`/api/review/questions/${qid}/restore`, { version_id: vid, note: n }, "Đã khôi phục thành bản nháp.")} />}
          {tab === "findings" && <Findings d={d} canConfirm={canConfirm} onAct={(fid, act, reason) => action(`/api/review/questions/${qid}/findings/${fid}`, { action: act, reason }, "Đã cập nhật.")}
                                           onRevalidate={() => action(`/api/review/questions/${qid}/revalidate`, {}, "Đã kiểm tra lại.")} />}
          {tab === "ai" && <AiPanel qid={qid} meta={meta} src={src} />}

          <div className="studio-foot">
            <div className="field" style={{ flex: 1 }}>
              <label htmlFor="rv-note">Ghi chú cho lần lưu/duyệt (lưu vào lịch sử)</label>
              <input id="rv-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="vd: sửa công thức theo trang 12 của đề gốc" />
            </div>
            {sourceRef && <span className="badge brand" title={JSON.stringify(sourceRef)}>Kèm tham chiếu nguồn (trang {String(sourceRef.page)})</span>}
            {dirty && <span className="badge warn">Chưa lưu</span>}
            <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              {canConfirm && <button className="btn ghost sm" disabled={busy} onClick={() => { const n = prompt("Lý do trả lại cho biên tập viên:"); if (n) action(`/api/review/questions/${qid}/status`, { action: "return", note: n }, "Đã trả lại."); }}>Trả lại</button>}
              {canEdit && d.review.draft_version_id && <button className="btn ghost sm" disabled={busy} onClick={() => { if (confirmBox("Bỏ bản nháp? Nội dung đang phục vụ giữ nguyên.")) action(`/api/review/questions/${qid}/status`, { action: "discard_draft" }, "Đã bỏ bản nháp."); }}>Bỏ bản nháp</button>}
              {canApprove && d.review.status === "approved" && <button className="btn ghost sm" disabled={busy} onClick={() => { if (confirmBox("Rút lại phê duyệt? Câu hỏi quay về trạng thái theo nguồn.")) action(`/api/review/questions/${qid}/status`, { action: "reopen" }, "Đã rút phê duyệt."); }}>Rút phê duyệt</button>}
              {dirty && <button className="btn ghost sm" onClick={() => setEdit(fromDetail(d))}>Huỷ thay đổi</button>}
            </div>
          </div>
        </div>
      </div>

      {failDim && <FailDialog label={d.dims[failDim].label} onClose={() => setFailDim(null)} onSave={(n) => { confirm(failDim, "fail", n); setFailDim(null); }} />}
      {showKeys && (
        <Modal title="Phím tắt" onClose={() => setShowKeys(false)}>
          <table className="data small"><tbody>
            <tr><td><kbd>Ctrl</kbd>+<kbd>Enter</kbd></td><td>Lưu & sang câu tiếp</td></tr>
            <tr><td><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Enter</kbd></td><td>Phê duyệt & sang câu tiếp</td></tr>
            <tr><td><kbd>Ctrl</kbd>+<kbd>S</kbd></td><td>Lưu bản nháp</td></tr>
            <tr><td><kbd>Ctrl</kbd>+<kbd>M</kbd> (trong ô soạn)</td><td>Chèn công thức</td></tr>
            <tr><td><kbd>A</kbd> <kbd>F</kbd> <kbd>V</kbd> <kbd>S</kbd> <kbd>C</kbd> <kbd>N</kbd></td><td>Đạt/bỏ: Đáp án · Công thức · Hình · Môn · Nội dung · Nguồn (chỉ khi không gõ trong ô nhập)</td></tr>
            <tr><td><kbd>←</kbd>/<kbd>→</kbd> hoặc <kbd>K</kbd>/<kbd>J</kbd></td><td>Câu trước / câu sau</td></tr>
            <tr><td><kbd>P</kbd></td><td>Bật/tắt xem như học sinh</td></tr>
          </tbody></table>
        </Modal>
      )}
    </div>
  );
}

function confirmBox(t: string) { return window.confirm(t); }

function FailDialog({ label, onClose, onSave }: { label: string; onClose: () => void; onSave: (n: string) => void }) {
  const [n, setN] = useState("");
  return (
    <Modal title={`Không đạt: ${label}`} onClose={onClose} actions={<>
      <button className="btn secondary" onClick={onClose}>Huỷ</button>
      <button className="btn danger" disabled={!n.trim()} onClick={() => onSave(n.trim())}>Đánh dấu không đạt</button>
    </>}>
      <p className="small">Câu hỏi sẽ bị chặn (không phục vụ học sinh) cho đến khi lỗi được sửa và xác nhận lại.</p>
      <textarea className="input" rows={3} autoFocus value={n} onChange={(e) => setN(e.target.value)} placeholder="Mô tả lỗi (bắt buộc)" />
    </Modal>
  );
}

// ------------------------------------------------------------------------------------------------
function Editor({ d, edit, up, meta, canEdit, focused, stemRef, onSubject }: {
  d: Detail; edit: Edit; up: (p: Partial<Edit>) => void; meta: Meta; canEdit: boolean;
  focused: React.MutableRefObject<EditorHandle | null>; stemRef: React.MutableRefObject<EditorHandle | null>;
  onSubject: (code: string, topic: string | null, note: string) => void;
}) {
  const [subj, setSubj] = useState(d.subject_override || d.subject || "");
  const [topic, setTopic] = useState(d.topic || "");
  const optRefs = useRef<Record<string, EditorHandle | null>>({});
  const solRef = useRef<EditorHandle | null>(null);
  useEffect(() => { setSubj(d.subject_override || d.subject || ""); setTopic(d.topic || ""); }, [d.id, d.subject, d.subject_override, d.topic]);

  const relabel = (opts: Edit["options"]) => opts.map((o, i) => ({ ...o, label: LETTERS[i] }));
  const removeOpt = (i: number) => {
    const old = edit.options[i].label;
    const opts = relabel(edit.options.filter((_, j) => j !== i));
    let answer = edit.answer;
    if (answer?.kind === "choice") {
      const map = Object.fromEntries(edit.options.filter((_, j) => j !== i).map((o, j) => [o.label, LETTERS[j]]));
      const labels = answer.labels.filter((l) => l !== old).map((l) => map[l]).filter(Boolean);
      answer = labels.length ? { kind: "choice", labels } : null;
    }
    up({ options: opts, answer });
  };
  const choice = ["single_choice", "multiple_choice", "error_identification", "true_false"].includes(edit.type) && edit.options.length > 0;

  return (
    <div className="editor-col">
      <div className="grid cols-3">
        <div className="field">
          <label htmlFor="ed-type">Dạng câu</label>
          <select id="ed-type" className="input" value={edit.type} disabled={!canEdit} onChange={(e) => up({ type: e.target.value })}>
            {meta.types.map((t) => <option key={t} value={t}>{TYPE_LABELS[t] || t}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ed-subj">Môn {d.subject_source === "admin" ? "(đã ghi đè)" : `(nguồn: ${d.upstream_subject || "chưa phân loại"})`}</label>
          <select id="ed-subj" className="input" value={subj} disabled={!canEdit} onChange={(e) => setSubj(e.target.value)}>
            <option value="">Theo nguồn ({d.upstream_subject || "chưa phân loại"})</option>
            {meta.subjects.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="ed-topic">Chủ đề</label>
          <div className="row" style={{ gap: 6 }}>
            <input id="ed-topic" className="input" value={topic} disabled={!canEdit} onChange={(e) => setTopic(e.target.value)} />
            <button className="btn secondary sm" disabled={!canEdit || (subj === (d.subject_override || d.subject || "") && topic === (d.topic || ""))}
                    onClick={() => onSubject(subj, topic || null, "")}>Áp dụng</button>
          </div>
        </div>
      </div>
      {d.classification_review && <div className="small muted">Nguồn đánh dấu cần xem lại môn ({d.classification_review}).</div>}

      {edit.group && (
        <fieldset className="ed-group">
          <legend>Đoạn dữ liệu chung <span className="mono small">{edit.group.key}</span></legend>
          <BlockEditor label="Tiêu đề đoạn" compact value={edit.group.header} readOnly={!canEdit}
                       onChange={(b) => up({ group: { ...edit.group!, header: b } })} />
          <BlockEditor label="Đoạn dữ liệu" value={edit.group.passage} readOnly={!canEdit}
                       onChange={(b) => up({ group: { ...edit.group!, passage: b } })} />
          <div className="row" style={{ gap: 10 }}>
            <label className="small"><input type="checkbox" checked={edit.applyGroup} disabled={!canEdit} onChange={(e) => up({ applyGroup: e.target.checked })} /> Áp dụng sửa đoạn cho mọi câu cùng nhóm</label>
            {canEdit && <button className="btn ghost sm" onClick={() => { if (window.confirm("Tách câu này khỏi đoạn dữ liệu chung?")) up({ group: null }); }}>Tách khỏi nhóm</button>}
          </div>
        </fieldset>
      )}

      <div className="field">
        <span className="label">Đề bài</span>
        <BlockEditor ref={(h) => { stemRef.current = h; }} label="Đề bài" value={edit.stem} readOnly={!canEdit}
                     onFocusEditor={() => { focused.current = stemRef.current; }} onChange={(b) => up({ stem: b })} placeholder="Nhập đề bài…" />
      </div>

      {edit.type === "multi_part" ? (
        <PartsEditor parts={edit.parts} answer={edit.answer} canEdit={canEdit}
                     onChange={(parts, answer) => up({ parts, answer })} />
      ) : <>
      <div className="field">
        <div className="row between"><span className="label">Phương án</span>
          {canEdit && edit.options.length < 8 && <button className="btn ghost sm" onClick={() => up({ options: [...edit.options, { label: LETTERS[edit.options.length], content: [] }] })}>+ Thêm phương án</button>}
        </div>
        {edit.options.length === 0 && <div className="small muted">Không có phương án (câu trả lời ngắn/tự luận).</div>}
        {edit.options.map((o, i) => (
          <div key={i} className={"ed-option" + (edit.answer?.kind === "choice" && edit.answer.labels.includes(o.label) ? " correct" : "")}>
            <span className="opt-label">{o.label}</span>
            <BlockEditor ref={(h) => { optRefs.current[o.label] = h; }} label={`Phương án ${o.label}`} compact value={o.content} readOnly={!canEdit}
                         onFocusEditor={() => { focused.current = optRefs.current[o.label]; }}
                         onChange={(b) => up({ options: edit.options.map((x, j) => (j === i ? { ...x, content: b } : x)) })} />
            {canEdit && <button className="btn ghost sm" title="Xoá phương án" onClick={() => removeOpt(i)}>✕</button>}
          </div>
        ))}
      </div>

      <AnswerEditor edit={edit} up={up} canEdit={canEdit} choice={choice} />
      </>}

      <div className="field">
        <span className="label">Lời giải</span>
        <BlockEditor ref={(h) => { solRef.current = h; }} label="Lời giải" richPaste value={edit.solution} readOnly={!canEdit}
                     onFocusEditor={() => { focused.current = solRef.current; }} onChange={(b) => up({ solution: b })} placeholder="(chưa có lời giải)" />
      </div>
      {(edit.explanation.length > 0 || canEdit) && (
        <details>
          <summary className="small">Phương pháp / giải thích thêm</summary>
          <BlockEditor label="Phương pháp" value={edit.explanation} readOnly={!canEdit} onChange={(b) => up({ explanation: b })} />
        </details>
      )}
      {(d.content_flags.length > 0 || d.state_notes.length > 0) && (
        <div className="small muted">
          {d.content_flags.length > 0 && <div>Cờ nội dung: {d.content_flags.map((f) => d.content_flag_labels[f] || f).join(" · ")}</div>}
          {d.state_notes.length > 0 && <div>Ghi chú nguồn: {d.state_notes.slice(0, 5).join(" · ")}</div>}
          <div>Trạng thái nguồn: {d.editorial_states.map((s) => STATE_LABELS[s] || s).join(", ")}</div>
        </div>
      )}
    </div>
  );
}

function AnswerEditor({ edit, up, canEdit, choice }: { edit: Edit; up: (p: Partial<Edit>) => void; canEdit: boolean; choice: boolean }) {
  const a = edit.answer;
  const kind = a?.kind || (choice ? "choice" : edit.type === "numeric_response" ? "numeric" : edit.type === "true_false_statements" ? "tf_sequence"
    : edit.type === "true_false" ? "boolean" : "text");
  const [mode, setMode] = useState<string>(kind);
  useEffect(() => setMode(kind), [kind]);
  const multi = edit.type === "multiple_choice";
  return (
    <fieldset className="ed-answer">
      <legend>Đáp án {a ? "" : <span className="badge warn">chưa có</span>}</legend>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <select className="input sm" value={mode} disabled={!canEdit} onChange={(e) => { setMode(e.target.value); up({ answer: null }); }} aria-label="Dạng đáp án">
          <option value="choice">Chọn phương án</option><option value="numeric">Số</option><option value="text">Chữ</option>
          <option value="tf_sequence">Đúng/Sai từng ý</option><option value="boolean">Đúng/Sai</option>
        </select>
        {mode === "choice" && edit.options.map((o) => {
          const on = a?.kind === "choice" && a.labels.includes(o.label);
          return (
            <label key={o.label} className={"chip" + (on ? " on" : "")}>
              <input type={multi ? "checkbox" : "radio"} name="ans" checked={on} disabled={!canEdit}
                     onChange={() => {
                       const cur = a?.kind === "choice" ? a.labels : [];
                       const labels = multi ? (on ? cur.filter((x) => x !== o.label) : [...cur, o.label].sort()) : [o.label];
                       up({ answer: labels.length ? { kind: "choice", labels } : null });
                     }} /> {o.label}
            </label>
          );
        })}
        {mode === "numeric" && <input className="input sm" style={{ maxWidth: 160 }} disabled={!canEdit} placeholder="vd: 2,5"
                                      value={a?.kind === "numeric" ? a.text : ""}
                                      onChange={(e) => up({ answer: e.target.value.trim() ? { kind: "numeric", text: e.target.value, value: Number(e.target.value.replace(",", ".")) } : null })} />}
        {mode === "text" && <input className="input sm" style={{ flex: 1 }} disabled={!canEdit} value={a?.kind === "text" ? a.text : ""}
                                   onChange={(e) => up({ answer: e.target.value.trim() ? { kind: "text", text: e.target.value } : null })} />}
        {mode === "boolean" && ["Đúng", "Sai"].map((l, i) => (
          <label key={l} className={"chip" + (a?.kind === "boolean" && a.value === (i === 0) ? " on" : "")}>
            <input type="radio" name="ansb" disabled={!canEdit} checked={a?.kind === "boolean" && a.value === (i === 0)} onChange={() => up({ answer: { kind: "boolean", value: i === 0 } })} /> {l}
          </label>
        ))}
        {mode === "tf_sequence" && (() => {
          const vals = a?.kind === "tf_sequence" ? a.values : [];
          const n = Math.max(vals.length, (edit.answer?.kind === "tf_sequence" ? 0 : 4));
          return (
            <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              {Array.from({ length: n }).map((_, i) => (
                <button key={i} className={"chip" + (vals[i] === undefined ? "" : vals[i] ? " on" : " off")} disabled={!canEdit}
                        onClick={() => { const v = [...vals]; while (v.length < n) v.push(false); v[i] = !v[i]; up({ answer: { kind: "tf_sequence", values: v } }); }}>
                  {String.fromCharCode(97 + i)}) {vals[i] === undefined ? "?" : vals[i] ? "Đúng" : "Sai"}
                </button>
              ))}
              {canEdit && <button className="btn ghost sm" onClick={() => up({ answer: { kind: "tf_sequence", values: [...vals, false] } })}>+ ý</button>}
            </div>
          );
        })()}
        {canEdit && a && <button className="btn ghost sm" onClick={() => up({ answer: null })}>Xoá đáp án</button>}
      </div>
    </fieldset>
  );
}

// ------------------------------------------------------------------------------------------------
function StudentPreview({ d, edit, dirty }: { d: Detail; edit: Edit; dirty: boolean }) {
  const [reveal, setReveal] = useState(true);
  const [which, setWhich] = useState<"working" | "published">("working");
  // unsaved edits are previewed from the editor state with the same components the student pages use
  const q = useMemo(() => {
    if (which === "published" && d.published_preview) return d.published_preview;
    const base = d.preview;
    if (!dirty) return base;
    const mp = partsFromContent({ type: edit.type, parts: edit.parts }, edit.answer);
    return { ...base, type: edit.type, stem: edit.stem, group: edit.group, solution: edit.solution.length ? edit.solution : null,
             explanation: edit.explanation.length ? edit.explanation : null, answer: edit.answer,
             options: edit.options.map((o, i) => ({ key: o.label, display: LETTERS[i], content: o.content })),
             input: edit.type === "multi_part" ? { kind: "parts" as const, parts: mp.parts.map((p) => ({ key: p.key, ...p.input })) } : base.input,
             parts: mp.parts, part_results: mp.results };
  }, [d, edit, dirty, which]);
  return (
    <div>
      <div className="row between mb">
        <div className="row" style={{ gap: 8 }}>
          <label className="small"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Hiện đáp án & lời giải</label>
          {d.published_preview && (
            <select className="input sm" value={which} onChange={(e) => setWhich(e.target.value as "working" | "published")}>
              <option value="working">Bản đang sửa</option><option value="published">Bản học sinh đang thấy</option>
            </select>
          )}
        </div>
        {dirty && <span className="badge warn">Xem trước thay đổi chưa lưu</span>}
      </div>
      <article className="q-card preview-card">
        {q.group && <Passage group={q.group} lang={q.language} />}
        <div className="q-head"><span className="q-num">Câu 1</span></div>
        <RichContent blocks={q.stem} />
        <AnswerInput input={q.input} options={q.options} response={null} disabled onChange={() => {}}
                     parts={q.parts} partResults={reveal ? q.part_results : undefined} graded={false} idPrefix="pv"
                     reveal={reveal ? { answer: q.answer, response: null } : undefined} />
        {reveal && <Solution q={q} answer={q.answer} />}
      </article>
    </div>
  );
}

function History({ d, canEdit, onRestore }: { d: Detail; canEdit: boolean; onRestore: (vid: number, note: string) => void }) {
  const [a, setA] = useState<number>(d.versions.find((v) => v.is_current)?.id || d.versions[0]?.id);
  const [b, setB] = useState<number>(d.versions[0]?.id);
  const [cmp, setCmp] = useState<{ a: any; b: any } | null>(null);
  const [cmpErr, setCmpErr] = useState<unknown>(null);
  const compare = async () => {
    setCmpErr(null);
    try {
      const [va, vb] = await Promise.all([get(`/api/review/questions/${d.id}/versions/${a}`), get(`/api/review/questions/${d.id}/versions/${b}`)]);
      setCmp({ a: va, b: vb });
    } catch (e) { setCmpErr(e); }
  };
  const ACTIONS: Record<string, string> = { edit: "Sửa nội dung", confirm: "Xác nhận QA", approve: "Phê duyệt", publish: "Xuất bản",
    subject: "Đổi môn", topic: "Đổi chủ đề", restore: "Khôi phục phiên bản", return: "Trả lại", reopen: "Rút phê duyệt",
    discard_draft: "Bỏ bản nháp", finding: "Xử lý phát hiện", assign: "Giao việc", ai_suggestion: "Gợi ý AI",
    asset_from_source: "Cắt hình từ nguồn", upstream_changed: "Nguồn thay đổi" };
  const show = (v: unknown) => (v === null || v === undefined ? "∅" : typeof v === "string" ? v : JSON.stringify(v));
  return (
    <div className="stack">
      <section>
        <h3>Phiên bản</h3>
        <div className="table-wrap"><table className="data small"><thead><tr><th>#</th><th>Nguồn</th><th>Lúc</th><th>Người</th><th>Ghi chú</th><th></th></tr></thead>
          <tbody>{d.versions.map((v) => (
            <tr key={v.id}>
              <td>v{v.version_no} {v.is_current && <span className="badge ok">đang phục vụ</span>} {v.is_draft && <span className="badge warn">nháp</span>} {v.is_upstream && <span className="badge">ngân hàng</span>}</td>
              <td>{v.origin === "review" ? "Review Studio" : "HSA-question-bank"}</td>
              <td>{v.created_at?.slice(0, 16).replace("T", " ")}</td><td>{v.created_by || "—"}</td><td>{v.note || ""}</td>
              <td>{canEdit && !v.is_current && !v.is_draft && <button className="btn ghost sm" onClick={() => { const n = prompt("Lý do khôi phục phiên bản này (sẽ thành bản nháp, cần phê duyệt):"); if (n !== null) onRestore(v.id, n); }}>Khôi phục</button>}</td>
            </tr>))}</tbody></table></div>
        <div className="row mt" style={{ gap: 8, flexWrap: "wrap" }}>
          <span className="small">So sánh</span>
          <select className="input sm" value={a} onChange={(e) => setA(Number(e.target.value))}>{d.versions.map((v) => <option key={v.id} value={v.id}>v{v.version_no}</option>)}</select>
          <span>↔</span>
          <select className="input sm" value={b} onChange={(e) => setB(Number(e.target.value))}>{d.versions.map((v) => <option key={v.id} value={v.id}>v{v.version_no}</option>)}</select>
          <button className="btn secondary sm" onClick={compare}>So sánh</button>
        </div>
        <ErrorBox error={cmpErr} />
        {cmp && (
          <div className="compare">
            {[cmp.a, cmp.b].map((v, i) => (
              <div key={i} className="q-card preview-card">
                <div className="small muted mb">v{v.version_no} · {v.origin} · {v.created_at?.slice(0, 16).replace("T", " ")}</div>
                {v.preview.group && <Passage group={v.preview.group} />}
                <RichContent blocks={v.preview.stem} />
                <AnswerInput input={v.preview.input} options={v.preview.options} response={null} disabled onChange={() => {}} reveal={{ answer: v.answer, response: null }} />
                <Solution q={v.preview} answer={v.answer} />
              </div>
            ))}
          </div>
        )}
      </section>
      <section>
        <h3>Nhật ký thay đổi</h3>
        {!d.history.length && <div className="muted small">Chưa có thao tác nào.</div>}
        <ul className="timeline">
          {d.history.map((h) => (
            <li key={h.id}>
              <div className="small"><strong>{ACTIONS[h.action] || h.action}</strong> · {h.actor || "hệ thống"} · {h.at.slice(0, 16).replace("T", " ")}
                {h.from_version_id && h.to_version_id && <span className="muted"> · phiên bản {h.from_version_id} → {h.to_version_id}</span>}</div>
              {h.note && <div className="small">“{h.note.length > 600 ? h.note.slice(0, 600) + "…" : h.note}”</div>}
              {h.changes.length > 0 && (
                <table className="diff small"><tbody>{h.changes.map((c, i) => (
                  <tr key={i}><td className="mono">{c.field}</td><td className="old">{show(c.old).slice(0, 400)}</td><td className="new">{show(c.new).slice(0, 400)}</td></tr>
                ))}</tbody></table>
              )}
              {h.source_ref && <div className="small muted">Nguồn: {show(h.source_ref)}</div>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Findings({ d, canConfirm, onAct, onRevalidate }: { d: Detail; canConfirm: boolean; onAct: (id: number, a: string, reason?: string) => void; onRevalidate: () => void }) {
  return (
    <div>
      <div className="row between mb">
        <span className="small muted">Bộ kiểm tra cục bộ (không dùng AI) · phiên bản {d.review.validator_version ?? "—"} · lần cuối {d.review.validated_at?.slice(0, 16).replace("T", " ") || "—"}</span>
        <button className="btn secondary sm" onClick={onRevalidate}>Kiểm tra lại</button>
      </div>
      {!d.findings.length && <div className="alert ok">Không có phát hiện nào.</div>}
      <div className="table-wrap"><table className="data small"><tbody>
        {d.findings.map((f) => (
          <tr key={f.id} className={f.status !== "open" ? "muted" : ""}>
            <td><span className={"badge " + (f.severity === "critical" ? "bad" : f.severity === "warning" ? "warn" : "")}>{f.severity === "critical" ? "nghiêm trọng" : f.severity === "warning" ? "cảnh báo" : "thông tin"}</span></td>
            <td><strong>{f.label}</strong><div className="muted">{f.detail}</div>{f.reason && <div>Lý do: {f.reason} {f.resolved_by && `(${f.resolved_by})`}</div>}</td>
            <td>{f.status === "open" ? "mở" : f.status === "accepted" ? "đã xác nhận, cần sửa" : f.status === "fixed" ? "đã sửa" : "đã bỏ qua"}</td>
            <td style={{ whiteSpace: "nowrap" }}>
              {f.status === "open" && <button className="btn ghost sm" onClick={() => onAct(f.id, "accept")}>Xác nhận lỗi</button>}
              {canConfirm && f.status !== "dismissed" && f.status !== "fixed" && <button className="btn ghost sm" onClick={() => { const r = prompt("Lý do bỏ qua (bắt buộc):"); if (r) onAct(f.id, "dismiss", r); }}>Bỏ qua</button>}
              {canConfirm && f.status === "dismissed" && <button className="btn ghost sm" onClick={() => onAct(f.id, "reopen")}>Mở lại</button>}
            </td>
          </tr>
        ))}
      </tbody></table></div>
      <p className="small muted mt">Sửa lỗi trong tab Biên tập rồi lưu: phát hiện tự chuyển “đã sửa” khi không còn xuất hiện.</p>
    </div>
  );
}

function AiPanel({ qid, meta, src }: { qid: number; meta: Meta; src: SourceInfo | null }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [out, setOut] = useState<{ action: string; text: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const doc = src?.documents[0];
  const occ = src?.occurrences.find((o) => o.document_id === doc?.document_id);
  const run = async (action: string) => {
    setBusy(action);
    setErr(null);
    try {
      const r = await post<{ suggestion: string }>(`/api/review/questions/${qid}/ai`, { action, document_id: doc?.document_id, page: occ?.pages?.[0] || null });
      setOut({ action, text: r.suggestion });
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div>
      <div className="alert info small">AI chỉ chạy khi bạn bấm, cho đúng câu này. Kết quả chỉ là gợi ý: không tự sửa, không tự xác nhận QA, không tự READY.</div>
      {!meta.ai.configured && <div className="alert warn small">Trợ lý AI chưa được cấu hình trên máy chủ (biến môi trường HSA_AI_PROVIDER / HSA_AI_API_KEY). Review Studio vẫn hoạt động đầy đủ không cần AI.</div>}
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        {meta.ai.actions.map((a) => (
          <button key={a.code} className="btn secondary sm" disabled={!meta.ai.configured || !!busy || (a.needs_source_image && !occ?.pages?.length)}
                  title={a.needs_source_image && !occ?.pages?.length ? "Cần trang tài liệu gốc" : ""} onClick={() => run(a.code)}>
            {busy === a.code ? "Đang hỏi…" : a.label}
          </button>
        ))}
      </div>
      {err instanceof ApiError ? <div className="alert error mt">{err.message}</div> : <ErrorBox error={err} />}
      {out && (
        <div className="card mt">
          <div className="small muted mb">Gợi ý ({meta.ai.actions.find((a) => a.code === out.action)?.label}) — kiểm tra trước khi dùng:</div>
          <pre className="ai-out">{out.text}</pre>
          <button className="btn ghost sm" onClick={() => copyText(out.text)}>Sao chép</button>
        </div>
      )}
    </div>
  );
}
