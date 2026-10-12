import katex from "katex";
import { normalizeTex } from "../../../lib/tex";

/** The KaTeX error for a formula exactly as the student renderer would draw it (null = renders). */
export function katexError(tex: string): string | null {
  try {
    katex.renderToString(normalizeTex(tex), { throwOnError: true, strict: "ignore", trust: false, output: "html" });
    return null;
  } catch (e) {
    return e instanceof Error ? e.message.replace(/^KaTeX parse error: /, "") : String(e);
  }
}

/** {tex: {ok, error}} for every formula, sent with a save (recorded as the formula check of new formulas). */
export function texChecks(texs: Iterable<string>): Record<string, { ok: boolean; error?: string }> {
  const out: Record<string, { ok: boolean; error?: string }> = {};
  for (const t of texs) {
    const e = katexError(t);
    out[t] = e ? { ok: false, error: e } : { ok: true };
  }
  return out;
}
