import katex from "katex";
import { memo, useState, type ReactNode } from "react";
import { normalizeTex, repairTex } from "../lib/tex";
import type { Block, Inline, Mark } from "../lib/types";
import { Modal } from "./ui";

const MEDIA = "/media/";
const cache = new Map<string, string>();

export function renderTex(tex: string, display = false): string {
  const key = (display ? "D" : "I") + tex;
  let html = cache.get(key);
  if (html === undefined) {
    const opts = { displayMode: display, throwOnError: true, strict: "ignore" as const, trust: false, output: "html" as const };
    try {
      html = katex.renderToString(normalizeTex(tex), opts);
    } catch {
      try {
        // a known conversion artefact (double script): repaired only when the formula does not render as it is
        html = katex.renderToString(repairTex(normalizeTex(tex)), opts);
      } catch {
        // keep the source visible instead of dropping it
        html = `<span class="math-error" title="Công thức chưa hiển thị được">${escapeHtml(tex)}</span>`;
      }
    }
    if (cache.size > 4000) cache.clear();
    cache.set(key, html);
  }
  return html;
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

function wrapMarks(node: ReactNode, marks: Mark[] | undefined, key: number): ReactNode {
  let out = node;
  for (const m of marks || []) {
    if (m === "b") out = <strong>{out}</strong>;
    else if (m === "i") out = <em>{out}</em>;
    else if (m === "u") out = <u>{out}</u>;
    else if (m === "x") out = <s>{out}</s>;
    else if (m === "sup") out = <sup>{out}</sup>;
    else if (m === "sub") out = <sub>{out}</sub>;
  }
  return <span key={key}>{out}</span>;
}

export interface RenderCtx {
  range?: number[]; // positions of the group's questions, replacing {{range}} in group headers
  lang?: string;
  onZoom?: (src: string) => void;
}

/** "từ câu 5 đến câu 7" — or just "5–7" when the source text already says "câu …" before it. */
export function rangeLabel(positions: number[] | undefined, lang: string | undefined, prevText: string): string {
  if (!positions || !positions.length) return "…";
  const a = positions[0], b = positions[positions.length - 1];
  const bare = /(câu(\s+hỏi)?|questions?|số)\s*$/i.test(prevText);
  if (bare) return a === b ? `${a}` : lang === "en" ? `${a}–${b}` : `${a} đến ${b}`;
  if (lang === "en") return a === b ? `question ${a}` : `questions ${a}–${b}`;
  return a === b ? `câu ${a}` : `từ câu ${a} đến câu ${b}`;
}

function Inlines({ nodes, ctx }: { nodes: Inline[]; ctx: RenderCtx }) {
  return (
    <>
      {nodes.map((n, i) => {
        switch (n.t) {
          case "s":
            return n.m && n.m.length ? wrapMarks(n.v, n.m, i) : <span key={i}>{n.v}</span>;
          case "m":
            return <span key={i} className="math-inline" dangerouslySetInnerHTML={{ __html: renderTex(n.tex) }} />;
          case "br":
            return <br key={i} />;
          case "tab":
            return <span key={i}>{" "}</span>;
          case "range": {
            const prev = nodes[i - 1];
            return <span key={i}>{rangeLabel(ctx.range, ctx.lang, prev && prev.t === "s" ? prev.v : "")}</span>;
          }
          case "warn":
            return <span key={i} className="warn-node">⚠ {n.v}</span>;
          case "img":
            return <img key={i} className="inline-img" src={MEDIA + n.src} alt="" loading="lazy" />;
          default:
            return null;
        }
      })}
    </>
  );
}

function BlockView({ b, ctx }: { b: Block; ctx: RenderCtx }) {
  switch (b.t) {
    case "p":
      if (b.display && b.c.length === 1 && b.c[0].t === "m") {
        return <div className="display-math" dangerouslySetInnerHTML={{ __html: renderTex(b.c[0].tex, true) }} />;
      }
      return <p><Inlines nodes={b.c} ctx={ctx} /></p>;
    case "img": {
      // show figures at their natural size up to the column width; small figures are not blown up
      const style = b.w ? { width: Math.min(b.w, 640) } : undefined;
      return (
        <figure>
          <img src={MEDIA + b.src} alt={b.alt || "Hình minh hoạ"} loading="lazy" style={style} width={b.w} height={b.h}
               onClick={() => ctx.onZoom?.(b.src)} />
          {b.caption && <figcaption>{b.caption}</figcaption>}
        </figure>
      );
    }
    case "list": {
      const items = b.items.map((it, i) => <li key={i}>{it.map((cb, k) => <BlockView key={k} b={cb} ctx={ctx} />)}</li>);
      return b.ordered ? <ol>{items}</ol> : <ul>{items}</ul>;
    }
    case "table":
      return (
        <div className="q-table-wrap">
          <table className="q-table">
            <tbody>
              {b.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, c) => (
                    <td key={c} colSpan={cell.colspan} rowSpan={cell.rowspan}>
                      {cell.c.map((cb, k) => <BlockView key={k} b={cb} ctx={ctx} />)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "warn":
      return <p><span className="warn-node">⚠ {b.v}</span></p>;
    default:
      return null;
  }
}

export const RichContent = memo(function RichContent({ blocks, range, lang, className }: {
  blocks: Block[] | null | undefined; range?: number[]; lang?: string; className?: string;
}) {
  const [zoom, setZoom] = useState<string | null>(null);
  if (!blocks || !blocks.length) return null;
  const ctx: RenderCtx = { range, lang, onZoom: setZoom };
  return (
    <div className={"rich " + (className || "")}>
      {blocks.map((b, i) => <BlockView key={i} b={b} ctx={ctx} />)}
      {zoom && (
        <Modal onClose={() => setZoom(null)} wide title="Hình phóng to">
          <img src={MEDIA + zoom} alt="Hình phóng to" style={{ width: "100%", height: "auto" }} />
        </Modal>
      )}
    </div>
  );
});
