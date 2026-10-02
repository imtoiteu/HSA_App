# Question-bank integration audit

Latest full audit: **2026-10-02** (deep mode, production database), upstream revision `8a61c8ead8b9`
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
4. **Subjects.** Original, upstream-effective and inferred subjects, and the resulting effective subject and
   its source, are counted before and after classification.
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

## Subject classification

Resolution order: admin override → upstream effective subject (approved, confidence not low) → specific
original subject → accepted second-pass inference → generic original (`science`) → unclassified
(`general`). The original, inferred and upstream-effective subjects are stored separately, with confidence
and evidence. Low-confidence results are never forced into a subject.

| Effective subject | Before | After | Served |
|---|---:|---:|---:|
| math | 14,604 | 32,316 | 6,098 |
| general (unclassified) | 40,836 | 12,588 | 537 |
| literature | 8,020 | 7,528 | 4,255 |
| chemistry | 2,504 | 5,712 | 414 |
| physics | 371 | 4,796 | 212 |
| english | 2,433 | 3,497 | 1,085 |
| history | 213 | 1,634 | 279 |
| biology | 9 | 729 | 159 |
| science (generic) | 840 | 573 | 304 |
| geography | 15 | 531 | 121 |
| logic | 136 | 77 | 6 |

Subject source:

- upstream_effective: 53,686
- unclassified: 12,588
- original: 3,707

Upstream flags 10,317 classifications `SUBJECT_CLASSIFICATION_REVIEW`. For those, the app falls back to
the original subject.

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
  - second-pass inference and the upstream effective subject were added, with an admin override and
    filters;
  - columns and the resolution order are described above (migrations `0002`, `0003`).
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
- 12,588 questions have no reliable subject. They stay `general` until upstream or an admin classifies them.
