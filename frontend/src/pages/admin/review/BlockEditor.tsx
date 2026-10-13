// Rich editor for one content field (stem, option, solution, passage…). contenteditable + the content model:
// bold/italic/underline/superscript/subscript, lists, tables, inline/block images and formulas (MathLive).
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Block } from "../../../lib/types";
import { blocksToHtml, domToBlocks, hasTexDelims, inlinesHtml, mathHtml, pasteTextToBlocks, textWithTex } from "./blockdom";
import ImageTool, { type ImageResult, uploadBlob } from "./ImageTool";
import MathDialog from "./MathDialog";

export interface EditorHandle {
  insertImage: (r: ImageResult) => void;
  focus: () => void;
}

type MathEdit = { el: HTMLElement | null; tex: string };
type ImgEdit = { el: HTMLElement | null; src?: string; alt?: string; caption?: string; file?: File };

const BlockEditor = forwardRef<EditorHandle, {
  value: Block[] | null; onChange: (b: Block[]) => void; label: string; compact?: boolean; readOnly?: boolean;
  onFocusEditor?: () => void; placeholder?: string;
  /** paste plain text as Markdown + LaTeX (a whole solution copied from ChatGPT/Claude) */
  richPaste?: boolean;
}>(function BlockEditor({ value, onChange, label, compact, readOnly, onFocusEditor, placeholder, richPaste }, ref) {
  const root = useRef<HTMLDivElement>(null);
  const saved = useRef<Range | null>(null);
  const [math, setMath] = useState<MathEdit | null>(null);
  const [image, setImage] = useState<ImgEdit | null>(null);
  const [inTable, setInTable] = useState(false);
  const [selected, setSelected] = useState<HTMLElement | null>(null);
  const timer = useRef<number | undefined>(undefined);

  // the DOM is owned by the browser while editing; it is (re)built only when a new value arrives from outside
  const lastEmitted = useRef<string>("");
  useEffect(() => {
    const json = JSON.stringify(value || []);
    if (root.current && json !== lastEmitted.current) {
      root.current.innerHTML = blocksToHtml(value || []);
      lastEmitted.current = json;
    }
  }, [value]);

  const emit = useCallback(() => {
    if (!root.current) return;
    const blocks = domToBlocks(root.current);
    lastEmitted.current = JSON.stringify(blocks);
    onChange(blocks);
  }, [onChange]);
  const emitSoon = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(emit, 250);
  }, [emit]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const saveSel = () => {
    const s = window.getSelection();
    if (s && s.rangeCount && root.current?.contains(s.anchorNode)) saved.current = s.getRangeAt(0).cloneRange();
    const n = s?.anchorNode as Node | null;
    setInTable(!!(n && (n.nodeType === 1 ? (n as Element) : n.parentElement)?.closest?.("td")));
  };
  const restoreSel = () => {
    root.current?.focus();
    const s = window.getSelection();
    if (saved.current && s) { s.removeAllRanges(); s.addRange(saved.current); }
    else if (root.current && s) {
      const r = document.createRange();
      r.selectNodeContents(root.current);
      r.collapse(false);
      s.removeAllRanges();
      s.addRange(r);
    }
  };
  // insert at the saved caret without relying on focus/execCommand (dialogs move the focus away);
  // block content (figures, tables, display formulas) goes after the paragraph holding the caret
  const insertHtml = (html: string, block = false) => {
    const el = root.current;
    if (!el) return;
    const t = document.createElement("template");
    t.innerHTML = html;
    const frag = t.content;
    const last = frag.lastChild;
    let r = saved.current && el.contains(saved.current.startContainer) ? saved.current.cloneRange() : null;
    if (block) {
      let top: Node | null = r ? r.startContainer : null;
      while (top && top.parentNode !== el) top = top.parentNode;
      if (top && top !== el) el.insertBefore(frag, top.nextSibling);
      else el.appendChild(frag);
    } else {
      if (!r || r.startContainer === el) {
        let p = el.lastElementChild;
        if (!p || p.tagName !== "P") { p = document.createElement("p"); el.appendChild(p); }
        r = document.createRange();
        r.selectNodeContents(p);
        r.collapse(false);
      }
      r.deleteContents();
      r.insertNode(frag);
    }
    if (last && last.parentNode) {
      const after = document.createRange();
      after.setStartAfter(last);
      after.collapse(true);
      saved.current = after;
    }
    emitSoon();
  };
  const cmd = (c: string, v?: string) => {
    restoreSel();
    document.execCommand(c, false, v);
    emitSoon();
  };

  useImperativeHandle(ref, () => ({
    insertImage: (r: ImageResult) => insertImageResult(r),
    focus: () => root.current?.focus(),
  }));

  const insertImageResult = (r: ImageResult) => {
    if (r.inline) insertHtml(inlinesHtml([{ t: "img", src: r.src, w: r.w, h: r.h, inline: true }]));
    else insertHtml(blocksToHtml([{ t: "img", src: r.src, w: r.w, h: r.h, alt: r.alt, caption: r.caption }]), true);
  };

  const onClick = (e: React.MouseEvent) => {
    const t = e.target as HTMLElement;
    const m = t.closest?.(".ed-math") as HTMLElement | null;
    if (m && root.current?.contains(m)) { saveSel(); setMath({ el: m, tex: m.dataset.tex || "" }); return; }
    const fig = t.closest?.(".ed-figure, .ed-img-inline") as HTMLElement | null;
    setSelected(fig && root.current?.contains(fig) ? fig : null);
  };
  useEffect(() => {
    root.current?.querySelectorAll(".ed-selected").forEach((x) => x.classList.remove("ed-selected"));
    selected?.classList.add("ed-selected");
  }, [selected]);

  const onPaste = async (e: React.ClipboardEvent) => {
    const files = [...e.clipboardData.files].filter((f) => f.type.startsWith("image/"));
    if (files.length) {
      e.preventDefault();
      saveSel();
      setImage({ el: null, file: files[0] });  // pasted screenshot → crop/resize before inserting
      return;
    }
    const html = e.clipboardData.getData("text/html");
    const text = e.clipboardData.getData("text/plain");
    e.preventDefault();
    if (richPaste && text && (hasTexDelims(text) || !html)) {
      const blocks = pasteTextToBlocks(text);
      if (blocks.length) {
        document.execCommand("insertHTML", false, blocks.length === 1 && blocks[0].t === "p" && !blocks[0].display
          ? inlinesHtml(blocks[0].c) : blocksToHtml(blocks));
        emitSoon();
        return;
      }
    }
    if (html) {
      const tmp = document.createElement("div");
      tmp.innerHTML = html.replace(/<!--[\s\S]*?-->/g, "");
      tmp.querySelectorAll("img").forEach((im) => {
        // only images already in the media store survive a paste; others must be uploaded via the image tool
        const src = im.getAttribute("src") || "";
        const m = src.match(/\/media\/([0-9a-f]{2}\/[0-9a-f]{64}\.(?:png|jpg|gif|webp|svg))/);
        if (m) im.setAttribute("data-src", m[1]); else im.remove();
      });
      const blocks = domToBlocks(tmp);
      document.execCommand("insertHTML", false, blocks.length === 1 && blocks[0].t === "p" ? inlinesHtml(blocks[0].c) : blocksToHtml(blocks));
    } else if (text) {
      document.execCommand("insertHTML", false, inlinesHtml(textWithTex(text)));
    }
    emitSoon();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault();
      selected.remove();
      setSelected(null);
      emitSoon();
    }
    if (e.ctrlKey && (e.key === "m" || e.key === "M")) { e.preventDefault(); saveSel(); setMath({ el: null, tex: "" }); }
  };

  const tableOp = (op: "row" | "col" | "delrow" | "delcol") => {
    const s = window.getSelection();
    const td = (s?.anchorNode && ((s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement) as Element)?.closest("td")) as HTMLTableCellElement | null;
    if (!td) return;
    const tr = td.parentElement as HTMLTableRowElement;
    const table = tr.closest("table")!;
    const idx = td.cellIndex;
    if (op === "row") {
      const n = tr.cloneNode(true) as HTMLTableRowElement;
      n.querySelectorAll("td").forEach((c) => (c.innerHTML = "<p><br></p>"));
      tr.after(n);
    } else if (op === "col") {
      table.querySelectorAll("tr").forEach((r) => { const c = document.createElement("td"); c.innerHTML = "<p><br></p>"; (r as HTMLTableRowElement).cells[idx]?.after(c); });
    } else if (op === "delrow") {
      if (table.querySelectorAll("tr").length > 1) tr.remove(); else table.remove();
    } else {
      table.querySelectorAll("tr").forEach((r) => (r as HTMLTableRowElement).cells[idx]?.remove());
      if (!table.querySelector("td")) table.remove();
    }
    emitSoon();
  };

  const tb = (title: string, label: React.ReactNode, fn: () => void, cls = "") => (
    <button type="button" className={"tb " + cls} title={title} aria-label={title}
            onMouseDown={(e) => { e.preventDefault(); saveSel(); }} onClick={fn} disabled={readOnly}>{label}</button>
  );

  return (
    <div className={"block-editor" + (compact ? " compact" : "")}>
      {!readOnly && (
        <div className="ed-toolbar" role="toolbar" aria-label={`Định dạng: ${label}`}>
          {tb("In đậm (Ctrl+B)", <b>B</b>, () => cmd("bold"))}
          {tb("In nghiêng (Ctrl+I)", <i>I</i>, () => cmd("italic"))}
          {tb("Gạch chân (Ctrl+U)", <u>U</u>, () => cmd("underline"))}
          {tb("Chỉ số trên", <span>x<sup>2</sup></span>, () => cmd("superscript"))}
          {tb("Chỉ số dưới", <span>x<sub>2</sub></span>, () => cmd("subscript"))}
          <span className="tb-sep" />
          {tb("Danh sách", "• ≡", () => cmd("insertUnorderedList"))}
          {tb("Danh sách số", "1. ≡", () => cmd("insertOrderedList"))}
          {tb("Chèn bảng 2×2", "▦", () => insertHtml(blocksToHtml([{ t: "table", rows: [[{ c: [] }, { c: [] }], [{ c: [] }, { c: [] }]] }]) + "<p><br></p>", true))}
          {inTable && <>
            {tb("Thêm hàng", "+hàng", () => tableOp("row"), "sm")}
            {tb("Thêm cột", "+cột", () => tableOp("col"), "sm")}
            {tb("Xoá hàng", "−hàng", () => tableOp("delrow"), "sm")}
            {tb("Xoá cột", "−cột", () => tableOp("delcol"), "sm")}
          </>}
          <span className="tb-sep" />
          {tb("Công thức (Ctrl+M)", <span>∑ Công thức</span>, () => setMath({ el: null, tex: "" }), "accent")}
          {tb("Hình ảnh: tải lên, dán, cắt", "🖼 Hình", () => setImage({ el: null }))}
          {selected && <>
            {tb("Sửa hình đang chọn", "Sửa hình", () => setImage({ el: selected, src: selected.dataset.src, alt: selected.dataset.alt, caption: selected.dataset.caption }), "sm")}
            {tb("Xoá hình đang chọn", "Xoá hình", () => { selected.remove(); setSelected(null); emitSoon(); }, "sm")}
          </>}
          {tb("Xoá định dạng", "⌫ định dạng", () => cmd("removeFormat"), "sm")}
        </div>
      )}
      <div ref={root} className="ed-area rich" contentEditable={!readOnly} suppressContentEditableWarning
           role="textbox" aria-multiline="true" aria-label={label} data-placeholder={placeholder || ""}
           spellCheck lang="vi"
           onInput={emitSoon} onBlur={() => { saveSel(); emit(); }} onKeyUp={saveSel} onMouseUp={saveSel}
           onFocus={onFocusEditor} onClick={onClick} onPaste={onPaste} onKeyDown={onKeyDown}
           onDrop={(e) => {
             const f = [...e.dataTransfer.files].find((x) => x.type.startsWith("image/"));
             if (f) {
               e.preventDefault();
               uploadBlob(f, f.name).then((r) => insertImageResult({ ...r }));
             }
           }} />
      {math && (
        <MathDialog initial={math.tex} onClose={() => setMath(null)}
                    onDelete={math.el ? () => { math.el!.remove(); setMath(null); emitSoon(); } : undefined}
                    onSave={(tex, display) => {
                      if (math.el) {
                        const p = math.el.closest("p");
                        const tmp = document.createElement("div");
                        tmp.innerHTML = mathHtml(tex, false, !!(display && p));
                        math.el.replaceWith(tmp.firstChild!);
                        if (p) p.dataset.display = display ? "1" : "";
                        emitSoon();
                      } else {
                        if (display) insertHtml(`<p data-display="1">${mathHtml(tex, false, true)}</p>`, true);
                        else insertHtml(mathHtml(tex) + "&nbsp;");
                      }
                      setMath(null);
                    }} />
      )}
      {image && (
        <ImageTool initial={image.src ? { src: image.src, alt: image.alt, caption: image.caption } : undefined}
                   initialFile={image.file}
                   onClose={() => setImage(null)}
                   onDone={(r) => {
                     if (image.el) {
                       const tmp = document.createElement("div");
                       tmp.innerHTML = r.inline ? inlinesHtml([{ t: "img", src: r.src, w: r.w, h: r.h, inline: true }])
                         : blocksToHtml([{ t: "img", src: r.src, w: r.w, h: r.h, alt: r.alt, caption: r.caption }]);
                       image.el.replaceWith(tmp.firstChild!);
                       setSelected(null);
                       emitSoon();
                     } else {
                       insertImageResult(r);
                     }
                     setImage(null);
                   }} />
      )}
    </div>
  );
});

export default BlockEditor;
