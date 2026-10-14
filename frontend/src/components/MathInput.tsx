// Formula input (MathLive, Word-equation-like) used by students for "Biểu thức / Công thức toán" answers and by
// the Review Studio for their keys. MathLive is loaded on first use; until then (or if it fails) a LaTeX text box
// with a KaTeX preview is shown.
import { createElement, useEffect, useRef, useState } from "react";
import { RichContent } from "./RichContent";

type MathField = HTMLElement & { value: string; mathVirtualKeyboardPolicy: string; smartFence: boolean };

let loading: Promise<boolean> | null = null;
function loadMathLive(): Promise<boolean> {
  if (!loading) {
    loading = import("mathlive").then((ml) => {
      const MFE = (ml as unknown as { MathfieldElement: { fontsDirectory: string | null; soundsDirectory: string | null } }).MathfieldElement;
      MFE.fontsDirectory = "/mathlive/fonts";
      MFE.soundsDirectory = null;
      return true;
    }).catch(() => false);
  }
  return loading;
}

export default function MathInput({ value, onChange, disabled, id, label, placeholder }: {
  value: string; onChange: (latex: string) => void; disabled?: boolean; id?: string; label?: string; placeholder?: string;
}) {
  const [ready, setReady] = useState(false);
  const [raw, setRaw] = useState(false);   // the reviewer/student prefers typing LaTeX
  const ref = useRef<MathField | null>(null);
  useEffect(() => { let alive = true; loadMathLive().then((ok) => { if (alive) setReady(ok); }); return () => { alive = false; }; }, []);
  useEffect(() => {
    const mf = ref.current;
    if (!mf || !ready || raw) return;
    if (mf.value !== value) mf.value = value;
    const onInput = () => onChange(mf.value);
    mf.addEventListener("input", onInput);
    return () => mf.removeEventListener("input", onInput);
  }, [ready, raw, value, onChange]);
  useEffect(() => {
    const mf = ref.current;
    if (mf && ready && !raw) { mf.smartFence = true; mf.mathVirtualKeyboardPolicy = "auto"; }
  }, [ready, raw]);
  return (
    <div className="math-input">
      {label && <label className="label" htmlFor={id}>{label}</label>}
      {ready && !raw ? (
        createElement("math-field", { ref, id, class: "input math-field", "read-only": disabled ? "" : undefined,
                                      "aria-label": label || "Nhập biểu thức", "math-virtual-keyboard-policy": "auto" })
      ) : (
        <input id={id} className="input mono" value={value} disabled={disabled} spellCheck={false} autoComplete="off"
               placeholder={placeholder || "LaTeX, vd: \\frac{a\\sqrt{6}}{6}"} onChange={(e) => onChange(e.target.value)} />
      )}
      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        {ready && !disabled && (
          <button type="button" className="btn ghost sm" onClick={() => setRaw((r) => !r)}>
            {raw ? "Nhập trực quan" : "Nhập LaTeX"}
          </button>
        )}
        {raw && value.trim() && <div className="formula-preview"><RichContent blocks={[{ t: "p", c: [{ t: "m", tex: value }] }]} /></div>}
      </div>
    </div>
  );
}
