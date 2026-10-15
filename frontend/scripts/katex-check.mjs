// Validate LaTeX strings with the exact KaTeX version the web app ships.
//   node scripts/katex-check.mjs tex.json results.json
// input:  [{"sha": "...", "tex": "..."}]   output: [{"sha", "ok", "error"}]
import { readFileSync, writeFileSync } from "node:fs";
import katex from "katex";

// same normalisation as src/lib/tex.ts (applied by the web renderer before KaTeX)
const normalizeTex = (tex) => tex.replace(/(^|[^\\])%/g, "$1\\%").replace(/\\\\\{([^{}]*)\\\}/g, "\\setminus\\{$1\\}");

// same repair as src/lib/tex.ts (tried only when a formula does not render as it is)
function group(s, i) {
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
function repairTex(tex) {
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
      const run = [];
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

const [, , inPath, outPath] = process.argv;
const items = JSON.parse(readFileSync(inPath, "utf8"));
const out = [];
let bad = 0;
for (const { sha, tex } of items) {
  try {
    // same options as src/components/RichContent.tsx
    const opts = { throwOnError: true, strict: "ignore", trust: false, output: "html" };
    try {
      katex.renderToString(normalizeTex(tex), opts);
    } catch {
      katex.renderToString(repairTex(normalizeTex(tex)), opts);
    }
    out.push({ sha, ok: true });
  } catch (e) {
    bad++;
    out.push({ sha, ok: false, error: String(e.message || e).slice(0, 300) });
  }
}
writeFileSync(outPath, JSON.stringify(out));
console.log(`checked ${items.length} formulas with KaTeX ${katex.version}: ${bad} failing`);
