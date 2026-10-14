# Review Studio

The Review Studio is the part of Admin where editors, reviewers and approvers check, fix and confirm questions
on the website. Its job is to move the bank toward READY_TO_SERVE over time through human review, local
validators and optional on-demand AI. It is not a bulk re-review.

URL: `/admin/review` (overview), `/admin/review/queue` (queues), `/admin/review/q/<question id>` (studio).

## Roles (`app_user.role`, set in Admin → Người dùng)

| role | view | edit content (drafts) | confirm QA dimensions | approve READY | assign work | AI |
|---|---|---|---|---|---|---|
| admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| approver | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| reviewer | ✓ | – | ✓ | – | – | ✓ |
| editor | ✓ | ✓ | – | – | – | ✓ |

Staff roles other than admin see only the Review Studio part of `/admin`. For practice plans they are
ordinary accounts.

## Workflow and safe publishing

1. **Edit.** Saving creates an immutable `question_version` with `origin=review`, which becomes the question's
   *draft* (`question_review.draft_version_id`). Students keep receiving the current, published version.
2. **Confirm QA.** Six dimensions are confirmed separately: CONTENT, ANSWER, FORMULA, VISUAL, SUBJECT, SOURCE.
   Each can be `ok`, `na` (not applicable) or `fail` (a note is required).
   - FORMULA is not required when the question has no formula.
   - VISUAL is not required when the question has no figure or table.
3. **Approve** (approver or admin). This requires:
   - every dimension `ok` or not required;
   - no open *critical* validator finding;
   - an answer, when the question takes one.

   Approval publishes the draft (`question.current_version_id`) and sets `question.review_gate='ready'`.
4. **Serve.** The serving policy treats `review_gate='ready'` like the upstream READY_TO_SERVE state. All other
   policy checks still apply: content flags, question type and scoring. A dimension marked `fail` sets
   `review_gate='blocked'`, which stops a question from being served even if upstream says READY.

Review status values are `none`, `draft`, `reviewed` (all dimensions confirmed), `approved` and `returned`.
**Reopen** withdraws an approval, and the question goes back to the upstream decision.

**Exam history is never rewritten.** Sessions reference the immutable versions they used, and old versions are
never deleted.

## Hash-bound QA confirmations (incremental QA)

`review/content.py` hashes the semantic parts of the working version:

- type, stem, options, solution, group;
- answer;
- formula (all TeX plus unresolved fragments);
- visual (images and tables);
- subject;
- source.

Each dimension covers a fixed set of parts:

| dimension | parts |
|---|---|
| content | type, stem, options, solution, group |
| answer | answer, options, type |
| formula | formula |
| visual | visual |
| subject | subject |
| source | source |

A confirmation stores the hash of its parts. It stays valid while those parts are unchanged and becomes
`stale` otherwise. So an edit invalidates only the dimensions it touches: changing the answer re-opens ANSWER,
and changing the subject does not re-open VISUAL.

`question_review` keeps the QA fingerprint for each question:

- `content_hash`;
- `validator_version`, `validated_hash` and `validated_at`;
- `verified_by`, `verified_at` and `verification_method`.

`hsa-app review-refresh` (run by the scheduler every 5 minutes) only processes questions marked `dirty` (by sync,
recompute-policy or review actions) or validated by an older `VALIDATOR_VERSION`. Unchanged content that was
already validated is skipped, so the bank is never re-reviewed for nothing. `--all` forces a full pass.

## Queues

Membership is recomputed with the validators and stored in `question_review.queues` (GIN-indexed).

| queue | rule |
|---|---|
| NEEDS_FORMULA_REVIEW / NEEDS_ANSWER_LINKING / NEEDS_VISUAL_REVIEW | the upstream editorial states contain it, and the matching dimension is not confirmed |
| SUBJECT_REVIEW | upstream `SUBJECT_CLASSIFICATION_REVIEW`, and SUBJECT is not confirmed |
| UNCLASSIFIED | no final subject, and SUBJECT is not confirmed |
| GENERAL_REVIEW | upstream `NEEDS_REVIEW`, and CONTENT is not confirmed |
| ANSWER_CONFLICT | conflicting answer flags upstream, and ANSWER is not confirmed |
| INCOMPLETE | empty stem or option, too few options, missing passage, … |
| VALIDATOR | open critical validator findings |
| UPSTREAM_CHANGED | the bank changed a question after it was edited or published here |
| DRAFT / RETURNED | review workflow |
| READY / NOT_READY | effective readiness (review gate, else the upstream state) |

**Priority** (sort "Ưu tiên cao trước") ranks first the questions one step from READY, with an answer and a
subject, and problems on questions students already receive.

## Validators (local, no AI; `review/validators.py`)

**Critical** (block approval until fixed or dismissed with a reason):

- cq_id format;
- empty stem;
- option structure: too few options, duplicate or non-sequential labels, empty option;
- answer: missing, not among the options, or malformed (single-choice with several keys, true/false count mismatch, invalid number);
- missing image file;
- broken image reference;
- unresolved placeholder or raster formula fragment;
- malformed LaTeX (braces, `\left`/`\right`, environments);
- KaTeX render failure (from `formula_check`);
- passage group without passage.

**Warnings:**

- duplicate option text;
- raw LaTeX in plain text;
- OCR/encoding garbage (`�`, mojibake, non-NFC text, symbol noise);
- group key mismatch;
- missing subject;
- READY inconsistent with flags or states;
- solution that seems to contain the next question;
- exact duplicate and near duplicate (bank-wide normalized keys);
- upstream changed.

Reviewers can **accept** a finding (confirmed, needs a fix), **dismiss** it (reason required; the dismissal holds
while the content it was about is unchanged), or fix the content. A finding that disappears on revalidation
becomes `fixed` on its own.

## Source viewer

The upstream bank keeps original files inside the archives of `source-original/`. These files are readable only
by root, and the bank is mounted read-only. The `sources` service prepares a document on first view:

1. It runs as root with only the DAC_READ_SEARCH, DAC_OVERRIDE, CHOWN and FOWNER capabilities, and
   no-new-privileges.
2. It follows `document.source_chains` with 7z, falling back to bsdtar or zip. Path names are matched with
   Unicode normalization.
3. It verifies the extracted bytes against `blob_sha256` and never shows a file that fails the check.
4. It converts DOC/DOCX to PDF with headless LibreOffice.

Pages are rendered lazily by `pdftoppm` at fixed resolutions and cached under the private `sources` volume.
They are never served from `/media`.

- **PDF text-layer questions** open at their page (from `question_occurrence.location`).
- **DOCX questions** have no page metadata, so the page is estimated by matching the question text and is
  labelled as an estimate.
- **No source found:** the viewer shows SOURCE_UNAVAILABLE. Provenance is never invented.

Viewer tools: zoom, drag to pan, page navigation, lazy thumbnails, *Copy source reference*, *Open source page*,
and **Cut a figure from the source**. Cutting a figure:

1. renders the page at 220 dpi;
2. crops the selected region on the server;
3. stores it as an image asset with `source_ref="src:<doc>:p<page>"`;
4. records the document, page and crop rectangle in the review history.

## Editor

Each content field (stem, options, solution, method, passage header and passage) is a rich editor that works
directly on the content model:

- bold, italic, underline, superscript and subscript;
- bulleted and numbered lists;
- tables (add or remove rows and columns);
- inline and block images, with alt text and caption;
- formulas.

Pasted HTML is reduced to the same model. TeX from MathML is kept, foreign tags and external images are
dropped, and `$…$` in pasted text becomes a formula.

**Formulas** (Ctrl+M, or the "∑ Công thức" button) use **MathLive**, a visual editor similar to Word
Equation. It offers fractions, roots, powers and indices, integrals, sums, limits, matrices, vectors, Greek
letters and geometry symbols. Formulas can also be typed or pasted as LaTeX. The preview uses the student
renderer (KaTeX), and a formula KaTeX cannot render is flagged before saving. Formulas are stored as normalized
LaTeX, as before.

**Images** can be uploaded, pasted from the clipboard or dragged in, then cropped, rotated and resized in the
browser. Only the final PNG is uploaded; the server re-encodes it and stores it in the content-addressed media
store. Images can also be replaced or deleted.

The **"Xem như học sinh"** (preview as student) tab renders with the student components (`Passage`,
`RichContent`, `AnswerInput`, `Solution`). It shows unsaved edits, and can also show the version students see
now.

## Fast review

| key | action |
|---|---|
| Ctrl+Enter | save and go to the next question |
| Ctrl+Shift+Enter | approve and go to the next question |
| Ctrl+S | save |
| A, F, V, S, C, N | toggle answer, formula, visual, subject, content, source as ok (only when no input has focus) |
| ←/→ or K/J | previous / next question |
| P | toggle the student preview |

"Next" is the next question of the current queue and filter (server-side `row_number`). When the saved question
has left the queue, it is the item now at the same position.

## History and audit

`review_event` stores every action (append-only):

- the `cq_id`;
- the field, with its old and new value (stem, option text, answer, solution, group, images, formula, subject,
  QA state, review gate, …);
- the actor and time;
- the note and the source reference;
- the from/to versions.

The History tab lists every version (upstream or review), **compares** two of them side by side, and
**restores** a version, which makes it the draft again; publishing it still requires approval.

## AI assist (optional, on demand)

The AI tab offers these actions:

- OCR fix suggestions;
- formula suggestions;
- recovering a formula from the source image;
- solving the question;
- checking the answer;
- suggesting a subject;
- comparing the question with the source;
- explaining a disagreement.

AI runs only when a reviewer clicks, and only for that one question. The answer is shown and stored in the
history as a suggestion. It changes nothing, confirms nothing and never makes a question READY.

The provider is configured with environment variables only: `HSA_AI_PROVIDER=anthropic`, `HSA_AI_API_KEY`, and
optionally `HSA_AI_MODEL`. Requests are rate-limited per user (`HSA_AI_MAX_PER_USER_HOUR`, default 30). Without
these variables, the actions report "not configured" and everything else works.

## Operations

- **Migration:** `0005_review_studio`, run by the API at start. It adds the staff roles, `question_review`,
  `review_event`, `review_finding`, `review_assignment`, `source_cache`, `question.upstream_version_id` and
  `review_gate`, and `question_version.origin`. Downgrade is supported; published review versions remain as
  versions.
- **Sync:** a version published from the studio stays current when upstream changes. The new upstream version
  is stored, and the question goes to UPSTREAM_CHANGED.
- **Background jobs:** the scheduler runs `hsa-app worker --no-sources` (maintenance plus an incremental
  review-refresh of up to 20,000 questions every 5 minutes). The `sources` service runs
  `hsa-app worker --sources-only`.
- **Manual commands:**

  ```
  docker compose exec scheduler nice -n 10 hsa-app review-refresh [--all]
  ```

## Multi-part questions ("Nhiều ý / Câu ghép")

Question type `multi_part`: one shared stem and several child items. Use it for a source question whose a), b),
c)… items each have their own answer, instead of faking the items as options.

* **Editor.** Choose the type "Nhiều ý / Câu ghép". The shared stem stays in "Đề bài". "+ Thêm ý" adds an item.
  Each item has:
  * a label (a, b, c, …) and its own prompt;
  * an answer type: Chọn phương án (with its own options, optionally several correct), Số, Chữ, Công thức
    (LaTeX), Đúng/Sai;
  * its own key, its own worked solution, and its points (weight).

  Items can be reordered or removed. The parent has no single answer and no options. An optional overall solution
  can stay in "Lời giải".
* **Storage.** `content.parts[]` holds `{key, label, prompt, answer_type, multi, options, solution, points}`. The
  answer is `{"kind": "parts", "parts": {key: <answer of the item's kind>}}`, and a formula key is `{"kind":
  "formula", "tex", "accepted"}`. The input spec is `{"kind": "parts", "parts": [...]}`. No column changed;
  migration 0006 only adds the type to a stored serving policy.
* **Checks.** An item without a prompt, answer type or options, or without a key, is a critical finding
  (`parts_malformed` / `answer_missing`), so the question cannot be approved until every item is complete. The
  content hashes of existing questions are unchanged.
* **Students.** The shared stem is shown once, then every item with its own input. After checking or submitting,
  each item shows its outcome, points, key and solution. Scoring:
  * each item uses the rules of its kind; formula answers are compared up to spacing and equivalent spellings
    (`\dfrac`/`\frac`, `\left`/`\right`);
  * the question earns the points-weighted share of its items: correct, partial or incorrect. It is still one
    source question with one entry in history, bookmarks and reports.
* **Import.** Multi-part questions are authored in the Review Studio; the JSONL import format does not carry
  items yet.
