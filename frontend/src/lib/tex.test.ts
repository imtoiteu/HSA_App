import katex from "katex";
import { describe, expect, it } from "vitest";
import { normalizeTex, repairTex } from "./tex";

const renders = (t: string) => { try { katex.renderToString(t, { throwOnError: true, strict: "ignore" }); return true; } catch { return false; } };

describe("repairTex (double scripts of the Word-equation conversion)", () => {
  it("joins a number split around its superscript", () => {
    expect(repairTex("C_{1}^{3}_{5}.C_{2}^{2}_{0}=86450")).toBe("C_{15}^{3}.C_{20}^{2}=86450");
    expect(repairTex("n(\\Omega )=C_{1}^{2}_{2}=66")).toBe("n(\\Omega )=C_{12}^{2}=66");
  });
  it("joins chained subscripts", () => {
    expect(repairTex("V_{H_{2}}_{C_{2}O_{4}}")).toBe("V_{H_{2}C_{2}O_{4}}");
    expect(repairTex("\\text{W}_{\\text{lkr}}_{_{\\text{Cl}}}")).toBe("\\text{W}_{\\text{lkr}_{\\text{Cl}}}");
  });
  it("drops an empty superscript before integral limits", () => {
    expect(repairTex("J=\\int^{\\,} _{0}^{2}f(x)dx")).toBe("J=\\int_{0}^{2}f(x)dx");
  });
  it("writes a prime superscript as a prime", () => {
    expect(repairTex("A^{'}C^{'}=\\sqrt{A^{'}B^{'}^{2}+B^{'}C^{'}^{2}}")).toBe("A'C'=\\sqrt{A'B'^{2}+B'C'^{2}}");
  });
  it("makes the real failing formulas render", () => {
    for (const t of ["C_{1}^{3}_{5}.C_{2}^{2}_{0}=86450", "P(A)=\\frac{n(A)}{n(\\Omega )}=\\frac{C_{2}^{2}_{6}.C_{2}^{1}_{6}}{C_{5}^{3}_{2}}=\\frac{13}{34}",
      "C_{KMnO_{4}(1)}=\\frac{C_{H_{2}C_{2}O_{4}}.V_{H_{2}}_{C_{2}O_{4}}.2}{V_{KMnO_{4}}.5}", "S=2\\left| \\int^{\\,} _{0}^{1}\\left( -x^{2}-x-4 \\right)dx \\right|",
      "\\Rightarrow A^{'}C^{'}=\\sqrt{A^{'}B^{'}^{2}+B^{'}C^{'}^{2}}=2\\sqrt{2}"]) {
      expect(renders(normalizeTex(t))).toBe(false);
      expect(renders(repairTex(normalizeTex(t)))).toBe(true);
    }
  });
  it("leaves valid formulas alone", () => {
    for (const t of ["C_{15}^{3}", "x_{1}^{2}+x_{2}^{2}", "\\int_{0}^{1}f(x)dx", "f'(x)", "a_{i_{j}}"]) expect(repairTex(t)).toBe(t);
  });
});

describe("repairTex: numbers split on both sides", () => {
  it("joins the pieces of each kind", () => {
    expect(repairTex("(a.t^{2}-3t)|^{1}_{0}^{0}=100a-30")).toBe("(a.t^{2}-3t)|_{0}^{10}=100a-30");
    expect(repairTex("C_{3}^{1}_{5}^{0}-C_{1}^{1}_{5}^{0}")).toBe("C_{35}^{10}-C_{15}^{10}");
  });
});
