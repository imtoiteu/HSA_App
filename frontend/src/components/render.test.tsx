import { fireEvent, render } from "@testing-library/react";
import { AnswerInput, Passage, answerText, rangeText } from "./QuestionBody";
import { RichContent, renderTex } from "./RichContent";
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
    expect(rangeText([3], "en")).toBe("question 3");
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
