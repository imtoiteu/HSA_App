import { useState } from "react";
import type { Answer, Group, InputSpec, OptionView, PartResult, PartView, QuestionView, Response } from "../lib/types";
import MathInput from "./MathInput";
import { RichContent } from "./RichContent";


export function Passage({ group, positions, lang }: { group: Group; positions?: number[]; lang?: string }) {
  const [collapsed, setCollapsed] = useState(false);
  const long = JSON.stringify(group.passage).length > 2500;
  return (
    <section className={"passage" + (collapsed ? " collapsed" : "")} aria-label="Dữ liệu dùng chung">
      {group.header.length > 0 && (
        <div className="passage-head"><RichContent blocks={group.header} range={positions} lang={lang} /></div>
      )}
      <div className="passage-body"><RichContent blocks={group.passage} /></div>
      {long && (
        <button className="btn ghost sm mt" onClick={() => setCollapsed((c) => !c)}>
          {collapsed ? "Xem toàn bộ đoạn" : "Thu gọn đoạn"}
        </button>
      )}
    </section>
  );
}

type Reveal = { answer?: Answer | null; response?: Response };

function optionState(opt: OptionView, selected: boolean, reveal?: Reveal): string {
  if (!reveal || !reveal.answer || reveal.answer.kind !== "choice") return selected ? "selected" : "";
  const correct = reveal.answer.labels.includes(opt.key);
  if (correct) return "correct";
  if (selected) return "wrong";
  return "";
}

const PART_OUTCOME: Record<string, [string, string]> = {
  correct: ["Đúng", "ok"], incorrect: ["Sai", "bad"], partial: ["Đúng một phần", "warn"], unanswered: ["Chưa trả lời", ""],
  ungraded: ["Chưa chấm", ""],
};

/** The items of a multi-part question: each with its own prompt, input and — once revealed — key and solution. */
/** Items and their keys/solutions from a raw content document (admin previews, unsaved editor state). */
export function partsFromContent(content: any, answer: Answer | null | undefined): { parts: PartView[]; results: PartResult[] } {
  const keys: Record<string, Answer> = answer && answer.kind === "parts" ? answer.parts : {};
  const ps: any[] = content?.type === "multi_part" ? content.parts || [] : [];
  const parts: PartView[] = ps.map((p) => ({
    key: p.key, label: p.label ?? null, prompt: p.prompt || [], points: Number(p.points || 1),
    input: p.answer_type === "choice" ? { kind: "choice", multi: !!p.multi } : { kind: p.answer_type || "none" } as InputSpec,
    options: p.answer_type === "choice" ? (p.options || []).map((o: any) => ({ key: o.label, display: o.label, content: o.content || [] })) : undefined,
  }));
  const results: PartResult[] = ps.map((p) => ({
    key: p.key, label: p.label ?? null, outcome: keys[p.key] ? "unanswered" : "ungraded", fraction: 0, points: Number(p.points || 1),
    awarded: 0, answer: keys[p.key] || null, solution: p.solution || null,
  }));
  return { parts, results };
}

export function PartsInput({ parts, response, onChange, disabled, results, idPrefix = "p", graded = true }: {
  parts: PartView[]; response: Response; onChange: (r: Response) => void; disabled?: boolean; results?: PartResult[];
  idPrefix?: string; graded?: boolean;
}) {
  const cur = response?.parts || {};
  const byKey = Object.fromEntries((results || []).map((r) => [r.key, r]));
  return (
    <ol className="parts" aria-label="Các ý của câu hỏi">
      {parts.map((p) => {
        const res = byKey[p.key];
        const set = (r: Response) => {
          const next = { ...cur };
          if (r) next[p.key] = r; else delete next[p.key];
          onChange(Object.keys(next).length ? { parts: next } : null);
        };
        const [lbl, tone] = res ? PART_OUTCOME[res.outcome] || ["", ""] : ["", ""];
        return (
          <li key={p.key} className={"part" + (res ? " part-" + res.outcome : "")}>
            <div className="part-head">
              <span className="part-label">{p.label ? `${p.label})` : ""}</span>
              <div className="part-prompt"><RichContent blocks={p.prompt} /></div>
              <span className="small muted">{p.points} điểm</span>
            </div>
            <AnswerInput input={p.input} options={p.options || []} response={cur[p.key] || null} onChange={set}
                         disabled={disabled} idPrefix={`${idPrefix}-${p.key}`}
                         reveal={res ? { answer: res.answer, response: cur[p.key] || null } : undefined} />
            {res && (
              <div className="part-result">
                {graded && <span className={"badge " + tone}>{lbl}{res.outcome !== "ungraded" ? ` · ${res.awarded}/${res.points} điểm` : ""}</span>}
                {res.answer ? <span className="small">Đáp án: <b><AnswerValue a={res.answer} /></b></span>
                  : <span className="badge warn">Ý này chưa có đáp án</span>}
                {res.solution && res.solution.length > 0 && (
                  <details className="part-solution" open><summary>Lời giải ý {p.label}</summary><RichContent blocks={res.solution} /></details>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function AnswerInput({ input, options, response, onChange, disabled, reveal, parts, partResults, idPrefix = "ans",
                             graded = true }: {
  input: InputSpec; options: OptionView[]; response: Response; onChange: (r: Response) => void; disabled?: boolean; reveal?: Reveal;
  parts?: PartView[]; partResults?: PartResult[]; idPrefix?: string; graded?: boolean;
}) {
  if (input.kind === "parts") {
    return <PartsInput parts={parts || []} response={response} onChange={onChange} disabled={disabled} results={partResults}
                       idPrefix={idPrefix} graded={graded} />;
  }
  if (input.kind === "math" || input.kind === "formula") {
    return (
      <div className="answer-box">
        <MathInput id={`${idPrefix}-math`} label="Đáp án (biểu thức):" value={response?.text ?? ""} disabled={disabled}
                   onChange={(t) => onChange(t.trim() ? { text: t } : null)} />
        <span className="hint">Nhập biểu thức; các cách viết tương đương đều được tính đúng.</span>
      </div>
    );
  }
  if (input.kind === "choice") {
    const labels = response?.labels || [];
    const toggle = (key: string) => {
      if (disabled) return;
      if (input.multi) {
        const next = labels.includes(key) ? labels.filter((x) => x !== key) : [...labels, key].sort();
        onChange(next.length ? { labels: next } : null);
      } else {
        onChange(labels.includes(key) ? null : { labels: [key] });
      }
    };
    return (
      <div className="options" role={input.multi ? "group" : "radiogroup"} aria-label="Các phương án">
        {input.multi && <div className="hint">Có thể chọn nhiều phương án.</div>}
        {options.map((o) => {
          const sel = labels.includes(o.key);
          const st = optionState(o, sel, reveal);
          return (
            <button key={o.key} type="button" className={"option " + st} disabled={disabled}
                    role={input.multi ? "checkbox" : "radio"} aria-checked={sel} onClick={() => toggle(o.key)}>
              <span className="opt-label">{o.display}</span>
              <RichContent blocks={o.content} />
              {st === "correct" && <span className="opt-mark" style={{ color: "var(--ok)" }}>Đáp án đúng</span>}
              {st === "wrong" && <span className="opt-mark" style={{ color: "var(--bad)" }}>Bạn chọn</span>}
            </button>
          );
        })}
      </div>
    );
  }
  if (input.kind === "numeric" || input.kind === "text") {
    const v = input.kind === "numeric" ? ((response?.value as string) ?? "") : (response?.text ?? "");
    return (
      <div className="answer-box">
        <label className="label" htmlFor={`${idPrefix}-input`}>{input.kind === "numeric" ? "Đáp số:" : "Câu trả lời:"}</label>
        <input id={`${idPrefix}-input`} className="input" value={v} disabled={disabled} autoComplete="off"
               inputMode={input.kind === "numeric" ? "decimal" : "text"}
               placeholder={input.kind === "numeric" ? "Ví dụ: 2,5 hoặc -3" : "Nhập câu trả lời"}
               onChange={(e) => {
                 const t = e.target.value;
                 if (input.kind === "numeric") onChange(t.trim() ? { value: t } : null);
                 else onChange(t.trim() ? { text: t } : null);
               }} />
        {input.kind === "numeric" && <span className="hint">Dùng dấu phẩy hoặc dấu chấm cho số thập phân.</span>}
      </div>
    );
  }
  if (input.kind === "tf_sequence") {
    const vals = response?.values || Array(input.n).fill(null);
    const set = (i: number, v: boolean) => {
      const next = [...vals];
      while (next.length < input.n) next.push(null);
      next[i] = next[i] === v ? null : v;
      onChange(next.some((x) => x !== null) ? { values: next } : null);
    };
    const key = reveal?.answer && reveal.answer.kind === "tf_sequence" ? reveal.answer.values : null;
    return (
      <div className="tf-table" role="group" aria-label="Chọn Đúng hoặc Sai cho từng ý">
        {Array.from({ length: input.n }).map((_, i) => (
          <div className="tf-row" key={i}>
            <span className="lbl">{String.fromCharCode(97 + i)})</span>
            <div className="seg">
              <button type="button" className={vals[i] === true ? "on" : ""} disabled={disabled} onClick={() => set(i, true)}>Đúng</button>
              <button type="button" className={vals[i] === false ? "on" : ""} disabled={disabled} onClick={() => set(i, false)}>Sai</button>
            </div>
            {key && <span className={"badge " + (key[i] === vals[i] ? "ok" : "bad")}>Đáp án: {key[i] ? "Đúng" : "Sai"}</span>}
          </div>
        ))}
      </div>
    );
  }
  if (input.kind === "boolean") {
    const v = response?.value;
    return (
      <div className="answer-box">
        <div className="seg">
          <button type="button" className={v === true ? "on" : ""} disabled={disabled} onClick={() => onChange(v === true ? null : { value: true })}>Đúng</button>
          <button type="button" className={v === false ? "on" : ""} disabled={disabled} onClick={() => onChange(v === false ? null : { value: false })}>Sai</button>
        </div>
      </div>
    );
  }
  // constructed response: free notes, compared by the student with the worked solution
  return (
    <div className="stack mt">
      <label className="label" htmlFor="ans-notes">Bài làm của bạn (không chấm tự động):</label>
      <textarea id="ans-notes" className="input" value={response?.text ?? ""} disabled={disabled}
                onChange={(e) => onChange(e.target.value.trim() ? { text: e.target.value } : null)} />
    </div>
  );
}

/** A key as shown to the student: expressions rendered as formulas, everything else as text. */
export function AnswerValue({ a, display }: { a: Answer | null | undefined; display?: string[] }) {
  if (a && (a.kind === "math_expression" || a.kind === "formula")) {
    return <span className="inline-math"><RichContent blocks={[{ t: "p", c: [{ t: "m", tex: a.tex }] }]} /></span>;
  }
  return <>{answerText(a, display)}</>;
}

export function answerText(a: Answer | null | undefined, display?: string[]): string {
  if (!a) return "Chưa có đáp án";
  switch (a.kind) {
    case "choice": return (display && display.length ? display : a.labels).join(", ");
    case "numeric": return a.text + (a.unit ? ` ${a.unit}` : "");
    case "text": return a.text;
    case "tf_sequence": return a.values.map((v, i) => `${String.fromCharCode(97 + i)}) ${v ? "Đúng" : "Sai"}`).join("; ");
    case "boolean": return a.value ? "Đúng" : "Sai";
    case "formula": return a.tex;
    case "math_expression": return a.tex;
    case "parts": return Object.keys(a.parts).length ? "đáp án và lời giải ở từng ý" : "Chưa có đáp án";
  }
}

export function Solution({ q, answer, answerDisplay }: { q: QuestionView | { solution?: any; explanation?: any }; answer?: Answer | null; answerDisplay?: string[] }) {
  const sol = (q as any).solution, expl = (q as any).explanation;
  const multi = answer?.kind === "parts" || (q as any).question?.type === "multi_part" || (q as any).type === "multi_part";
  if (multi && !sol && !expl) return null;   // each item shows its own key and solution
  return (
    <div className="solution">
      {!multi && <div className="key-line">Đáp án: <span className="badge ok" style={{ fontSize: ".95rem" }}><AnswerValue a={answer} display={answerDisplay} /></span></div>}
      {expl && <><h4>Phương pháp</h4><RichContent blocks={expl} /></>}
      {sol && <><h4 style={{ marginTop: expl ? 12 : 0 }}>Lời giải</h4><RichContent blocks={sol} /></>}
      {!sol && !expl && !multi && <div className="muted small">Tài liệu nguồn chưa có lời giải chi tiết cho câu này.</div>}
    </div>
  );
}
