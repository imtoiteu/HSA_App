import { useState } from "react";
import type { Answer, Group, InputSpec, OptionView, QuestionView, Response } from "../lib/types";
import { RichContent } from "./RichContent";

export function rangeText(positions: number[] | undefined, lang?: string): string | undefined {
  if (!positions || !positions.length) return undefined;
  const a = positions[0], b = positions[positions.length - 1];
  if (lang === "en") return a === b ? `question ${a}` : `questions ${a}–${b}`;
  return a === b ? `câu ${a}` : `từ câu ${a} đến câu ${b}`;
}

export function Passage({ group, positions, lang }: { group: Group; positions?: number[]; lang?: string }) {
  const [collapsed, setCollapsed] = useState(false);
  const long = JSON.stringify(group.passage).length > 2500;
  return (
    <section className={"passage" + (collapsed ? " collapsed" : "")} aria-label="Dữ liệu dùng chung">
      {group.header.length > 0 && (
        <div className="passage-head"><RichContent blocks={group.header} range={rangeText(positions, lang)} /></div>
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

export function AnswerInput({ input, options, response, onChange, disabled, reveal }: {
  input: InputSpec; options: OptionView[]; response: Response; onChange: (r: Response) => void; disabled?: boolean; reveal?: Reveal;
}) {
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
        <label className="label" htmlFor="ans-input">{input.kind === "numeric" ? "Đáp số:" : "Câu trả lời:"}</label>
        <input id="ans-input" className="input" value={v} disabled={disabled} autoComplete="off"
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

export function answerText(a: Answer | null | undefined, display?: string[]): string {
  if (!a) return "Chưa có đáp án";
  switch (a.kind) {
    case "choice": return (display && display.length ? display : a.labels).join(", ");
    case "numeric": return a.text + (a.unit ? ` ${a.unit}` : "");
    case "text": return a.text;
    case "tf_sequence": return a.values.map((v, i) => `${String.fromCharCode(97 + i)}) ${v ? "Đúng" : "Sai"}`).join("; ");
    case "boolean": return a.value ? "Đúng" : "Sai";
  }
}

export function Solution({ q, answer, answerDisplay }: { q: QuestionView | { solution?: any; explanation?: any }; answer?: Answer | null; answerDisplay?: string[] }) {
  const sol = (q as any).solution, expl = (q as any).explanation;
  return (
    <div className="solution">
      <div className="key-line">Đáp án: <span className="badge ok" style={{ fontSize: ".95rem" }}>{answerText(answer, answerDisplay)}</span></div>
      {expl && <><h4>Phương pháp</h4><RichContent blocks={expl} /></>}
      {sol && <><h4 style={{ marginTop: expl ? 12 : 0 }}>Lời giải</h4><RichContent blocks={sol} /></>}
      {!sol && !expl && <div className="muted small">Tài liệu nguồn chưa có lời giải chi tiết cho câu này.</div>}
    </div>
  );
}
