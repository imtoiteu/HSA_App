import { fireEvent, render } from "@testing-library/react";
import { AnswerInput, AnswerValue, Passage, answerText } from "./QuestionBody";
import { RichContent, rangeLabel, renderTex } from "./RichContent";
import type { Block, OptionView } from "../lib/types";

describe("RichContent", () => {
  it("renders text marks, KaTeX math and line breaks", () => {
    const blocks: Block[] = [{ t: "p", c: [
      { t: "s", v: "Đậm", m: ["b"] }, { t: "s", v: " H" }, { t: "s", v: "2", m: ["sub"] }, { t: "s", v: "O " },
      { t: "m", tex: "\\frac{1}{2}" }, { t: "br" }, { t: "s", v: "gạch", m: ["u"] },
    ] }];
    const { container } = render(<RichContent blocks={blocks} />);
    expect(container.querySelector("strong")?.textContent).toBe("Đậm");
    expect(container.querySelector("sub")?.textContent).toBe("2");
    expect(container.querySelector("u")?.textContent).toBe("gạch");
    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector("br")).not.toBeNull();
  });

  it("renders display math, tables with spans and warnings", () => {
    const blocks: Block[] = [
      { t: "p", c: [{ t: "m", tex: "x^2" }], display: true },
      { t: "table", rows: [[{ c: [{ t: "p", c: [{ t: "s", v: "a" }] }], colspan: 2 }, { c: [], rowspan: 2 }], [{ c: [] }, { c: [] }]] },
      { t: "warn", v: "thiếu hình" },
    ];
    const { container } = render(<RichContent blocks={blocks} />);
    expect(container.querySelector(".display-math .katex-display")).not.toBeNull();
    const tds = container.querySelectorAll("td");
    expect(tds[0].getAttribute("colspan")).toBe("2");
    expect(tds[1].getAttribute("rowspan")).toBe("2");
    expect(container.textContent).toContain("thiếu hình");
  });

  it("never throws on invalid LaTeX and keeps the source visible", () => {
    const html = renderTex("\\frac{1}{");
    expect(html).toContain("math-error");
    expect(html).toContain("\\frac{1}{");
  });

  it("escapes HTML inside text nodes", () => {
    const { container } = render(<RichContent blocks={[{ t: "p", c: [{ t: "s", v: "<img src=x onerror=alert(1)>" }] }]} />);
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders images from the media store", () => {
    const { container } = render(<RichContent blocks={[{ t: "img", src: "ab/abc.png", w: 300, h: 200 }]} />);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("/media/ab/abc.png");
  });
});

describe("group passage", () => {
  it("fills the question range in the header", () => {
    const { container } = render(<Passage group={{ key: "g", header: [{ t: "p", c: [{ t: "s", v: "Trả lời " }, { t: "range" }] }],
      passage: [{ t: "p", c: [{ t: "s", v: "Hà Nội" }] }] }} positions={[5, 6, 7]} />);
    expect(container.textContent).toContain("Trả lời từ câu 5 đến câu 7");
    expect(rangeLabel([3], "en", "Read and answer ")).toBe("question 3");
    // the source already says "câu": only the numbers are inserted
    expect(rangeLabel([1], "vi", "trả lời các câu ")).toBe("1");
    expect(rangeLabel([5, 6, 7], "vi", "các câu hỏi ")).toBe("5 đến 7");
  });
});

describe("AnswerInput", () => {
  const opts: OptionView[] = [
    { key: "C", display: "A", content: [{ t: "p", c: [{ t: "s", v: "một" }] }] },
    { key: "A", display: "B", content: [{ t: "p", c: [{ t: "s", v: "hai" }] }] },
  ];
  it("reports canonical keys for shuffled options", () => {
    const onChange = vi.fn();
    const { getAllByRole } = render(<AnswerInput input={{ kind: "choice", multi: false }} options={opts} response={null} onChange={onChange} />);
    fireEvent.click(getAllByRole("radio")[1]);
    expect(onChange).toHaveBeenCalledWith({ labels: ["A"] });
  });
  it("shows the correct option and the student's wrong choice on review", () => {
    const { container } = render(<AnswerInput input={{ kind: "choice", multi: false }} options={opts} response={{ labels: ["C"] }}
      onChange={() => {}} disabled reveal={{ answer: { kind: "choice", labels: ["A"] } }} />);
    expect(container.querySelector(".option.correct")?.textContent).toContain("hai");
    expect(container.querySelector(".option.wrong")?.textContent).toContain("một");
  });
  it("true/false statements", () => {
    const onChange = vi.fn();
    const { getAllByText } = render(<AnswerInput input={{ kind: "tf_sequence", n: 2 }} options={[]} response={null} onChange={onChange} />);
    fireEvent.click(getAllByText("Sai")[1]);
    expect(onChange).toHaveBeenCalledWith({ values: [null, false] });
  });
  it("formats answers for display", () => {
    expect(answerText({ kind: "choice", labels: ["A"] }, ["C"])).toBe("C");
    expect(answerText({ kind: "numeric", value: 1.5, text: "1,5" })).toBe("1,5");
    expect(answerText({ kind: "tf_sequence", values: [true, false] })).toBe("a) Đúng; b) Sai");
    expect(answerText(null)).toBe("Chưa có đáp án");
  });
});

describe("normalizeTex", () => {
  it("fixes converter artefacts without changing valid LaTeX", async () => {
    const { normalizeTex } = await import("../lib/tex");
    expect(normalizeTex("(75\\text{%})^{2}")).toBe("(75\\text{\\%})^{2}");
    expect(normalizeTex("50\\%")).toBe("50\\%");
    expect(normalizeTex("D=R\\\\{3\\}.")).toBe("D=R\\setminus\\{3\\}.");
    expect(renderTex("(75\\text{%})^{2}")).not.toContain("math-error");
    expect(normalizeTex("\\frac{1}{2}")).toBe("\\frac{1}{2}");
  });
});

describe("multi-part questions", () => {
  const p = (v: string): Block[] => [{ t: "p", c: [{ t: "s", v }] }];
  const parts = [
    { key: "p1", label: "a", prompt: p("Tung độ giao điểm"), input: { kind: "numeric" as const }, points: 1 },
    { key: "p2", label: "b", prompt: p("Khoảng đồng biến"), input: { kind: "choice" as const, multi: false }, points: 2,
      options: [{ key: "A", display: "A", content: p("(2;+∞)") }, { key: "B", display: "B", content: p("(−∞;2)") }] },
  ];

  it("renders every item with its own input and keeps the answers per item", () => {
    const seen: unknown[] = [];
    const { container, getByText } = render(
      <AnswerInput input={{ kind: "parts", parts: [{ key: "p1", kind: "numeric" }, { key: "p2", kind: "choice", multi: false }] }}
                   options={[]} response={{ parts: { p1: { value: "3" } } }} parts={parts} onChange={(r) => seen.push(r)} />,
    );
    expect(container.querySelectorAll("li.part").length).toBe(2);
    expect(container.textContent).toContain("Tung độ giao điểm");
    fireEvent.click(getByText("(2;+∞)"));
    expect(seen[seen.length - 1]).toEqual({ parts: { p1: { value: "3" }, p2: { labels: ["A"] } } });
  });

  it("shows each item's outcome, key and solution once graded", () => {
    const { container } = render(
      <AnswerInput input={{ kind: "parts", parts: [] }} options={[]} response={null} parts={parts} disabled onChange={() => {}}
                   partResults={[
                     { key: "p1", label: "a", outcome: "correct", fraction: 1, points: 1, awarded: 1, answer: { kind: "numeric", value: 3, text: "3" }, solution: p("y(0)=3") },
                     { key: "p2", label: "b", outcome: "incorrect", fraction: 0, points: 2, awarded: 0, answer: { kind: "choice", labels: ["A"] }, solution: null },
                   ]} />,
    );
    expect(container.querySelector("li.part-correct")).not.toBeNull();
    expect(container.querySelector("li.part-incorrect")).not.toBeNull();
    expect(container.textContent).toContain("y(0)=3");
    expect(container.textContent).toContain("0/2 điểm");
    expect(answerText({ kind: "parts", parts: {} })).toBe("Chưa có đáp án");
  });
});

describe("math expression answers", () => {
  it("lets the student enter an expression and shows math keys as formulas", async () => {
    const seen: unknown[] = [];
    const { container } = render(<AnswerInput input={{ kind: "math" }} options={[]} response={null} onChange={(r) => seen.push(r)} />);
    const box = container.querySelector("input, math-field") as HTMLInputElement;
    expect(box).not.toBeNull();
    if (box.tagName === "INPUT") {
      fireEvent.change(box, { target: { value: "\\frac{a}{\\sqrt{6}}" } });
      expect(seen[seen.length - 1]).toEqual({ text: "\\frac{a}{\\sqrt{6}}" });
    }
    expect(answerText({ kind: "math_expression", tex: "\\frac{a\\sqrt{6}}{6}" })).toBe("\\frac{a\\sqrt{6}}{6}");
    const k = render(<AnswerValue a={{ kind: "math_expression", tex: "\\frac{a\\sqrt{6}}{6}" }} />);
    expect(k.container.querySelector(".katex")).not.toBeNull();
  });
});
