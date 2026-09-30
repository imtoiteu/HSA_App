// Validate LaTeX strings with the exact KaTeX version the web app ships.
//   node scripts/katex-check.mjs tex.json results.json
// input:  [{"sha": "...", "tex": "..."}]   output: [{"sha", "ok", "error"}]
import { readFileSync, writeFileSync } from "node:fs";
import katex from "katex";

// same normalisation as src/lib/tex.ts (applied by the web renderer before KaTeX)
const normalizeTex = (tex) => tex.replace(/(^|[^\\])%/g, "$1\\%").replace(/\\\\\{([^{}]*)\\\}/g, "\\setminus\\{$1\\}");

const [, , inPath, outPath] = process.argv;
const items = JSON.parse(readFileSync(inPath, "utf8"));
const out = [];
let bad = 0;
for (const { sha, tex } of items) {
  try {
    // same options as src/components/RichContent.tsx
    katex.renderToString(normalizeTex(tex), { throwOnError: true, strict: "ignore", trust: false, output: "html" });
    out.push({ sha, ok: true });
  } catch (e) {
    bad++;
    out.push({ sha, ok: false, error: String(e.message || e).slice(0, 300) });
  }
}
writeFileSync(outPath, JSON.stringify(out));
console.log(`checked ${items.length} formulas with KaTeX ${katex.version}: ${bad} failing`);
