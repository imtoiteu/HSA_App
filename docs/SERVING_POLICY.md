# Serving policy

Which questions students receive is decided by a **configurable policy**
(`app_setting['serving_policy']`, editable in Admin → Cài đặt → Chính sách phục vụ; defaults in
`backend/app/sync/policy.py`). Every question stores the *reasons* it is not served.

A question is **eligible** when all of the following hold:

1. its editorial state is in `allowed_states` (default: only `READY_TO_SERVE`), so
   `NEEDS_REVIEW`, `NEEDS_FORMULA_REVIEW`, `NEEDS_VISUAL_REVIEW`, `NEEDS_ANSWER_LINKING`
   and `REJECTED` are excluded;
2. its type is in `allowed_types` (default: every type except `open_or_unknown`);
3. no blocking app-level content check fired:

   | reason | meaning |
   |---|---|
   | `group_context_missing` | belongs to a passage group whose shared passage is empty upstream |
   | `inline_options_in_short_response` | a "short response" whose stem actually contains A/B/C/D options |
   | `empty_stem`, `too_few_options`, `empty_option` | structural problems |
   | `render_warning` | something the student would see (stem, options or the shared passage) cannot be displayed, or is an unvalidated fallback picture of a formula |
   | `answer_not_in_options`, `answer_unparseable`, `answer_empty`, … | a provided key is unusable |
   | `no_input` | neither an answer format nor anything to compare with |
   | `removed_upstream` | the question disappeared from the canonical bank |
   | `unsupported_for_serving` | question type the app cannot present or score (`open_or_unknown`) — imported, never served |
   | `formula_render_error` | a formula the student would see fails the web renderer (KaTeX) |
   | `bank_inactive` | the question's bank is deactivated |

4. scoring is possible: `auto` (deterministic key) or — for practice only, when
   `practice_allow_self_check` is on — `self_check` (the student compares with the key/solution).
   Free-text keys are `self_check` unless `text_answers_auto_scored` is enabled.

**Exams** additionally require `scoring_mode = auto` (blueprint option `require_auto_scoring`,
default on), so a question whose answer is unresolved can never be part of a scored test.

**Admin override** (`enable` / `disable`, with a note, audited) takes precedence over the policy and
never modifies upstream data. Changing the policy re-evaluates every stored question immediately
(`hsa-app recompute-policy` does the same from the CLI).

**Topic practice** is offered only when at least `topic_min_coverage` (default 60 %) of served
questions carry a topic; the current upstream bank has no topic classification yet, so the UI hides it.

## Upstream QA states

| upstream state | app default |
|---|---|
| `READY_TO_SERVE` | eligible (auto-scored exams need a scoreable answer; self-check items are practice-only) |
| `NEEDS_ANSWER_LINKING` | imported, excluded (never in auto-scored exams — no answer is inferred) |
| `NEEDS_FORMULA_REVIEW`, `NEEDS_VISUAL_REVIEW`, `NEEDS_REVIEW`, `REJECTED` | imported, excluded |
| `NEEDS_MATH_AWARE_OCR` (documents) | no questions exist; the documents are listed per bank as deferred sources |

## Subjects

Students browse by the **effective subject**. For the HSA bank this is upstream's **final**
classification (`editorial/subject_effective.jsonl`), exactly as published. A `SUBJECT_CLASSIFICATION_REVIEW`
flag or a low confidence is shown to admins but does not change the subject, and a null final subject means
*Chưa phân loại* (`general`). The app never re-applies an original label that upstream removed, such as
`science`, `logic_reasoning`, or Vietnamese text labelled `english`. The only app-side change is an explicit
admin override (Admin → câu hỏi → Phân loại môn), which survives syncs and is listed as `subject_source = admin`.

Banks without an upstream final classification fall back to:

1. a specific original subject;
2. a second-pass inference with an accepted confidence (`subject_inference_confidence`, default `high`
   and `medium`), used only when the original is generic or unknown (`generic_source_subjects`);
3. *Chưa phân loại*.

`use_upstream_effective_subject: false` restores this fallback for the HSA bank too.
