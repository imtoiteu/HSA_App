// Copies MathLive's fonts next to the SPA (served from /mathlive/fonts; the CSP allows same-origin fonts only).
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "node_modules", "mathlive", "fonts");
const dst = join(root, "public", "mathlive", "fonts");
if (!existsSync(src)) {
  console.error("mathlive fonts not found (npm ci first)");
  process.exit(1);
}
mkdirSync(dst, { recursive: true });
cpSync(src, dst, { recursive: true });
console.log("mathlive fonts →", dst);
