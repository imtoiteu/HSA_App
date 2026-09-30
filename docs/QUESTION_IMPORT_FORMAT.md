# Importing additional question banks

Besides the synchronised HSA upstream bank, admins can create any number of other banks without code
changes (Admin → Ngân hàng & đồng bộ → Tạo ngân hàng: code, name, description, version, active flag) and
fill them by

* uploading JSONL (Admin → Nhập JSONL, or `hsa-app import-jsonl <bank_code> <file>`), or
* entering questions one by one (bank row → *Thêm câu*; `POST /api/admin/banks/<code>/questions` with one
  record of the format below). Saving the same `external_id` again creates a new immutable version.

Each bank keeps its version label, source, sync/import history, served-subject counts and the list of exam
blueprints that can draw from it. Deactivating a bank removes all its questions from serving and exam
generation at once (nothing is deleted).
Imports are idempotent (keyed by `(bank, external_id)`; unchanged records are no-ops, changed ones
get a new immutable version). Blueprint pools can target banks with `"banks": ["<bank_code>"]`.

One JSON object per line:

```json
{"external_id": "de-thi-thu-01-cau-1",
 "type": "single_choice",
 "subject": "math",
 "status": "READY_TO_SERVE",
 "language": "vi",
 "stem_md": "Cho {{tex:\\frac{1}{2}+\\frac{1}{3}}}. Kết quả là",
 "options": [{"label": "A", "md": "{{tex:\\frac{5}{6}}}"}, {"label": "B", "md": "{{tex:\\frac{2}{5}}}"}],
 "answer": {"kind": "labels", "labels": ["A"]},
 "solution_md": "Quy đồng: …",
 "group": {"key": "g-01", "header_md": "Đọc đoạn sau và trả lời câu hỏi từ 1 đến 3", "passage_md": "…"},
 "tables": [{"rows": [[{"md": "x"}, {"md": "y"}]]}],
 "exam_systems": ["HSA"],
 "source": {"note": "free-form provenance shown to admins"}}
```

* `type`: `single_choice | multiple_choice | true_false | true_false_statements | numeric_response |
  short_response | error_identification | constructed_response`.
* `subject`: an app subject code (Admin → Môn học).
* `status`: an editorial state; only states allowed by the serving policy are served
  (`READY_TO_SERVE` by default). Records with rendering problems are demoted automatically.
* Rich text (`*_md`) uses hsa-md: `**bold**`, `*italic*`, `<u>`, `<sup>`, `<sub>`, `{{tex:LATEX}}`,
  `{{tbl:N}}` (N-th entry of `tables`), `{{image:relative/path.png}}` (CLI imports; path relative to
  the JSONL file), blank line = paragraph.
* `answer`: `{"kind":"labels","labels":[…]}`, `{"kind":"numeric","value":2.5}`,
  `{"kind":"text","text":"…"}`, `{"kind":"true_false_sequence","values":[true,false,…]}` or omitted
  (never guessed; such items are practice-only with self-check or not served).

Example: [`docs/examples/sample_bank.jsonl`](examples/sample_bank.jsonl).
