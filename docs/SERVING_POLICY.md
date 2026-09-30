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
   | `render_warning` | something the student would see cannot be displayed |
   | `answer_not_in_options`, `answer_unparseable`, `answer_empty`, … | a provided key is unusable |
   | `no_input` | neither an answer format nor anything to compare with |
   | `removed_upstream` | the question disappeared from the canonical bank |

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
