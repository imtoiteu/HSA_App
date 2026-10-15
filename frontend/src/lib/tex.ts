/**
 * Semantics-preserving fixes for systematic LaTeX conversion artefacts in the bank, applied at render
 * time (stored question versions stay untouched, so completed exams render the same way).
 * Keep in sync with scripts/katex-check.mjs (normalizeTex and repairTex).
 *  - an unescaped "%" is a TeX comment and swallows the rest of the formula; the sources mean a percent sign
 *  - "R\\{3\}" (line break + braces) is a set difference R \ {3} produced by the converter
 */
export function normalizeTex(tex: string): string {
  return tex
    .replace(/(^|[^\\])%/g, "$1\\%")
    .replace(/\\\\\{([^{}]*)\\\}/g, "\\setminus\\{$1\\}");
}

/** Read a balanced {…} group starting at s[i] === "{"; returns [content, index after the group] or null. */
function group(s: string, i: number): [string, number] | null {
  if (s[i] !== "{") return null;
  let depth = 0;
  for (let j = i; j < s.length; j++) {
    const c = s[j];
    if (c === "\\") { j++; continue; }
    if (c === "{") depth++;
    else if (c === "}" && --depth === 0) return [s.slice(i + 1, j), j + 1];
  }
  return null;
}

/**
 * Repairs for the double scripts the bank's Word-equation → LaTeX conversion produced (only tried when KaTeX
 * rejects the formula as it is, so a formula that renders is never changed):
 *  - a number split around its superscript: C_{1}^{3}_{5} is C^3_{15} (C_{1}^{3}_{5}·C_{2}^{2}_{0} = 86450)
 *  - chained subscripts: V_{H_{2}}_{C_{2}O_{4}} → V_{H_{2}C_{2}O_{4}}
 *  - an empty superscript before integral limits: \int^{\,} _{0}^{1} → \int_{0}^{1}
 *  - a prime written as a superscript before a power: B^{'}^{2} → B'^{2}
 */
export function repairTex(tex: string): string {
  const s = tex
    .replace(/\\(int|iint|iiint|oint)\s*\^\{\s*(?:\\,|\\ |~)?\s*\}\s*/g, "\\$1")
    .replace(/\^\{('+)\}/g, "$1");
  let out = "";
  let i = 0;
  while (i < s.length) {
    if (s[i] === "\\") { out += s.slice(i, i + 2); i += 2; continue; }
    if ((s[i] === "_" || s[i] === "^") && s[i + 1] === "{") {
      // a run of script groups: when a kind repeats (_{1}^{3}_{5}, ^{1}_{0}^{0}, _{a}_{b}) its pieces are one
      // script cut apart by the converter → one subscript and one superscript, pieces joined in order
      const run: [string, string][] = [];
      let j = i;
      for (;;) {
        let k = j;
        while (run.length && s[k] === " ") k++;
        if ((s[k] === "_" || s[k] === "^") && s[k + 1] === "{") {
          const g = group(s, k + 1);
          if (!g) break;
          run.push([s[k], g[0]]);
          j = g[1];
          continue;
        }
        break;
      }
      const subs = run.filter((r) => r[0] === "_").map((r) => r[1]);
      const sups = run.filter((r) => r[0] === "^").map((r) => r[1]);
      if (subs.length > 1 || sups.length > 1) {
        out += (subs.length ? `_{${subs.join("")}}` : "") + (sups.length ? `^{${sups.join("")}}` : "");
        i = j;
        continue;
      }
    }
    out += s[i++];
  }
  return out;
}
