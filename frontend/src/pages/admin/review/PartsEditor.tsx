// Items of a multi-part question ("Nhiều ý / Câu ghép"): each with its own prompt, answer type, options (choice),
// key, worked solution and points. The parent question has no single answer: the keys live per item.
import type { Answer, Block } from "../../../lib/types";
import BlockEditor from "./BlockEditor";

export type AnswerType = "choice" | "numeric" | "text" | "formula" | "boolean";
export interface PartEdit {
  key: string; label: string; prompt: Block[]; answer_type: AnswerType; multi?: boolean;
  options?: { label: string; content: Block[] }[]; solution: Block[] | null; points: number;
}

const TYPE_NAMES: Record<AnswerType, string> = {
  choice: "Chọn phương án", numeric: "Số", text: "Chữ", formula: "Công thức", boolean: "Đúng/Sai",
};
const LETTERS = "ABCDEFGH";

function nextKey(parts: PartEdit[]): string {
  const used = new Set(parts.map((p) => p.key));
  for (let i = 1; ; i++) if (!used.has(`p${i}`)) return `p${i}`;
}

export function newPart(parts: PartEdit[]): PartEdit {
  return { key: nextKey(parts), label: String.fromCharCode(97 + parts.length), prompt: [], answer_type: "choice", multi: false,
           options: [{ label: "A", content: [] }, { label: "B", content: [] }, { label: "C", content: [] }, { label: "D", content: [] }],
           solution: null, points: 1 };
}

type Keys = Record<string, Answer>;

export default function PartsEditor({ parts, answer, canEdit, onChange }: {
  parts: PartEdit[]; answer: Answer | null; canEdit: boolean;
  onChange: (parts: PartEdit[], answer: Answer | null) => void;
}) {
  const keys: Keys = answer && answer.kind === "parts" ? answer.parts : {};
  const emit = (ps: PartEdit[], ks: Keys) => onChange(ps, Object.keys(ks).length ? { kind: "parts", parts: ks } : null);
  const setPart = (i: number, p: Partial<PartEdit>, resetKey = false) => {
    const ps = parts.map((x, j) => (j === i ? { ...x, ...p } : x));
    const ks = { ...keys };
    if (resetKey) delete ks[parts[i].key];
    emit(ps, ks);
  };
  const setKey = (key: string, a: Answer | null) => {
    const ks = { ...keys };
    if (a) ks[key] = a; else delete ks[key];
    emit(parts, ks);
  };
  const remove = (i: number) => {
    const ks = { ...keys };
    delete ks[parts[i].key];
    emit(parts.filter((_, j) => j !== i), ks);
  };
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= parts.length) return;
    const ps = [...parts];
    [ps[i], ps[j]] = [ps[j], ps[i]];
    emit(ps, keys);
  };

  return (
    <fieldset className="ed-parts">
      <legend>Các ý <span className="small muted">— mỗi ý có đáp án, lời giải và điểm riêng; câu cha không có đáp án chung</span></legend>
      {parts.length === 0 && <div className="small muted">Chưa có ý nào. Bấm “+ Thêm ý”.</div>}
      {parts.map((p, i) => {
        const a = keys[p.key];
        return (
          <div key={p.key} className="ed-part">
            <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <input className="input sm" style={{ width: 56 }} aria-label="Nhãn ý" value={p.label} disabled={!canEdit}
                     onChange={(e) => setPart(i, { label: e.target.value.slice(0, 8) })} />
              <select className="input sm" aria-label="Dạng trả lời" value={p.answer_type} disabled={!canEdit}
                      onChange={(e) => {
                        const t = e.target.value as AnswerType;
                        setPart(i, { answer_type: t, multi: t === "choice" ? !!p.multi : undefined,
                                     options: t === "choice" ? (p.options && p.options.length ? p.options : newPart([]).options) : undefined }, true);
                      }}>
                {(Object.keys(TYPE_NAMES) as AnswerType[]).map((t) => <option key={t} value={t}>{TYPE_NAMES[t]}</option>)}
              </select>
              {p.answer_type === "choice" && (
                <label className="small"><input type="checkbox" checked={!!p.multi} disabled={!canEdit}
                                                onChange={(e) => setPart(i, { multi: e.target.checked }, true)} /> nhiều đáp án</label>
              )}
              <label className="small">Điểm <input className="input sm" style={{ width: 70 }} type="number" min={0.25} step={0.25}
                                                   value={p.points} disabled={!canEdit}
                                                   onChange={(e) => setPart(i, { points: Number(e.target.value) || 1 })} /></label>
              <span className="grow" />
              {canEdit && <>
                <button className="btn ghost sm" title="Lên" onClick={() => move(i, -1)} disabled={i === 0}>↑</button>
                <button className="btn ghost sm" title="Xuống" onClick={() => move(i, 1)} disabled={i === parts.length - 1}>↓</button>
                <button className="btn ghost sm" title="Xoá ý" onClick={() => { if (window.confirm(`Xoá ý ${p.label}?`)) remove(i); }}>✕</button>
              </>}
            </div>
            <BlockEditor label={`Câu hỏi ý ${p.label}`} compact value={p.prompt} readOnly={!canEdit}
                         onChange={(b) => setPart(i, { prompt: b })} placeholder="Nội dung câu hỏi của ý…" />
            {p.answer_type === "choice" && (
              <div className="ed-part-options">
                {(p.options || []).map((o, k) => {
                  const on = a?.kind === "choice" && a.labels.includes(o.label);
                  return (
                    <div key={k} className={"ed-option" + (on ? " correct" : "")}>
                      <label className="opt-label" title="Đáp án đúng">
                        <input type={p.multi ? "checkbox" : "radio"} name={`ans-${p.key}`} checked={on} disabled={!canEdit}
                               onChange={() => {
                                 const cur = a?.kind === "choice" ? a.labels : [];
                                 const labels = p.multi ? (on ? cur.filter((x) => x !== o.label) : [...cur, o.label].sort()) : [o.label];
                                 setKey(p.key, labels.length ? { kind: "choice", labels } : null);
                               }} /> {o.label}
                      </label>
                      <BlockEditor label={`Ý ${p.label} – phương án ${o.label}`} compact value={o.content} readOnly={!canEdit}
                                   onChange={(b) => setPart(i, { options: (p.options || []).map((x, m) => (m === k ? { ...x, content: b } : x)) })} />
                      {canEdit && (p.options || []).length > 2 && (
                        <button className="btn ghost sm" title="Xoá phương án" onClick={() => {
                          const opts = (p.options || []).filter((_, m) => m !== k).map((x, m) => ({ ...x, label: LETTERS[m] }));
                          setPart(i, { options: opts }, true);
                        }}>✕</button>
                      )}
                    </div>
                  );
                })}
                {canEdit && (p.options || []).length < 8 && (
                  <button className="btn ghost sm" onClick={() => setPart(i, { options: [...(p.options || []), { label: LETTERS[(p.options || []).length], content: [] }] })}>
                    + Thêm phương án
                  </button>
                )}
              </div>
            )}
            <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span className="label">Đáp án ý {p.label}:</span>
              {p.answer_type === "choice" && <span className="small muted">chọn ở cột trái của phương án</span>}
              {p.answer_type === "numeric" && (
                <input className="input sm" style={{ maxWidth: 160 }} disabled={!canEdit} placeholder="vd: 2,5"
                       value={a?.kind === "numeric" ? a.text : ""}
                       onChange={(e) => setKey(p.key, e.target.value.trim() ? { kind: "numeric", text: e.target.value, value: Number(e.target.value.replace(",", ".")) } : null)} />
              )}
              {p.answer_type === "text" && (
                <input className="input sm" style={{ flex: 1 }} disabled={!canEdit} value={a?.kind === "text" ? a.text : ""}
                       onChange={(e) => setKey(p.key, e.target.value.trim() ? { kind: "text", text: e.target.value } : null)} />
              )}
              {p.answer_type === "formula" && (
                <input className="input sm mono" style={{ flex: 1 }} disabled={!canEdit} placeholder="LaTeX, vd: 2x-4"
                       value={a?.kind === "formula" ? a.tex : ""}
                       onChange={(e) => setKey(p.key, e.target.value.trim() ? { kind: "formula", tex: e.target.value } : null)} />
              )}
              {p.answer_type === "boolean" && ["Đúng", "Sai"].map((l, k) => (
                <label key={l} className={"chip" + (a?.kind === "boolean" && a.value === (k === 0) ? " on" : "")}>
                  <input type="radio" name={`ansb-${p.key}`} disabled={!canEdit} checked={a?.kind === "boolean" && a.value === (k === 0)}
                         onChange={() => setKey(p.key, { kind: "boolean", value: k === 0 })} /> {l}
                </label>
              ))}
              {!a && <span className="badge warn">chưa có</span>}
            </div>
            <BlockEditor label={`Lời giải ý ${p.label}`} compact richPaste value={p.solution || []} readOnly={!canEdit}
                         onChange={(b) => setPart(i, { solution: b.length ? b : null })} placeholder="(lời giải của ý)" />
          </div>
        );
      })}
      {canEdit && parts.length < 12 && (
        <button className="btn secondary sm" onClick={() => emit([...parts, newPart(parts)], keys)}>+ Thêm ý</button>
      )}
    </fieldset>
  );
}
