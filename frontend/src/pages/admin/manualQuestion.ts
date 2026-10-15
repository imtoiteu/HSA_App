// Admin → "Thêm câu hỏi": form fields → one record of the import format (docs/QUESTION_IMPORT_FORMAT.md), with
// the checks that can be made before asking the server (which still validates everything).

export const CHOICE_TYPES = ["single_choice", "multiple_choice", "error_identification"];
export const MANUAL_TYPES = ["single_choice", "multiple_choice", "true_false", "true_false_statements", "numeric_response",
  "short_response", "error_identification", "constructed_response"];

export type ManualForm = {
  external_id: string; type: string; subject: string; status: string; stem_md: string; options: string; answer: string;
  solution_md: string;
};

export const EMPTY_FORM: ManualForm = { external_id: "", type: "single_choice", subject: "math", status: "READY_TO_SERVE",
  stem_md: "", options: "A. \nB. \nC. \nD. ", answer: "", solution_md: "" };

/** "A. text" lines → options (lines without a label continue the previous option). */
export function parseOptions(text: string): { label: string; md: string }[] {
  const out: { label: string; md: string }[] = [];
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*([A-H])[.)]\s*(.*)$/);
    if (m) out.push({ label: m[1], md: m[2].trim() });
    else if (line.trim() && out.length) out[out.length - 1].md += "\n" + line.trim();
  }
  return out.filter((o) => o.md.trim());
}

export function answerLabels(text: string): string[] {
  return [...new Set(text.toUpperCase().split(/[\s,;]+/).filter(Boolean))];
}

export function buildRecord(f: ManualForm): Record<string, unknown> {
  const rec: Record<string, unknown> = { type: f.type, subject: f.subject, status: f.status, stem_md: f.stem_md.trim() };
  if (f.external_id.trim()) rec.external_id = f.external_id.trim();
  if (f.solution_md.trim()) rec.solution_md = f.solution_md.trim();
  const ans = f.answer.trim();
  if (CHOICE_TYPES.includes(f.type)) {
    rec.options = parseOptions(f.options);
    const labels = answerLabels(ans);
    if (labels.length) rec.answer = { kind: "labels", labels };
  } else if (ans) {
    const v = Number(ans.replace(",", "."));
    rec.answer = f.type === "numeric_response" && ans !== "" && !Number.isNaN(v) ? { kind: "numeric", value: v, text: ans }
      : { kind: "text", text: ans };
  }
  return rec;
}

/** Problems that make saving pointless (the server would refuse or not serve the question). */
export function formProblems(f: ManualForm): string[] {
  const out: string[] = [];
  if (!f.stem_md.trim()) out.push("Chưa nhập đề bài.");
  if (f.external_id.trim() && !/^[\w.\-:]{1,80}$/.test(f.external_id.trim()))
    out.push("Mã câu chỉ gồm chữ, số, dấu chấm, gạch ngang/gạch dưới (tối đa 80 ký tự).");
  if (CHOICE_TYPES.includes(f.type)) {
    const opts = parseOptions(f.options);
    const labels = opts.map((o) => o.label);
    if (opts.length < 2) out.push("Cần ít nhất 2 phương án có nội dung (mỗi dòng “A. …”).");
    if (new Set(labels).size !== labels.length) out.push("Nhãn phương án bị trùng.");
    const ans = answerLabels(f.answer);
    const bad = ans.filter((l) => !labels.includes(l));
    if (bad.length) out.push(`Đáp án ${bad.join(", ")} không có trong các phương án.`);
    if (f.type === "single_choice" && ans.length > 1) out.push("Câu một đáp án chỉ được chọn một nhãn.");
  }
  if (f.type === "numeric_response" && f.answer.trim() && Number.isNaN(Number(f.answer.trim().replace(",", "."))))
    out.push("Đáp số phải là một số (vd: 2,5 hoặc -3).");
  return out;
}

export const ANSWER_HINT: Record<string, string> = {
  single_choice: "nhãn, vd: B", multiple_choice: "các nhãn, vd: A, C", error_identification: "nhãn, vd: C",
  true_false: "Đúng hoặc Sai", true_false_statements: "vd: a) Đúng b) Sai c) Đúng d) Sai",
  numeric_response: "một số, vd: 2,5", short_response: "đáp án ngắn", constructed_response: "(tự luận: có thể để trống)",
};
