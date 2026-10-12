// Visual formula editor: MathLive (WYSIWYG, Word-equation-like) + LaTeX source + preview rendered with the
// student renderer (KaTeX). MathLive is loaded on first use only.
import { useEffect, useRef, useState } from "react";
import { renderTex } from "../../../components/RichContent";
import { Modal } from "../../../components/ui";
import { katexError } from "./tex";

type MathField = HTMLElement & {
  value: string;
  getValue: (fmt?: string) => string;
  setValue: (v: string, opts?: Record<string, unknown>) => void;
  executeCommand: (cmd: unknown) => boolean;
  insert: (s: string, opts?: Record<string, unknown>) => boolean;
  focus: () => void;
  mathVirtualKeyboardPolicy: string;
  smartFence: boolean;
};

let loading: Promise<unknown> | null = null;
function loadMathLive() {
  if (!loading) {
    loading = import("mathlive").then((ml) => {
      const MFE = (ml as unknown as { MathfieldElement: { fontsDirectory: string | null; soundsDirectory: string | null } }).MathfieldElement;
      MFE.fontsDirectory = "/mathlive/fonts";
      MFE.soundsDirectory = null;
      return ml;
    });
  }
  return loading;
}

// templates shown as buttons (#0 = selection/placeholder, #? = placeholder)
const PALETTE: { group: string; items: [string, string][] }[] = [
  { group: "Cấu trúc", items: [
    ["\\frac{#0}{#?}", "a/b"], ["\\sqrt{#0}", "√"], ["\\sqrt[#?]{#0}", "ⁿ√"], ["#0^{#?}", "xⁿ"], ["#0_{#?}", "xₙ"],
    ["\\left(#0\\right)", "( )"], ["\\left[#0\\right]", "[ ]"], ["\\left|#0\\right|", "| |"], ["\\left\\{#0\\right.", "{ hệ"],
    ["\\begin{cases}#0\\\\#?\\end{cases}", "hệ PT"], ["\\overline{#0}", "x̄"], ["\\vec{#0}", "vec"], ["\\overrightarrow{#0}", "→AB"],
  ] },
  { group: "Giải tích", items: [
    ["\\int_{#?}^{#?}#0\\,dx", "∫"], ["\\int #0\\,dx", "∫dx"], ["\\sum_{#?}^{#?}#0", "Σ"], ["\\prod_{#?}^{#?}#0", "Π"],
    ["\\lim_{#?\\to #?}#0", "lim"], ["\\log_{#?}#0", "log"], ["\\ln #0", "ln"], ["\\sin #0", "sin"], ["\\cos #0", "cos"],
    ["\\tan #0", "tan"], ["#0'", "f′"], ["\\infty", "∞"],
  ] },
  { group: "Ma trận", items: [
    ["\\begin{pmatrix}#0 & #?\\\\#? & #?\\end{pmatrix}", "(2×2)"], ["\\begin{vmatrix}#0 & #?\\\\#? & #?\\end{vmatrix}", "|2×2|"],
    ["\\begin{pmatrix}#0 & #? & #?\\\\#? & #? & #?\\\\#? & #? & #?\\end{pmatrix}", "(3×3)"],
  ] },
  { group: "Hy Lạp", items: [
    ["\\alpha", "α"], ["\\beta", "β"], ["\\gamma", "γ"], ["\\delta", "δ"], ["\\Delta", "Δ"], ["\\varepsilon", "ε"],
    ["\\lambda", "λ"], ["\\mu", "μ"], ["\\pi", "π"], ["\\rho", "ρ"], ["\\sigma", "σ"], ["\\varphi", "φ"], ["\\omega", "ω"], ["\\Omega", "Ω"],
  ] },
  { group: "Quan hệ & hình học", items: [
    ["\\le", "≤"], ["\\ge", "≥"], ["\\ne", "≠"], ["\\approx", "≈"], ["\\pm", "±"], ["\\cdot", "·"], ["\\times", "×"], ["\\div", "÷"],
    ["\\in", "∈"], ["\\notin", "∉"], ["\\subset", "⊂"], ["\\cup", "∪"], ["\\cap", "∩"], ["\\emptyset", "∅"], ["\\Rightarrow", "⇒"],
    ["\\Leftrightarrow", "⇔"], ["\\angle", "∠"], ["\\triangle", "△"], ["\\perp", "⊥"], ["\\parallel", "∥"], ["^{\\circ}", "°"],
    ["\\equiv", "≡"], ["\\sim", "∼"], ["\\cong", "≅"], ["\\widehat{#0}", "góc"],
  ] },
];

export default function MathDialog({ initial, onSave, onClose, onDelete }: {
  initial: string; onSave: (tex: string, display: boolean) => void; onClose: () => void; onDelete?: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const mf = useRef<MathField | null>(null);
  const [tex, setTex] = useState(initial);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [display, setDisplay] = useState(false);

  useEffect(() => {
    let alive = true;
    loadMathLive().then(() => {
      if (!alive || !host.current) return;
      const el = document.createElement("math-field") as MathField;
      el.setAttribute("aria-label", "Soạn công thức");
      el.className = "ml-field";
      el.mathVirtualKeyboardPolicy = "manual";
      el.smartFence = true;
      host.current.appendChild(el);
      el.setValue(initial, { silenceNotifications: true });
      el.addEventListener("input", () => setTex(el.getValue("latex")));
      mf.current = el;
      setReady(true);
      setTimeout(() => el.focus(), 30);
    }).catch(() => alive && setFailed(true));
    return () => { alive = false; mf.current?.remove(); mf.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const insert = (t: string) => {
    const el = mf.current;
    if (el) { el.insert(t, { selectionMode: "placeholder", format: "latex" }); el.focus(); setTex(el.getValue("latex")); }
    else setTex((x) => x + t.replace(/#0|#\?/g, ""));
  };
  const fromSource = (v: string) => {
    setTex(v);
    mf.current?.setValue(v, { silenceNotifications: true });
  };
  const error = tex.trim() ? katexError(tex) : null;

  return (
    <Modal title="Công thức" wide onClose={onClose} actions={<>
      {onDelete && <button className="btn ghost" onClick={onDelete}>Xoá công thức</button>}
      <button className="btn secondary" onClick={onClose}>Huỷ</button>
      <button className="btn" disabled={!tex.trim()} onClick={() => onSave(tex.trim(), display)}>Chèn / cập nhật</button>
    </>}>
      <div className="math-dialog">
        <div className="small muted mb">Soạn trực quan như Word Equation: gõ <code>/</code> để tạo phân số, <code>^</code> số mũ, <code>_</code> chỉ số,
          <code>sqrt</code> căn… hoặc bấm các nút bên dưới. Có thể dán/sửa LaTeX ở ô bên dưới.</div>
        <div ref={host} className="ml-host">{!ready && !failed && <span className="muted small">Đang tải trình soạn công thức…</span>}
          {failed && <span className="alert warn small">Không tải được trình soạn trực quan; hãy nhập LaTeX.</span>}</div>
        <div className="palette">
          {PALETTE.map((g) => (
            <div key={g.group} className="pal-group">
              <div className="pal-title">{g.group}</div>
              <div className="pal-items">
                {g.items.map(([t, label]) => (
                  <button key={t} type="button" className="pal-btn" title={t.replace(/#0|#\?/g, "□")} onClick={() => insert(t)}>{label}</button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <label className="label" htmlFor="tex-src">LaTeX</label>
        <textarea id="tex-src" className="input mono" rows={3} value={tex} onChange={(e) => fromSource(e.target.value)}
                  onPaste={(e) => {
                    const t = e.clipboardData.getData("text/plain");
                    const m = t.match(/^\s*(?:\$\$?|\\\(|\\\[)([\s\S]*?)(?:\$\$?|\\\)|\\\])\s*$/);
                    if (m) { e.preventDefault(); fromSource(m[1].trim()); }
                  }} />
        <div className="row between mt">
          <span className="label">Xem trước (giống trang học sinh)</span>
          <label className="small"><input type="checkbox" checked={display} onChange={(e) => setDisplay(e.target.checked)} /> Công thức riêng dòng</label>
        </div>
        <div className={"math-preview" + (error ? " bad" : "")}>
          {tex.trim() ? <span dangerouslySetInnerHTML={{ __html: renderTex(tex, display) }} /> : <span className="muted">—</span>}
        </div>
        {error && <div className="alert error small mt">KaTeX không hiển thị được: {error}</div>}
      </div>
    </Modal>
  );
}
