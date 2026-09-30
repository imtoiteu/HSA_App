/**
 * Semantics-preserving fixes for systematic LaTeX conversion artefacts in the bank, applied at render
 * time (stored question versions stay untouched, so completed exams render the same way).
 * Keep in sync with scripts/katex-check.mjs.
 *  - an unescaped "%" is a TeX comment and swallows the rest of the formula; the sources mean a percent sign
 *  - "R\\{3\}" (line break + braces) is a set difference R \ {3} produced by the converter
 */
export function normalizeTex(tex: string): string {
  return tex
    .replace(/(^|[^\\])%/g, "$1\\%")
    .replace(/\\\\\{([^{}]*)\\\}/g, "\\setminus\\{$1\\}");
}
