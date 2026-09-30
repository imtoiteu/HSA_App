# Integration contract: HSA-question-bank → HSA-app

HSA-question-bank (upstream) is a **read-only** data source. The app never writes to it, never
runs its pipeline, and never reads its DOCX/HTML editorial outputs as content.

## What the app reads

| Upstream path | Used for | Notes |
|---|---|---|
| `question-bank/sqlite/hsa_question_bank.sqlite` | canonical questions, options, groups, formulas, assets, representative occurrence, documents | opened with `mode=ro`; upstream replaces the file atomically (`os.replace`), so an open handle is a consistent snapshot |
| `question-bank/assets/<sha[:2]>/<sha>.<ext>` | images and formula preview pictures | copied (hard-linked when possible) into the app's content-addressed store; WMF/EMF converted to PNG |
| `editorial/question_overrides.jsonl` | validated reconstructions of stem/options (`{{tex:…}}`, `{{redrawn}}`) | applied only when `status = RECONSTRUCTED_VALIDATED` |
| `editorial/formula_overrides.jsonl` | retyped formulas | applied only when `status = RECONSTRUCTED_VALIDATED` |
| `editorial/figure_overrides.jsonl` | redrawn figures (`redrawn_path`) | `REDRAWN_VALIDATED` only |
| `editorial/asset_replacements.jsonl` | `decorative_remove` / `table` replacements of raster fragments | |
| `rendered/docx/_manifest/<Subject>_VolNN.json` → `rows[].states` | **authoritative editorial states** once the bulk editorial build has produced them | if absent for a question, the app derives states with a port of upstream `editorial.editorial_states()` (see below) |
| `editorial/subject_inference.jsonl` | second-pass subject inference: `inferred_subject`, `confidence`, `evidence` per question | stored separately from the original subject; applied as metadata (no content rebuild) |
| `inventory/state.sqlite` → `blobs.doc_status = 'NEEDS_OCR'` | scanned PDFs without a text layer (no questions extracted) | recorded as `source_document` rows with status `NEEDS_MATH_AWARE_OCR`; never served |
| `.git/HEAD` (read as a file) | upstream revision | stored as the bank's `version` |

Identity is **only** the immutable `canonical_question_id` (`cq_<16 hex>`). Word volume numbers,
occurrence ids and file paths are never used as keys (occurrence/document data is kept as
provenance for admins).

## Field mapping

| Upstream | App |
|---|---|
| `canonical_question_id` | `question.external_id` (bank `hsa`) |
| `question_type` | `question.question_type` (all 9 upstream types are representable) |
| `stem_md`, options `content_md`, `tables`, `solution_md`, `explanation_md` | converted to the app block model in `question_version.content` (formulas, images and tables resolved; overlays applied) |
| `correct_answer` + `answer_source_type` | `question_version.answer` — **only** when `answer_source_type ∈ {SOURCE_PROVIDED_ANSWER, SOURCE_PROVIDED_SOLUTION}`; `UNRESOLVED` and `AUTOMATICALLY_INFERRED_CANDIDATE` produce `answer = null` (never inferred) |
| `group_id` → `question_group` | `question.group_key` + group snapshot (header, passage, tables) inside each member's version |
| `subject` | kept as `question.source_subject` (original) |
| inference record | `inferred_subject`, `inference_confidence`, `inference_evidence` |
| — | effective `subject_code` + `subject_source` (`admin` override → specific original → inferred with an accepted confidence → generic original such as `science` → `unclassified`); aliases map bank values to app subjects |
| `topic`, `subtopic`, `cognitive_level` | stored; topic practice is enabled only when the configured coverage threshold is met |
| `exam_systems`, representative occurrence (`document`, section, number) | provenance (admin only) |
| `review_status`, `review_flags` | inputs of the serving policy |

## Editorial states

Primary state = first of `REJECTED, NEEDS_FORMULA_REVIEW, NEEDS_VISUAL_REVIEW,
NEEDS_ANSWER_LINKING, NEEDS_REVIEW, READY_TO_SERVE` present. `question.state_source` records
whether the states came from the upstream manifests (`upstream_manifest`) or from the app's
port of the rules (`derived`). The derived rules are conservative: formula status uses the
same glyph check as upstream (`formula_check.py`, vendored with attribution), images below the
upstream resolution threshold or unconvertible become `NEEDS_VISUAL_REVIEW`.

## Serving policy (app side, configurable)

See [SERVING_POLICY.md](SERVING_POLICY.md). Default: only `READY_TO_SERVE` questions, with an
answer the app can score, not blocked by app-level checks (missing passage, inline options in a
short-response item, unrenderable content), are served to students. Admin overrides are stored
separately and never modify upstream data.

## Synchronisation semantics

* `hsa-app sync` is idempotent, incremental and restartable: every question produces a
  deterministic `content_hash`; unchanged hashes are no-ops; a changed hash inserts a **new**
  immutable version and moves the current pointer.
* Batches of 500 questions per transaction; a checkpoint in `sync_run` lets an interrupted run
  continue.
* Questions that disappear upstream are marked `removed_upstream` and excluded from serving; no
  row, version or exam history is deleted.
* Completed exams reference `question_version_id`, so later corrections never change them.

## Corrections flowing back

Student reports and admin corrections live in `question_report` / `question_correction`
(keyed by `cq_…`). `hsa-app export-corrections` writes them in the upstream proposal format
(`question_id, field, new_value, evidence, editor, date, note`) for the editorial team to
validate and apply as upstream overlays. The app never writes into the upstream tree.

## Reconciliation

`hsa-app audit [--deep] [--out report.md]` (or Admin → Ngân hàng & đồng bộ → Đối soát) compares the
upstream canonical ids with the app by `cq_…` and checks answers, assets, shared passages, subjects, QA
states, formula rendering and serving. `--deep` recomputes each question's upstream input hash to find
content changed since the last sync. See [QUESTION_BANK_AUDIT.md](QUESTION_BANK_AUDIT.md) for the latest
report.

## Formula rendering check

Formulas are rendered on the web with KaTeX. `hsa-app export-tex f.json` → `node
frontend/scripts/katex-check.mjs f.json r.json` → `hsa-app import-tex-check f.json r.json` stores the
result per formula (`formula_check`) and flags questions: `formula_render_error` (stem/options/passage;
blocks serving) and `solution_formula_render_error` (solution only; recorded, not blocking by default).
