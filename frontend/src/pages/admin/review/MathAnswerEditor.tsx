// Key of a "Biểu thức / Công thức toán" answer: canonical LaTeX (entered with MathLive), declared variables,
// tolerance and accepted alternatives. The server checks every form with its equivalence parser.
import MathInput from "../../../components/MathInput";

export interface MathKey { kind: "math_expression"; tex: string; accepted?: string[]; variables?: string[]; tolerance?: number }

export default function MathAnswerEditor({ value, onChange, canEdit, idPrefix }: {
  value: MathKey | null; onChange: (k: MathKey | null) => void; canEdit: boolean; idPrefix: string;
}) {
  const k: MathKey = value || { kind: "math_expression", tex: "" };
  const extra = (k.accepted || []).filter((t) => t !== k.tex);
  const emit = (p: Partial<MathKey>) => {
    const next = { ...k, ...p };
    const acc = [next.tex, ...(p.accepted ?? extra)];   // blanks stay while being typed; the server drops them
    onChange(next.tex.trim() ? { ...next, accepted: acc } : null);
  };
  return (
    <div className="stack" style={{ gap: 6, flex: 1 }}>
      <MathInput id={`${idPrefix}-tex`} label="Đáp án (biểu thức)" value={k.tex} disabled={!canEdit} onChange={(t) => emit({ tex: t })} />
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <label className="small">Biến <input className="input sm" style={{ width: 140 }} disabled={!canEdit} placeholder="vd: a, x"
                                                value={(k.variables || []).join(", ")}
                                                onChange={(e) => emit({ variables: e.target.value.split(/[,;\s]+/).filter(Boolean) })} /></label>
        <label className="small">Sai số <input className="input sm" style={{ width: 100 }} disabled={!canEdit} placeholder="1e-6"
                                                  value={k.tolerance ?? ""} type="number" step="any" min={0}
                                                  onChange={(e) => emit({ tolerance: e.target.value === "" ? undefined : Number(e.target.value) })} /></label>
        <span className="small muted">Chấm theo tương đương đại số: a√6/6, a/√6 và √6·a/6 là cùng đáp án.</span>
      </div>
      {extra.map((t, i) => (
        <div key={i} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
          <MathInput id={`${idPrefix}-acc-${i}`} label={`Đáp án chấp nhận khác ${i + 1}`} value={t} disabled={!canEdit}
                     onChange={(v) => emit({ accepted: extra.map((x, j) => (j === i ? v : x)) })} />
          {canEdit && <button className="btn ghost sm" title="Bỏ" onClick={() => emit({ accepted: extra.filter((_, j) => j !== i) })}>✕</button>}
        </div>
      ))}
      {canEdit && k.tex.trim() && (
        <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => emit({ accepted: [...extra, ""] })}>+ Đáp án chấp nhận khác</button>
      )}
    </div>
  );
}
