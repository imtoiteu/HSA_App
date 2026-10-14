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
| 7 | TUYEN TAP 12-LG (doc_a047a9…), 1st 1000 | 1000 | 186 | 127 | 8 788 | 22 607 |
| 8 | TUYEN TAP 12-LG, rest | 712 | 166 | 129 | 8 954 | 22 773 |
| 9 | HINH HOC 12-dongke, rest | 694 | 524 | 99 | 9 478 | 23 297 |
| fix 1 | 49 promoted questions withdrawn and rebuilt (see below) | 49 | 47 | 2 | 9 477 | 23 296 |
| 10 | TUYEN TAP 12-NOLG (doc_bf5d…) | 593 | 52 | 341 | 9 529 | 23 348 |
| fix 2 | 6 promoted questions withdrawn and rebuilt (see below) | 6 | 6 | 0 | 9 529 | 23 348 |
| 11 | DAI SO VA GT 12-LG (doc_e8c0ed…) | 459 | 92 | 87 | 9 621 | 23 440 |
| 12 | DAI SO VA GT 12-NOdongke (doc_339749…) | 408 | 58 | 190 | 9 679 | 23 498 |
| fix 3 | 2 promoted questions withdrawn and rebuilt (see below) | 2 | 2 | 0 | 9 679 | 23 498 |
| 13 | HINH HOC 12-LG (doc_55bdf0…) | 302 | 62 | 50 | 9 741 | 23 560 |
| 14 | HINH HOC 12-NOdongke (doc_8f61e6…) | 181 | 29 | 119 | 9 770 | 23 589 |
| 15 | remaining candidates of all LaTeX books (GIAI TICH 12-NOdongke, …) | 17 | 3 | 9 | 9 773 | 23 592 |
| retry | questions refused earlier and never rebuilt, re-examined with the current engine | 2 719 | 16 | 13 | 9 789 | 23 608 |
| fix 4 | 3 promoted questions withdrawn and rebuilt (see below) | 3 | 3 | 0 | 9 789 | 23 608 |

Spot checks: 10 random promoted questions per batch compared with the PDF page, all correct.
Retry pass: 227 of the 2 719 earlier refusals now rebuild; 198 of them stay unresolved because the source's worked
solution names another answer than the key table. Questions that already had a draft were not retried (a retry
would only save the same draft again).
Batch 12: 181 confirmed rebuilds wait in Review Studio because their key comes only from an answer-key table and
no edition with worked solutions holds them (this book's only other edition, GIAI TICH 12-NOdongke, has none
either).
Batch 10: 327 of the 411 rebuilt questions wait in Review Studio because no second edition confirms them: the
only other copy of this book is a byte-different file with the same layout, which is not an independent check.
Batch 7: this edition prints worked solutions, many with figures, so far fewer questions rebuild (325/1000);
nested systems ({ containing [ …) are refused.
Batch 3: 23 questions whose source worked solution names another answer than the key table were left unresolved
(source conflict).

## Correction after batch 9 (regression of every promoted question)

Every promoted question (3 163) was rebuilt again with the engine after the batch-9 fixes and compared with its
served version.
* 42 differed substantively (1.3%): 26 "∉" drawn as ∈ with an overlaid slash had become "\in/"; 4 notes such as
  "(loại)" in a system row had been moved out of the formula; 2 sentence continuations had been absorbed into a
  system row; the rest smaller.
* 7 more had words glued to symbols inside a formula ("4hayb").

All 49 were withdrawn (`review-decide` revert, reason recorded), rebuilt with the fixed engine and run through the
same gates: 47 re-promoted; 2 wait in Review Studio (their subject could not be re-confirmed). No promoted
question contains "\in/" any more. The old versions stay in the history.

## Correction after batch 10

The batch-10 spot check found a bracket system shown as a separate formula before its sentence ("… ⇔" left
dangling). The fixed engine (54159ac) was run again on every promoted question (3 213): 6 differed, all with
this error. Checking them found a second defect: a tall interval inside a row of the system (`x = 1 ∈ [1/2; 3]`)
ended the row early and the interval was lost (fixed in f49508a). With both fixes, all 6 rebuild correctly, and
the held-out sample is unchanged. The 6 were withdrawn and rebuilt through the same gates, and all 6 were
re-promoted.

## Correction after batch 12

The batch-12 spot check found `log₅ x` in one option printed right above `log_{1/5} a` in the option below: the
subscript 5 was taken as the numerator of the small fraction (`log^{5/5¹}`). The second edition has the same
two-column layout, so it misread identically and could not catch it. A fraction's numerator and denominator are
now the rows next to its bar (test added; fixed in 42f6e31). The regression of all 3 363 promoted questions found
one more with the same misread (`8 log²_{b/a} a` read as `8 log_{2/a^b} a`). Both were withdrawn, rebuilt and
re-promoted. The held-out sample is unchanged.

## Correction after the retry pass

The retry pass's spot check (all 16 promotions) found two bracket systems shown on a line of their own *before*
"⇔ (log₃x)⁴ = 16 ⇔ ⇔": the big brackets sat on a baseline of their own, and that line was chosen as the systems'
home. A system now goes on a line holding real content (fixed in b3501c2). The regression of all 3 473 promoted
questions found 3 with this error; all 3 were withdrawn, rebuilt and re-promoted. The held-out sample is unchanged.

## Status after all LaTeX books

Every candidate of the 11 LaTeX books has been examined. Of 7 975 candidates, 3 473 are promoted and served
(Review READY 6 311 → 9 789, serving 20 131 → 23 608). The rest either wait in Review Studio as drafts,
mostly for lack of a second edition or of an independent answer check, or remain unresolved with their
reason: figures and tables, ambiguous layout, conflicting source answers. Word/MathType PDFs (about 11 000
questions) need a different engine and have not been started.

# Word/MathType PDFs (`app/review/formula_word.py`, batch `pdf_formula_word`)

The rest of the flattened-formula questions come from PDFs printed from Word, whose formulas are MathType (or
Equation Editor / Word equation) objects. They need their own engine: the fonts, encodings and layout rules
have nothing in common with TeX.

## Pool (2026-10-14)

4 583 single-choice candidates in 191 non-LaTeX documents. 2 488 of them have their source PDF prepared, so
the batches work on those first. The other sources are prepared later in small groups, because the server disk
is nearly full.

## How a formula is read

* **Fonts.**
  * Variables are italic Times New Roman (or Euclid/Palatino/Cambria).
  * Digits and function names are upright.
  * Operators, relations, Greek letters and the pieces of big delimiters come from the Symbol fonts (Symbol,
    SymbolMT, Euclid Symbol). Their private-use codes U+F0xx, or Latin-1 bytes, are the Adobe Symbol encoding:
    0xB3 is ≥, 0xCE is ∈, 0xE6–0xFE are delimiter pieces.
  * ℝ ℤ ℕ ℓ, vector arrows and wide hats come from MT Extra. Its codes were identified on the bank's documents.
  * Legacy 8-bit Vietnamese fonts (VNI, .Vn) are refused.
* **Structures** are rebuilt from geometry, innermost first:
  * accents: an arrow or hat on its own baseline above the letters it covers;
  * fraction bars, whose numerator and denominator are the rows next to the bar;
  * radicals: a sign drawn as an outline plus strokes, or as lines only, or a √ glyph, with its overline and an
    optional index;
  * drawn slashes and underlines (formatting only);
  * extensible delimiters, then systems (`cases` / bracket `aligned`, stopped by a second system to their
    right);
  * scripts, by size and baseline: nested, pre-scripts (²³⁵₉₂U), limits under min/max/lim, primes.
* **Lines become text and formulas.** A word holding a Vietnamese letter is text; italic Latin letters, symbols,
  structures and scripts make a formula. A formula glued to a word ("(C)tâm") is split. Upright words inside an
  equation become `\text{…}`, or `\mathrm{…}` for units and element symbols.
* **Page furniture.** Header/footer text and drawings repeated on the neighbouring pages are ignored, and so
  are rotated watermark text and light watermark images. Questions are labelled "Câu N" or "Bài N".

## Checks (a question is refused when any of these fails)

1. Every glyph has a known meaning, and every glyph of the question's region ends up in the rebuild (ledger).
2. Every dark stroke is explained: fraction bar, radical, slash, underline, or page furniture.
3. **Page image, with its text:** after masking where each glyph's ink actually lies (x-height for small
   letters, so an accent drawn above one is not hidden), no dark ink remains.
4. **Page image, without its text:** the page is rendered again with every text object removed (pypdf, also
   inside the equation objects). Any drawing or picture ink left in the region refuses the question, unless it
   lies on an explained stroke. This catches figures, tables, and accents or whole equations that MathType
   embedded as pictures.
5. The words of the stored (flattened) version match (≥95%), and nearly all its letters and digits (≥97%)
   reappear in the rebuild.
6. KaTeX renders every formula. The answer passes the same rules as the other batches, where a key from an
   answer table alone needs the solution's "Chọn X". The subject and visual checks are the same.
7. Questions of a shared passage, and stored versions with figures or tables, are left for review.

Word books rarely exist in a second edition, so there is no identical-rebuild confirmation. The checks above,
especially the no-text rendering, take its place. Their precision is measured on samples.

## Measured quality

* Development samples (60 + 200 questions): every rebuild compared with the PDF page. Each error found was fixed
  and the samples were re-run:
  * slanted Greek letters dropped as watermarks;
  * an underline read as a fraction bar;
  * a label-line numerator outside the region;
  * radical index or slash misread;
  * two adjacent systems merged;
  * pre-scripts attached to an operator;
  * a lone row of exponents taken for a page number.
* **Held-out sample** (150 questions, seed 303, not used for tuning): 28 rebuilt. All of the 27 not also in the
  development samples are exactly right. Refusals are mostly figures/tables, ambiguous delimiters and unknown
  glyphs.

## Batch log

| Batch | Source | Examined | Promoted | Drafts waiting in Review Studio | Review READY | Runtime eligible = serving |
|---|---|---|---|---|---|---|
| (before) | | | | | 9 789 | 23 608 |
| W1 | Chuyên đề 19. Phương trình mũ - logarit (doc_8b6e30…) | 364 | 74 | 12 | 9 863 | 23 682 |
| fix W1 | 5 promoted questions withdrawn and rebuilt (see below) | 5 | 5 | 0 | 9 863 | 23 682 |
| W2 | Chuyên đề 18 (Hàm số mũ - logarit) + Chuyên đề 1 (Tính đơn điệu) | 389 | 40 | 23 | 9 995 | 23 814 |
| W3 | Chuyên đề 20, 3, 2, 10, 21, 17 (bất phương trình mũ-log, GTLN-GTNN, cực trị, thể tích khối chóp, khối nón, biến đổi logarit) | 715 | 55 | 139 | 10 057 | 23 876 |
| fix W3 | 3 promoted questions withdrawn and rebuilt (see below) | 3 | 3 | 0 | 10 057 | 23 876 |
| W4 | next 10 documents of the pool (mathematics and physics) | 584 | 19 | 115 | 10 076 | 23 895 |
| W5 | next 15 documents of the pool | 522 | 101 | 139 | 10 177 | 23 996 |
| fix W5 | 11 promoted questions withdrawn and rebuilt (see below) | 11 | 11 | 0 | 10 177 | 23 996 |

W1 was redone after the database restore of 2026-10-14 (same result: 74 promoted). Counts after W2 include the
unsupported_objects batch running alongside it.
W1: 57 rebuilds were first held back by the character check, which ignored the letters of `\log`, `\ln`, …
in the rebuild (fixed). With the corrected check they passed, and 47 of them were promoted.

**Correction after W1.** The spot check found a system row `3^x = 3` whose superscript had attached to the
system's bracket and come out as a row of its own. The fix (a nucleus is never a system's tall bracket) is
covered by a test. The regression of all 74 promoted questions found 2 with that error and 3 with an opening
bracket kept inside a formula before a remark ("4 (thỏa)"). All 5 were withdrawn, rebuilt and re-promoted.

**Correction after W3.** The spot check found two engine errors: two function names written together ("ln sin A"
came out as the word "lnsin"), and a word right after a system row ("với R") whose first ASCII letter went into
the row. In a third question, a system with centred rows and equation labels (1), (2), (3) lost its first row and
its last label. The fixes: a run made only of function names is split; a word after a row gives its letters back;
rows centred under the widest row belong to the system; a label "(n)" / "(I)" ends its row as `\quad (n)`. Each
fix is covered by a test. The regression of all 169 Word-promoted questions found these 2 plus one more (q50016,
a stray "(1)" line). All 3 were withdrawn, rebuilt and re-promoted, and their served content now matches the engine.

W4: 10 promoted questions spot-checked against the PDF, all correct. Most drafts are held back by the existing
gates, not by the rebuild: answer evidence from a single source (60), subject unclassified (35), and one subject
conflict.

**Correction after W5.** The spot check (10 questions) found one serious engine error: the conjugate bars of
w = (z̄₁)³(z̄₂)⁵ were taken as fraction bars, with z₁ of the line above as numerator (q51191). A bar that touches what
is under it but is more than 0.6 em from anything above it is now an overline. It also found two smaller faults: no
space between a number and a Vietnamese word when the PDF has no space glyph ("3là"), and a formula running across a
full stop ("AD = a. SA vuông" → "a.SA"; "W = 4. W_đ" stays one product). Each fix is covered by a test. The regression
of all 289 Word-promoted questions changed 11, all corrections. They were withdrawn, rebuilt and re-promoted.
