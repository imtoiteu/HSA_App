import { describe, expect, it } from "vitest";
import type { Block } from "../../../lib/types";
import { blocksToHtml, collectTex, domToBlocks, hasTexDelims, mathHtml, pasteTextToBlocks, textWithTex } from "./blockdom";

const roundtrip = (b: Block[]) => {
  const el = document.createElement("div");
  el.innerHTML = blocksToHtml(b);
  return domToBlocks(el);
};

describe("editor DOM <-> content model", () => {
  it("round-trips paragraphs, marks, formulas, Vietnamese text and tokens", () => {
    const b: Block[] = [
      { t: "p", c: [{ t: "s", v: "Cho hàm số " }, { t: "m", tex: "y=\\frac{x+1}{x-2}" }, { t: "s", v: " đồng biến", m: ["b"] },
                    { t: "s", v: " trên ", m: ["i", "u"] }, { t: "br" }, { t: "s", v: "Tiếng Việt: ữ ặ ỡ ơ ư đ" }] },
      { t: "p", c: [{ t: "m", tex: "x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}" }], display: true },
      { t: "p", c: [{ t: "s", v: "H" }, { t: "s", v: "2", m: ["sub"] }, { t: "s", v: "O và x" }, { t: "s", v: "2", m: ["sup"] },
                    { t: "range" }, { t: "warn", v: "công thức" }] },
    ];
    expect(roundtrip(b)).toEqual(b);
  });

  it("round-trips tables, lists and figures", () => {
    const b: Block[] = [
      { t: "table", rows: [[{ c: [{ t: "p", c: [{ t: "s", v: "x" }] }] }, { c: [{ t: "p", c: [{ t: "m", tex: "x^2" }] }], colspan: 2 }]] },
      { t: "list", ordered: true, items: [[{ t: "p", c: [{ t: "s", v: "một" }] }], [{ t: "p", c: [{ t: "s", v: "hai" }] }]] },
      { t: "img", src: "ab/" + "a".repeat(64) + ".png", w: 300, h: 200, alt: "đồ thị", caption: "Hình 1" },
    ];
    const out = roundtrip(b);
    expect(out[1]).toEqual(b[1]);
    expect(out[2]).toEqual(b[2]);
    expect(out[0].t).toBe("table");
  });

  it("reduces pasted HTML to the model (no foreign tags, MathML TeX kept)", () => {
    const el = document.createElement("div");
    el.innerHTML = '<div><span style="font-weight:bold">Đậm</span> <script>alert(1)</script><a href="x">liên kết</a>' +
      '<math alttext="\\alpha+1"><mi>α</mi></math></div><img src="https://evil/x.png">';
    const out = domToBlocks(el);
    expect(out).toEqual([{ t: "p", c: [{ t: "s", v: "Đậm", m: ["b"] }, { t: "s", v: " liên kết" }, { t: "m", tex: "\\alpha+1" }] }]);
  });

  it("parses $…$ in pasted text and collects formulas", () => {
    const inl = textWithTex("Tính $x^2$ và \\(\\sqrt{2}\\).");
    expect(inl.filter((n) => n.t === "m")).toEqual([{ t: "m", tex: "x^2" }, { t: "m", tex: "\\sqrt{2}" }]);
    expect([...collectTex([{ t: "p", c: inl }])]).toEqual(["x^2", "\\sqrt{2}"]);
  });
});

describe("pasteTextToBlocks (ChatGPT/Claude solution)", () => {
  const sol = [
    "**Lời giải.** Xét hàm số \\(f(x)=x^2-4x+3\\) trên $[0;3]$.",
    "Ta có",
    "",
    "\\[",
    "f'(x)=2x-4",
    "\\]",
    "",
    "Các bước:",
    "1. Giải $f'(x)=0$ được $x=2$.",
    "2. Tính giá trị:",
    "   - $f(0)=3$",
    "",
    "- *Kết luận* min là $$f(2)=-1$$",
    "",
    "### Đáp án: C",
  ].join("\n");
  const b = pasteTextToBlocks(sol);
  it("keeps paragraphs, line breaks, bold and inline math left-aligned", () => {
    expect(b[0]).toEqual({ t: "p", c: [
      { t: "s", v: "Lời giải.", m: ["b"] }, { t: "s", v: " Xét hàm số " }, { t: "m", tex: "f(x)=x^2-4x+3" },
      { t: "s", v: " trên " }, { t: "m", tex: "[0;3]" }, { t: "s", v: "." }, { t: "br" }, { t: "s", v: "Ta có" }] });
  });
  it("puts \\[…\\] on its own display line", () => {
    expect(b[1]).toEqual({ t: "p", c: [{ t: "m", tex: "f'(x)=2x-4" }], display: true });
    expect(b[2]).toEqual({ t: "p", c: [{ t: "s", v: "Các bước:" }] });
  });
  it("builds ordered / unordered lists", () => {
    expect(b[3].t).toBe("list");
    const l = b[3] as Extract<Block, { t: "list" }>;
    expect(l.ordered).toBe(true);
    expect(l.items.length).toBe(2);
    expect(l.items[1]).toEqual([{ t: "p", c: [{ t: "s", v: "Tính giá trị:" }] },
      { t: "list", ordered: false, items: [[{ t: "p", c: [{ t: "m", tex: "f(0)=3" }], display: false }]] }]);
    expect(b[4]).toMatchObject({ t: "list", ordered: false });
  });
  it("splits $$…$$ out as display, heading bold", () => {
    expect(b).toContainEqual({ t: "p", c: [{ t: "m", tex: "f(2)=-1" }], display: true });
    expect(b[b.length - 1]).toEqual({ t: "p", c: [{ t: "s", v: "Đáp án: C", m: ["b"] }] });
  });
  it("survives the editor round trip", () => {
    const div = document.createElement("div");
    div.innerHTML = blocksToHtml(b);
    expect(domToBlocks(div)).toEqual(b);
  });
  it("plain text without markup stays one paragraph", () => {
    expect(pasteTextToBlocks("Ta có 2 + 2 = 4")).toEqual([{ t: "p", c: [{ t: "s", v: "Ta có 2 + 2 = 4" }] }]);
    expect(hasTexDelims("giá $5 và $6")).toBe(true);
    expect(hasTexDelims("không có công thức")).toBe(false);
  });
  it("pasted KaTeX HTML keeps the formula, display stays display", () => {
    const div = document.createElement("div");
    div.innerHTML = '<p>Ta có <span class="katex"><span class="katex-mathml"><math><semantics><annotation encoding="application/x-tex">x^2</annotation></semantics></math></span></span> nên</p>'
      + '<span class="katex-display"><span class="katex"><annotation encoding="application/x-tex">\\int_0^1 x\\,dx</annotation></span></span>';
    expect(domToBlocks(div)).toEqual([
      { t: "p", c: [{ t: "s", v: "Ta có " }, { t: "m", tex: "x^2" }, { t: "s", v: " nên" }] },
      { t: "p", c: [{ t: "m", tex: "\\int_0^1 x\\,dx" }], display: true },
    ]);
  });
});

describe("a paragraph that is one formula", () => {
  const parse = (html: string) => { const el = document.createElement("div"); el.innerHTML = html; return domToBlocks(el); };
  it("stays inline unless marked as a display formula (the server reads a missing flag as display)", () => {
    expect(parse(mathHtml("\\dfrac{5}{6}"))).toEqual([{ t: "p", c: [{ t: "m", tex: "\\dfrac{5}{6}" }], display: false }]);
    expect(parse(`<p>${mathHtml("x^2")}</p>`)).toEqual([{ t: "p", c: [{ t: "m", tex: "x^2" }], display: false }]);
    expect(parse(`<p data-display="1">${mathHtml("x^2", false, true)}</p>`)).toEqual([{ t: "p", c: [{ t: "m", tex: "x^2" }], display: true }]);
  });
});
