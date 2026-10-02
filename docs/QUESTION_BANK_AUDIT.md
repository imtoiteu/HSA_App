# Question-bank integration audit

Latest full audit: **2026-10-02** (deep mode, production database; subject reconciliation below), upstream revision `8a61c8ead8b9`
(editorial build v2.2), app sync run 5 (`d5cf4c42e39d88bd@hsa-builder-3`), migration `0003`.

Re-run at any time:

```bash
docker compose exec scheduler nice hsa-app audit --deep --out /tmp/audit.md   # or Admin → Ngân hàng & đồng bộ → Đối soát
```

The report is stored for the admin UI. Use `--deep` to recompute every question's upstream input hash and
detect content changed since the last sync.

## Method

1. **Identity.** Upstream `canonical_question_id` (`cq_…`) is compared with `question.external_id` of bank `hsa`.
   Missing, extra, duplicate and removed-upstream ids are listed. No other key (volume, occurrence,
   file path) is used.
2. **Drift.** Each question's upstream inputs (canonical row, options, group, formulas, assets, overlays,
   manifest states) are hashed exactly as the importer does, then compared with the stored `source_hash`.
3. **Content checks on every current version.** These cover:
   - answer key against the input type and options;
   - every referenced image exists in the media store and the asset table;
   - shared-passage snapshot is present;
   - render placeholders (`warn` nodes and unvalidated fallbacks) in stem, options and the shared passage;
   - every LaTeX string, checked against the stored KaTeX results (`formula_check`).
4. **Subjects.** Each question's app effective subject is compared with upstream's final subject, id by id,
   and then followed through the serving policy, the student API and the rendered UI.
5. **Serving.** Excluded questions are counted by their primary cause. Editorial state comes first, then the
   first blocking app check.
6. **Rendering QA.** A headless browser opens a sample of 22 served questions in the student UI, covering
   every subject and served type, passages, tables, images and display math. It finds no KaTeX errors, no
   broken images and no placeholders.

## Reconciliation

| | |
|---|---|
| Upstream canonical questions | 69,981 |
| App imported (unique `cq_`) | 69,981 |
| Missing from app / extra in app / duplicates | 0 / 0 / 0 |
| Changed upstream since last sync (deep) | 0 |
| READY_TO_SERVE upstream / in app | 15,501 / 15,501 |
| **Served to students** | **13,470** (12,707 auto-scored, 763 practice self-check) |
| Excluded by editorial QA state | 54,480 |
| Excluded by app content checks (READY upstream) | 2,031 |
| Unsupported types (imported, not served) | `open_or_unknown` 39 |
| Questions missing required assets (all / served) | 0 / 0 |
| Served auto-scored questions without a resolvable answer | 0 |
| Imported questions without any answer | 43,553 (never guessed; not auto-scored) |
| Questions in shared groups / snapshots missing | 10,795 in 2,953 groups / 0 |
| Distinct LaTeX strings checked with KaTeX | 45,937 (all) |
| Failing formulas (all / served / student-visible in served) | 101 / 43 / **0** (served failures are solution-only) |
| Render placeholders (all / served) | 564 / **0** |
| Deferred source documents (scanned PDFs, NEEDS_MATH_AWARE_OCR) | 15 |

### Excluded by QA state (primary cause)

| State | Questions |
|---|---|
| NEEDS_FORMULA_REVIEW | 32,955 |
| NEEDS_ANSWER_LINKING | 16,584 |
| NEEDS_VISUAL_REVIEW | 2,556 |
| NEEDS_REVIEW | 2,385 |

`NEEDS_MATH_AWARE_OCR` applies to source *documents* (15 scanned PDFs with no text layer), not to
extracted questions. Those documents are recorded in `source_document` and listed per bank.

### READY upstream, excluded by app checks

| Check | Questions |
|---|---|
| `group_context_missing`: question refers to a passage whose content is empty | 1,442 |
| `group_membership_suspect`: passage header names a question range that excludes this question | 428 |
| `render_warning`: undisplayable object or unvalidated formula picture in stem/options/passage | 92 |
| `inline_options_in_short_response` | 50 |
| `formula_render_error`: KaTeX cannot render a formula the student sees | 7 |
| `empty_stem` | 6 |
| `answer_not_in_options` | 5 |
| `too_few_options` | 1 |

Admins can override any single question (Admin → Câu hỏi → detail). The serving policy itself is
editable (Admin → Chính sách phục vụ).

### Served by type

single_choice 11,952 · short_response 764 · numeric_response 726 · error_identification 24 ·
true_false_statements 3 · constructed_response 1.

All 9 upstream types are imported and representable. Only `open_or_unknown` is marked
`unsupported_for_serving`.

## Subject reconciliation (2026-10-02, upstream `8a61c8e` → production → student UI)

Every `cq_` id is traced. Upstream is read from `canonical_question`, `editorial/subject_effective.jsonl`
and the manifest states. Production is queried directly. The student numbers come from `/api/catalog`,
the practice-session pool query, and the subject chips rendered in a headless browser at `/luyen-tap`.

| Subject | Upstream final | App imported (same final subject) | App effective | Upstream READY | App eligible = served | Student API | UI chip | Excluded | Mismatch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Toán | 32,304 | 32,304 | 32,304 | 7,190 | 6,088 | 6,088 | 6,088 | 26,216 | 0 |
| Ngữ văn | 7,551 | 7,551 | 7,551 | 4,521 | 4,261 | 4,261 | 4,261 | 3,290 | 0 |
| Tiếng Anh | 3,208 | 3,208 | 3,208 | 1,541 | 1,085 | 1,085 | 1,085 | 2,123 | 0 |
| Vật lý | 4,803 | 4,803 | 4,803 | 267 | 214 | 214 | 214 | 4,589 | 0 |
| Hóa học | 5,720 | 5,720 | 5,720 | 423 | 421 | 421 | 421 | 5,299 | 0 |
| Sinh học | 739 | 739 | 739 | 180 | 164 | 164 | 164 | 575 | 0 |
| Lịch sử | 1,642 | 1,642 | 1,642 | 296 | 283 | 283 | 283 | 1,359 | 0 |
| Địa lý | 534 | 534 | 534 | 125 | 123 | 123 | 123 | 411 | 0 |
| Chưa phân loại (`general`, UI "Tổng hợp") | 13,480 | 13,480 | 13,480 | 958 | 831 | 831 | 831 | 12,649 | 0 |
| **Total** | **69,981** | **69,981** | **69,981** | **15,501** | **13,470** | **13,470** | **13,470** | **56,511** | **0** |

For every subject, imported = served + excluded, with no remainder. The student API count equals the database
`is_served` count, which equals the practice pool. Practice allows self-check items, so nothing extra is filtered.

### Exclusion reasons by subject (primary cause)

| Subject | Formula review | Answer linking | Visual review | General review | App checks |
|---|---:|---:|---:|---:|---|
| Toán | 18,988 | 4,396 | 1,271 | 459 | passage missing 748, wrong passage 302, render 41, formula 7, answer∉options 2, inline options 2 |
| Ngữ văn | 458 | 2,057 | 199 | 316 | wrong passage 126, passage missing 98, render 33, answer∉options 2, empty stem 1 |
| Tiếng Anh | 159 | 951 | 205 | 352 | passage missing 444, render 7, empty stem 5 |
| Vật lý | 3,416 | 871 | 135 | 114 | inline options in short response 48, passage missing 2, too few options 1, answer∉options 1, render 1 |
| Hóa học | 1,698 | 2,777 | 255 | 567 | render 2 |
| Sinh học | 98 | 331 | 39 | 91 | passage missing 16 |
| Lịch sử | 203 | 1,036 | 19 | 88 | passage missing 11, render 2 |
| Địa lý | 26 | 132 | 67 | 184 | passage missing 2 |
| Chưa phân loại | 7,909 | 4,033 | 366 | 214 | passage missing 121, render 6 |

The following contribute nothing to the exclusions:

- subject-classification review (a flag only);
- unsupported types: the 39 `open_or_unknown` questions are already excluded by QA state;
- pagination, API or frontend filters;
- inactive banks.

**Physics:** 4,803 upstream = 4,803 imported = 214 served + 4,589 excluded. Of the excluded, 4,536 are not
READY upstream (formula review 3,416, answer linking 871, visual review 135, review 114). The other 53 are
READY but fail an app check: 48 short-response items with options typed inside the stem, 2 with an empty
passage, and 1 each for too few options, answer not among options, and undisplayable content.

### Subject semantics fix (2026-10-02)

Before this fix, 996 questions had an app subject different from upstream's final subject, with no admin
override:

- 929 questions had a null upstream final subject (deliberately unclassified), but the app re-applied
  the original label: `science` 564, `english` 290 (Vietnamese text removed from English by upstream rule
  R5), and `logic_reasoning` 75.
- 67 questions had a final subject flagged `SUBJECT_CLASSIFICATION_REVIEW` or of low confidence, and the
  app ignored it.

These two rules also created the app-only categories `science` (573) and `logic` (77). The resolver now
uses upstream's final classification as published, so only an admin override can replace it. Production was
recomputed after a backup (`backups/hsa_pre_subjectfix_20261002_0250.dump`):

- 996 subjects changed, and 0 mismatches remain;
- `science` and `logic` hold 0 questions;
- 13,470 served before and after.

Physics went from 212 to 214. The 296 served questions formerly in `science` are now *Chưa phân loại*, so they
left the Khoa học exam pools. Those pools still hold 1,205 served questions, against a need of 50.

`subject_source`: upstream_effective 56,501, unclassified 13,480, admin 0.

## Assets and formulas

- 4,585 distinct media files (142 MB) are referenced by imported questions and stored content-addressed
  with their sha256 and provenance. Upstream's 82,205 asset rows also include formula preview pictures,
  which the app replaces with LaTeX, and decorative fragments.
- Formulas render on the web with KaTeX from LaTeX, never from DOCX screenshots. A render-time
  normalisation (`frontend/src/lib/tex.ts`) fixes unescaped `%` and `R\\{x\}` set notation. The remaining
  101 failing strings are upstream LaTeX defects. Questions where students would see one are excluded
  (`formula_render_error`), and solution-only failures are recorded (`solution_formula_render_error`).

## Changes made during the audit

These were fixed, not only reported:

- **Sync:**
  - 59 new upstream questions and 905 new versions imported;
  - 0 questions removed;
  - no history touched.
- **Subject handling:**
  - original, inferred and upstream-final subjects are stored separately (migrations `0002`, `0003`);
  - the upstream final subject is authoritative, and an admin override with filters is available;
  - see the subject reconciliation above.
- **Builder (`hsa-builder-3`):**
  - answer keys printed as the last paragraph of a stem are removed;
  - passages attached to questions outside their stated range are flagged;
  - `open_or_unknown` is marked `unsupported_for_serving`;
  - an inactive bank removes its questions from serving;
  - render placeholders and unvalidated formula pictures inside **shared passages** now block serving.
    This affected 92 previously served questions. `hsa-app refresh-flags` re-derives these flags from
    stored versions without a rebuild.
- **Formula check:** KaTeX validation of all 45,937 distinct LaTeX strings (`export-tex` → `katex-check.mjs` →
  `import-tex-check`).
- **Reliability:**
  - the sync advisory lock moved to a dedicated AUTOCOMMIT connection, because the idle-transaction timeout
    could silently drop it and allow a concurrent run;
  - long audit phases no longer hold a transaction open.
- **Multi-bank management:**
  - bank metadata: version, source, active flag, sync history, deferred documents;
  - manual question entry and JSONL import for additional banks;
  - exam sets with fixed items plus random pools.

## Known upstream data issues (for the editorial team)

The app never modifies upstream. These issues are excluded from serving by the checks above, or noted for
correction:

- 1,442 READY questions point to an empty shared passage, and 428 are attached to a passage whose header
  names other question numbers.
- 81 trusted answers cannot be used, for example a key that is not among the options or that the parser
  cannot read.
- 261 "solutions" only repeated the stem and were dropped.
- 101 LaTeX strings fail KaTeX, some because `{{tex:}}` was truncated upstream.
- A few solutions bleed into the next question, and one table was paired with the wrong question. Both
  were seen during rendering QA and need upstream correction.
- 15 scanned PDFs need math-aware OCR before their questions can exist.

## Remaining blockers

- 54,480 questions await upstream editorial review: formula retyping, answer linking and visual review.
  They become servable automatically when upstream marks them READY_TO_SERVE and the next sync runs.
- 13,480 questions are unclassified upstream, 9,558 of them flagged for classification review. They stay
  *Chưa phân loại* until upstream or an admin classifies them.
