import { describe, expect, it } from "vitest";
import type { Block } from "../../../lib/types";
import { blocksToHtml, collectTex, domToBlocks, textWithTex } from "./blockdom";

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
