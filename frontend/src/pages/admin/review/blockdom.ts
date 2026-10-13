// Content model (Block[]) <-> editable DOM of the Review Studio editor.
// The editor is a contenteditable element; formulas, figures, placeholders and range tokens are atomic
// (contenteditable=false) nodes carrying their data in data-* attributes. Parsing the DOM back always yields the
// same model the backend sanitises (review/content.py), so pasted HTML is reduced to it as well.
import { renderTex } from "../../../components/RichContent";
import type { Block, Cell, Inline, Mark } from "../../../lib/types";

const MEDIA = "/media/";
const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const MARK_TAG: Record<Mark, string> = { b: "strong", i: "em", u: "u", x: "s", sup: "sup", sub: "sub" };

export function mathHtml(tex: string, review?: boolean, display?: boolean): string {
  return `<span class="ed-math${review ? " review" : ""}${display ? " ed-math-display" : ""}" contenteditable="false" data-tex="${esc(tex)}"${review ? ' data-review="1"' : ""} title="Bấm để sửa công thức">${renderTex(tex, display)}</span>`;
}

function inlineHtml(nodes: Inline[]): string {
  return nodes.map((n) => {
    switch (n.t) {
      case "s": {
        let h = esc(n.v).replace(/\t/g, "&#9;");
        for (const m of n.m || []) h = `<${MARK_TAG[m]}>${h}</${MARK_TAG[m]}>`;
        return h;
      }
      case "m": return mathHtml(n.tex, n.review);
      case "br": return "<br>";
      case "tab": return "&#9;";
      case "range": return '<span class="ed-token" contenteditable="false" data-range="1">[phạm vi câu]</span>';
      case "warn": return `<span class="ed-warn" contenteditable="false" data-warn="${esc(n.v)}">⚠ ${esc(n.v)}</span>`;
      case "img":
        return `<img class="ed-img-inline${n.review ? " review" : ""}" data-src="${esc(n.src)}" src="${MEDIA}${esc(n.src)}"${n.w ? ` data-w="${n.w}"` : ""}${n.h ? ` data-h="${n.h}"` : ""}${n.review ? ' data-review="1"' : ""} alt="">`;
      default: return "";
    }
  }).join("");
}

export function blocksToHtml(blocks: Block[] | null | undefined): string {
  return (blocks || []).map((b) => {
    switch (b.t) {
      case "p":
        if (b.display && b.c.length === 1 && b.c[0].t === "m")
          return `<p data-display="1">${mathHtml(b.c[0].tex, b.c[0].review, true)}</p>`;
        return `<p${b.display ? ' data-display="1"' : ""}>${inlineHtml(b.c) || "<br>"}</p>`;
      case "img":
        return `<figure class="ed-figure" contenteditable="false" data-src="${esc(b.src)}"${b.w ? ` data-w="${b.w}"` : ""}${b.h ? ` data-h="${b.h}"` : ""} data-alt="${esc(b.alt || "")}" data-caption="${esc(b.caption || "")}"><img src="${MEDIA}${esc(b.src)}" alt="${esc(b.alt || "")}" style="max-width:min(100%, ${Math.min(b.w || 640, 640)}px)">${b.caption ? `<figcaption>${esc(b.caption)}</figcaption>` : ""}</figure>`;
      case "table":
        return `<table class="ed-table"><tbody>${b.rows.map((r) => `<tr>${r.map((c) => `<td${c.colspan ? ` colspan="${c.colspan}"` : ""}${c.rowspan ? ` rowspan="${c.rowspan}"` : ""}>${blocksToHtml(c.c) || "<p><br></p>"}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      case "list": {
        const tag = b.ordered ? "ol" : "ul";
        return `<${tag}>${b.items.map((it) => it.length === 1 && it[0].t === "p"
          ? (it[0].display && it[0].c.length === 1 && it[0].c[0].t === "m"
            ? `<li data-display="1">${mathHtml(it[0].c[0].tex, it[0].c[0].review, true)}</li>` : `<li>${inlineHtml(it[0].c)}</li>`)
          : `<li>${blocksToHtml(it)}</li>`).join("")}</${tag}>`;
      }
      case "warn":
        return `<p><span class="ed-warn" contenteditable="false" data-warn="${esc(b.v)}">⚠ ${esc(b.v)}</span></p>`;
      default:
        return "";
    }
  }).join("");
}

// ------------------------------------------------------------------------------------------------
// DOM -> model
// ------------------------------------------------------------------------------------------------
const TAG_MARK: Record<string, Mark> = { B: "b", STRONG: "b", I: "i", EM: "i", U: "u", INS: "u", S: "x", STRIKE: "x", DEL: "x", SUP: "sup", SUB: "sub" };
const BLOCK_TAGS = new Set(["P", "DIV", "H1", "H2", "H3", "H4", "H5", "H6", "FIGURE", "TABLE", "UL", "OL", "BLOCKQUOTE", "PRE", "SECTION", "ARTICLE"]);
const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : undefined);

function pushText(out: Inline[], text: string, marks: Mark[]) {
  if (!text) return;
  const parts = text.replace(/ /g, " ").split("\t");
  parts.forEach((part, i) => {
    if (i > 0) out.push({ t: "tab" });
    if (!part) return;
    const m = [...new Set(marks)].sort() as Mark[];
    const last = out[out.length - 1];
    if (last && last.t === "s" && JSON.stringify(last.m || []) === JSON.stringify(m)) last.v += part;
    else out.push(m.length ? { t: "s", v: part, m } : { t: "s", v: part });
  });
}

function styleMarks(el: HTMLElement): Mark[] {
  const s = el.style;
  const m: Mark[] = [];
  if (s.fontWeight === "bold" || Number(s.fontWeight) >= 600) m.push("b");
  if (s.fontStyle === "italic") m.push("i");
  if (/underline/.test(s.textDecoration || s.textDecorationLine || "")) m.push("u");
  if (/line-through/.test(s.textDecoration || s.textDecorationLine || "")) m.push("x");
  if (s.verticalAlign === "super") m.push("sup");
  if (s.verticalAlign === "sub") m.push("sub");
  return m;
}

/** LaTeX from pasted MathML (Word/MathType put the TeX source in an annotation or alttext when available). */
function mathmlTex(el: Element): string | null {
  const ann = el.querySelector('annotation[encoding*="tex" i], annotation[encoding*="TeX"]');
  if (ann?.textContent?.trim()) return ann.textContent.trim();
  const alt = el.getAttribute("alttext");
  return alt?.trim() || null;
}

function inlines(node: Node, marks: Mark[], out: Inline[]) {
  if (node.nodeType === Node.TEXT_NODE) {
    pushText(out, node.textContent || "", marks);
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  const el = node as HTMLElement;
  const tag = el.tagName;
  if (el.dataset?.tex !== undefined) {
    out.push(el.dataset.review ? { t: "m", tex: el.dataset.tex, review: true } : { t: "m", tex: el.dataset.tex });
    return;
  }
  if (el.dataset?.range) { out.push({ t: "range" }); return; }
  if (el.dataset?.warn !== undefined) { out.push({ t: "warn", v: el.dataset.warn }); return; }
  if (tag === "BR") { out.push({ t: "br" }); return; }
  if (tag === "IMG") {
    const src = el.dataset.src;
    if (src) {
      const n: Inline = { t: "img", src, inline: true };
      const w = num(el.dataset.w || null), h = num(el.dataset.h || null);
      if (w) n.w = w;
      if (h) n.h = h;
      if (el.dataset.review) n.review = true;
      out.push(n);
    }
    return;
  }
  if (tag === "MATH" || el.localName === "math") {
    const tex = mathmlTex(el);
    if (tex) out.push({ t: "m", tex });
    else pushText(out, el.textContent || "", marks);
    return;
  }
  if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEMPLATE") return;
  if (el.classList?.contains("katex") || el.classList?.contains("katex-display")) {
    const tex = el.querySelector("annotation")?.textContent?.trim();
    if (tex) out.push({ t: "m", tex });
    return;
  }
  const m = TAG_MARK[tag];
  const next = m ? [...marks, m] : [...marks, ...(tag === "SPAN" || tag === "FONT" ? styleMarks(el) : [])];
  el.childNodes.forEach((c, i) => {
    if (c.nodeType === Node.ELEMENT_NODE && BLOCK_TAGS.has((c as Element).tagName) && i > 0) out.push({ t: "br" });
    inlines(c, next, out);
  });
}

function trimInl(c: Inline[]): Inline[] {
  let a = 0, b = c.length;
  while (a < b && c[a].t === "br") a++;
  while (b > a && c[b - 1].t === "br") b--;
  return c.slice(a, b);
}

function isBlank(c: Inline[]) {
  return c.every((n) => n.t === "br" || n.t === "tab" || (n.t === "s" && !n.v.trim()));
}

function para(c: Inline[], el?: HTMLElement): Block | null {
  c = trimInl(c);
  if (!c.length || isBlank(c)) return null;
  const p: Block = { t: "p", c };
  if (c.length === 1 && c[0].t === "m" && (el?.dataset.display === "1" || el === undefined)) p.display = true;
  return p;
}

function tableBlock(el: HTMLTableElement): Block | null {
  const rows: Cell[][] = [];
  el.querySelectorAll(":scope > tbody > tr, :scope > thead > tr, :scope > tr").forEach((tr) => {
    const row: Cell[] = [];
    (tr as HTMLTableRowElement).querySelectorAll(":scope > td, :scope > th").forEach((td) => {
      const cell: Cell = { c: domToBlocks(td as HTMLElement) };
      const cs = Number((td as HTMLTableCellElement).colSpan), rs = Number((td as HTMLTableCellElement).rowSpan);
      if (cs > 1) cell.colspan = cs;
      if (rs > 1) cell.rowspan = rs;
      row.push(cell);
    });
    if (row.length) rows.push(row);
  });
  return rows.length ? { t: "table", rows } : null;
}

export function domToBlocks(root: HTMLElement): Block[] {
  const out: Block[] = [];
  let pending: Inline[] = [];
  const flush = () => {
    const p = para(pending);
    if (p) {
      if (p.t === "p" && p.display && pending.length) delete p.display;
      out.push(p);
    }
    pending = [];
  };
  root.childNodes.forEach((n) => {
    if (n.nodeType === Node.TEXT_NODE) { pushText(pending, n.textContent || "", []); return; }
    if (n.nodeType !== Node.ELEMENT_NODE) return;
    const el = n as HTMLElement;
    const tag = el.tagName;
    if (tag === "FIGURE" || (el.dataset.src && tag !== "IMG")) {
      flush();
      const src = el.dataset.src || el.querySelector("img")?.getAttribute("data-src");
      if (src) {
        const b: Block = { t: "img", src };
        const w = num(el.dataset.w || null), h = num(el.dataset.h || null);
        if (w) b.w = w;
        if (h) b.h = h;
        if (el.dataset.alt) b.alt = el.dataset.alt;
        if (el.dataset.caption) b.caption = el.dataset.caption;
        out.push(b);
      }
      return;
    }
    if (tag === "TABLE") { flush(); const t = tableBlock(el as HTMLTableElement); if (t) out.push(t); return; }
    if (tag === "UL" || tag === "OL") {
      flush();
      const items: Block[][] = [];
      el.querySelectorAll(":scope > li").forEach((li) => {
        const hasBlocks = [...li.children].some((c) => BLOCK_TAGS.has(c.tagName));
        if (hasBlocks) {
          const bl = domToBlocks(li as HTMLElement);
          if (bl.length) items.push(bl);
        } else {
          const c: Inline[] = [];
          inlines(li, [], c);
          const p = para(c, li as HTMLElement);
          if (p) {
            // a lone formula item: keep its display flag explicit (the server defaults a missing flag to display)
            if (p.t === "p") { if (p.c.length === 1 && p.c[0].t === "m") p.display = (li as HTMLElement).dataset.display === "1"; else delete p.display; }
            items.push([p]);
          }
        }
      });
      if (items.length) out.push({ t: "list", ordered: tag === "OL", items });
      return;
    }
    if (BLOCK_TAGS.has(tag)) {
      flush();
      if ([...el.children].some((c) => ["TABLE", "UL", "OL", "FIGURE"].includes(c.tagName) || (c as HTMLElement).dataset?.src && c.tagName !== "IMG")) {
        out.push(...domToBlocks(el));
        return;
      }
      const c: Inline[] = [];
      inlines(el, /^H[1-6]$/.test(tag) ? ["b"] : [], c);
      const p = para(c, el);
      if (p) {
        if (p.t === "p" && el.dataset.display !== "1" && p.display && c.length > 1) delete p.display;
        if (p.t === "p" && p.c.length === 1 && p.c[0].t === "m" && el.querySelector(".katex-display")) p.display = true;
        out.push(p);
      }
      return;
    }
    if (el.classList?.contains("katex-display")) {
      flush();
      const tex = el.querySelector("annotation")?.textContent?.trim();
      if (tex) out.push({ t: "p", c: [{ t: "m", tex }], display: true });
      return;
    }
    inlines(el, [], pending);
  });
  flush();
  return out;
}

/** Plain text with $…$ / \(…\) / \[…\] formulas (pasted from elsewhere) → inline nodes. */
export function textWithTex(text: string): Inline[] {
  const out: Inline[] = [];
  const re = /\$\$([^$]+)\$\$|\$([^$\n]+)\$|\\\((.+?)\\\)|\\\[(.+?)\\\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) pushPlain(out, text.slice(last, m.index));
    out.push({ t: "m", tex: (m[1] || m[2] || m[3] || m[4]).trim() });
    last = m.index + m[0].length;
  }
  if (last < text.length) pushPlain(out, text.slice(last));
  return out;
}

/** Inline Markdown (**đậm**, *nghiêng*, `mã`) with $…$ / \(…\) formulas → inline nodes. */
function mdInlines(text: string, out: Inline[] = []): Inline[] {
  const re = /\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$|\*\*([^*\n]+?)\*\*|__([^_\n]+?)__|(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])|`([^`\n]+)`/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) pushText(out, text.slice(last, m.index), []);
    if (m[1] !== undefined || m[2] !== undefined) out.push({ t: "m", tex: (m[1] ?? m[2]).trim() });
    else if (m[3] !== undefined || m[4] !== undefined) mdMarked(m[3] ?? m[4], "b", out);
    else if (m[5] !== undefined) mdMarked(m[5], "i", out);
    else pushText(out, m[6], []);
    last = m.index + m[0].length;
  }
  if (last < text.length) pushText(out, text.slice(last), []);
  return out;
}

function mdMarked(text: string, mark: Mark, out: Inline[]) {
  for (const n of mdInlines(text)) {
    if (n.t === "s") pushText(out, n.v, [...(n.m || []), mark]);
    else out.push(n);
  }
}

function mdItem(text: string): Block {
  const c = mdInlines(text);
  return c.length === 1 && c[0].t === "m" ? { t: "p", c, display: false } : { t: "p", c };
}

const MD_ITEM = /^\s*(?:([-*+•])|(\d+)[.)])\s+(.*)$/;

/** A solution pasted as plain text from ChatGPT/Claude (Markdown + LaTeX) → blocks: paragraphs on blank lines,
 *  line breaks kept, - / 1. lists, # headings (bold), **bold**, inline \(…\) $…$ and display \[…\] $$…$$ formulas
 *  (each display formula on its own line). Nothing is centred except display formulas, as in the student view. */
export function pasteTextToBlocks(text: string): Block[] {
  const display: string[] = [];
  const src = text.replace(/\r\n?/g, "\n").replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (_, a, b) => {
    display.push(((a ?? b) as string).trim());
    return `\n\n\u0000${display.length - 1}\u0000\n\n`;
  });
  const out: Block[] = [];
  let list: { t: "list"; ordered: boolean; items: Block[][] } | null = null;
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      const c: Inline[] = [];
      para.forEach((line, i) => { if (i) c.push({ t: "br" }); mdInlines(line, c); });
      if (c.length && !isBlank(c)) out.push({ t: "p", c });
    }
    para = [];
  };
  const flushList = () => { if (list) out.push(list); list = null; };
  for (const raw of src.split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const dm = line.trim().match(/^\u0000(\d+)\u0000$/);
    if (dm) {
      flushPara(); flushList();
      if (display[+dm[1]]) out.push({ t: "p", c: [{ t: "m", tex: display[+dm[1]] }], display: true });
      continue;
    }
    if (!line.trim()) { flushPara(); flushList(); continue; }
    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { flushPara(); flushList(); continue; }  // --- rule
    const h = line.match(/^\s*#{1,6}\s+(.*)$/);
    if (h) { flushPara(); flushList(); const c: Inline[] = []; mdMarked(h[1], "b", c); if (c.length) out.push({ t: "p", c }); continue; }
    const it = line.match(MD_ITEM);
    if (it && list && /^\s{2,}/.test(raw)) {  // indented item → sub-list of the last item
      const last = list.items[list.items.length - 1];
      const ordered = it[2] !== undefined;
      let sub = last[last.length - 1];
      if (sub.t !== "list" || sub.ordered !== ordered) { sub = { t: "list", ordered, items: [] }; last.push(sub); }
      sub.items.push([mdItem(it[3])]);
      continue;
    }
    if (it) {
      flushPara();
      const ordered = it[2] !== undefined;
      if (list && list.ordered !== ordered) flushList();
      if (!list) list = { t: "list", ordered, items: [] };
      list.items.push([mdItem(it[3])]);
      continue;
    }
    if (list && /^\s+\S/.test(raw)) {  // indented continuation of the last item
      const p = list.items[list.items.length - 1][0] as Extract<Block, { t: "p" }>;
      p.c.push({ t: "br" });
      mdInlines(line.trim(), p.c);
      continue;
    }
    flushList();
    para.push(line.replace(/^\s*>\s?/, ""));
  }
  flushPara(); flushList();
  return out;
}

/** Plain text that carries LaTeX delimiters (the source of a chatbot answer, not its rendering). */
export function hasTexDelims(text: string): boolean {
  return /\\\(|\\\[|\$\$|\$[^$\n]+\$/.test(text);
}

function pushPlain(out: Inline[], t: string) {
  t.split("\n").forEach((line, i) => {
    if (i > 0) out.push({ t: "br" });
    pushText(out, line, []);
  });
}

export function inlinesHtml(nodes: Inline[]): string {
  return inlineHtml(nodes);
}

/** Every LaTeX string of a block tree (for the KaTeX check sent with a save). */
export function collectTex(nodes: unknown, out: Set<string> = new Set()): Set<string> {
  if (Array.isArray(nodes)) nodes.forEach((n) => collectTex(n, out));
  else if (nodes && typeof nodes === "object") {
    const n = nodes as Record<string, unknown>;
    if (n.t === "m" && typeof n.tex === "string") out.add(n.tex);
    for (const k of ["c", "rows", "items", "content", "header", "passage"]) if (k in n) collectTex(n[k], out);
  }
  return out;
}
