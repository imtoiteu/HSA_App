# Formula reconstruction for PDF text-layer questions

About 45 000 questions in the bank came from the text layer of PDFs (`extraction_method = pdf_text_layer`).
`pdftotext` flattens mathematics: `x²` becomes `x2`, a fraction becomes its numerator and denominator on separate
lines, `√` becomes a lone sign. Upstream therefore marks them `NEEDS_FORMULA_REVIEW`, and they are not served.

The PDF itself still holds everything needed to rebuild the formulas. For every glyph it records the font, the
size and the exact position, and it keeps the strokes TeX drew (fraction bars, radical overlines). The
`pdf_formula_latex` batch rebuilds questions from that layout. It does not use an LLM, and no result is
hard-coded.

## Scope

* Only **LaTeX-typeset PDFs** (pdfTeX, Computer Modern math fonts) are handled. Their math and text fonts are
  distinct: Vietnamese text uses `VNR`/`VNBX`, math uses `CMMI`/`CMR`/`CMSY`/`CMEX`. The glyph encodings are
  the standard TeX ones, and the layout follows TeX's rules. Other producers (Word/MathType, scans) are skipped
  with the reason recorded.
* Candidates are single-choice questions that upstream holds back only for formula fidelity:
  * `NEEDS_FORMULA_REVIEW` (+ `NEEDS_REVIEW`);
  * no visual or answer-linking state;
  * not under human review.

  Questions that are `READY` or already approved are never touched.

## Pipeline (`app/review/formula_pdf.py`, `app/review/formula_batch.py`)

1. **Locate.** The representative occurrence gives the page (`location.blocks = p<page>.<line>`) and the
   question number. The region runs from the bold `Câu N.` line to the next question or section heading, or to
   the □ that closes a worked solution. It may continue onto following pages, and formula parts hanging above or
   below the edge lines are assigned to the nearest question. Header and footer (between the full-width rules),
   rotated text and the redistributor's stamps are excluded.
2. **Read.** `pdfminer.six` (MIT) gives every glyph (font, code, size, box, baseline) plus the strokes and
   drawings.
3. **Rebuild**, innermost structures first:
   * pieces of extensible delimiters;
   * vector arrows (`TeX-vect`) → `\overrightarrow`;
   * radicals: the sign hangs from its overline, plus an optional index;
   * fractions: a bar with numerator/denominator baselines above/below it (`\dfrac`, or `\frac` at script size);
   * `\overline`, wide hats (`yhmath`), stacked limits (`∑`, `lim`/`max`/`min`), integral limits;
   * big delimiters, attached to the symbols they enclose, with their real height measured on the page image;
   * systems: a big `{` or `[` with stacked rows → `cases` / bracket `aligned`.

   Then lines are assembled by baseline. Superscripts and subscripts are found by size and baseline offset;
   upright `sin`/`log`/`lim`… become `\sin`/`\log`/…; spaces are restored from glyph gaps (the Vietnamese fonts
   carry no space glyphs). Lines become stem / options A–D / solution: equation numbers are kept, worksheet
   answer lines (dots) are dropped, and `Chọn đáp án X` is read.
4. **Refuse rather than guess.** The whole question stays unresolved, with the reason, if any of these occurs:
   * a glyph whose meaning is not in the tables;
   * a stroke that is not a fraction bar, overline or radical (tables, underlines);
   * a drawing or image (figures);
   * unbalanced delimiters, or a script whose position is ambiguous;
   * options not exactly A–D.
5. **Verify.**
   * *Ledger:* every glyph of the region appears in the output exactly once.
   * *Page image:* the region is rendered with `pdftoppm`. After masking every glyph, stroke and the answer
     circle, no ink may remain (more than 12 px at 100 dpi refuses).
   * *Identity:* at least 95% of the rebuilt stem/options words must be in the stored (flattened) version.
   * *KaTeX:* every formula must render with the web app's KaTeX (`frontend/scripts/katex-check.mjs`).
   * *Answer:* the source key must pass the same rules as the other remediation batches. A key that comes only
     from an answer-key table (positional) also needs the worked solution's `Chọn đáp án X` printed right after
     the question. That can come from the question's own document, or from the same question in another edition
     of the book (`…-LG.pdf`), but only when that edition rebuilds to an *identical* stem and options. Its worked
     solution then also fills the empty solution.
6. **Second edition.** The same question appears in other editions of the book: a worksheet, an edition with
   solutions, one without, each with different line breaks and positions. Up to three of them are rebuilt. An
   edition whose stem and options rebuild **identically** confirms the formulas, since misreads depend on
   position and would differ between layouts.
7. **Apply.** A verified rebuild is saved as a review draft through `save_edit`; the old version stays in the
   history.
   * **Confirmed by a second edition:** the formula, content and source dimensions are confirmed (method
     `pdf_layout`). If the answer and subject checks pass, with the same rules as the other batches, the question
     is approved and served.
   * **One edition only:** the draft waits in Review Studio with the formula dimension left for a reviewer.
   * The reason for anything not approved is recorded as a `remediation_skip` event.

## Measured quality (2026-10-13)

* **Development samples (450 questions):** each rebuilt question was compared side by side with the PDF page.
  Every error pattern found was fixed and turned into a refusal rule or a test.
* **Held-out sample** (100 random candidates, seed 202, not used for tuning):
  * 67 rebuilt by the engine;
  * 65 of 67 exactly right (97%). The two errors were one regression, vector arrows read as exponents, since
    fixed and covered by a test.
  * Refusals: figures/tables 14, unexplained ink 3, ambiguous scripts/delimiters 10, others 6.
* Auto-approval additionally needs the identical rebuild from a second edition and a valid answer key, so the
  served set is a stricter subset.

## Running a batch

```sh
# 1. dry run, export the formulas not yet KaTeX-checked
hsa-app formula-pdf --document doc_… --limit 200 --export-tex /tmp/tex.json
# 2. render them with the web app's KaTeX (host, frontend/)
node scripts/katex-check.mjs /tmp/tex.json /tmp/tex-results.json
# 3. apply (drafts + approvals); --tex-results feeds step 2
hsa-app formula-pdf --document doc_… --limit 200 --tex-results /tmp/tex-results.json --apply
```

Questions this batch already refused or rebuilt into a draft are skipped; `--retry` re-examines them (e.g. after an
engine improvement). The dry run prints one line per question (`OK` / `SKIP <reason>`) and a JSON summary.

## Batch log

| Batch | Source | Examined | Promoted | Drafts waiting in Review Studio | Review READY | Runtime eligible = serving |
|---|---|---|---|---|---|---|
| (before) | | | | | 6 311 | 20 131 |
| 1 | HINH HOC 12-dongke (doc_95b96e…) | 200 ×3 passes | 201 | 45 | 6 513 | 20 332 |
| 2 | HINH HOC 12-dongke (rest) | 700 | 450 | 166 | 6 966 | 20 785 |
| 3 | DAI SO VA GT 12-dongke (doc_8963cf…), 1st 900 | 900 | 553 | 139 | 7 519 | 21 338 |
| 4 | DAI SO VA GT 12-dongke, next 900 | 900 | 372 | 213 | 7 891 | 21 710 |
| 5 | DAI SO VA GT 12-dongke, 900 not yet examined | 900 | 570 | 140 | 8 461 | 22 280 |
| 6 | DAI SO VA GT 12-dongke, rest | 258 | 141 | 30 | 8 602 | 22 421 |

Spot checks: 10 random promoted questions per batch compared with the PDF page, all correct.
Batch 3: 23 questions whose source worked solution names another answer than the key table were left unresolved
(source conflict).
