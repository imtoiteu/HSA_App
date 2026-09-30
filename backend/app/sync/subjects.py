"""Effective subject resolution.

Every question keeps its original subject (from the bank), the second-pass inferred subject with
confidence and evidence (upstream editorial/subject_inference.jsonl), and an optional admin override.
The *effective* subject (question.subject_code, used for browsing/practice/exam pools) is chosen:

  1. admin override                                                  → source "admin"
  1b. upstream's validated effective subject (subject_effective.jsonl), unless flagged for review or
      of low confidence                                              → "upstream_effective"
  2. original subject when it is specific (not generic/unknown)      → "original"
  3. inferred subject when its confidence is accepted by the policy  → "inferred"
  4. the generic original (e.g. "science" → Khoa học tổng hợp)       → "original"
  5. otherwise the bank's fallback subject ("general")                → "unclassified"

Low-confidence inferences are never used. The accepted confidences and the list of generic original
values are part of the serving policy (admin-editable).
"""


def resolve_subject(original: str | None, inferred: str | None, confidence: str | None, override: str | None,
                    alias: dict, policy: dict, upstream: tuple | None = None) -> tuple[str | None, str]:
    """upstream = (effective_subject, review_flag, confidence) from editorial/subject_effective.jsonl, or
    None when the bank has no such classification (then the second-pass inference is used)."""
    generic = set(policy.get("generic_source_subjects") or [])
    accepted = set(policy.get("subject_inference_confidence") or [])
    fallback = alias.get("")
    if override:
        return override, "admin"
    if upstream is not None and policy.get("use_upstream_effective_subject", True):
        eff, review, conf = upstream
        if eff and not review and conf != "low" and eff in alias:
            return alias[eff], "upstream_effective"
        inferred = None  # upstream reviewed this question: its second pass alone is not enough
    orig_key = original or ""
    if orig_key not in generic and orig_key in alias:
        return alias[orig_key], "original"
    if inferred and confidence in accepted and inferred in alias:
        return alias[inferred], "inferred"
    if orig_key and orig_key in alias and alias[orig_key] != fallback:
        return alias[orig_key], "original"
    return fallback, "unclassified"


def upstream_tuple(q) -> tuple | None:
    """The upstream effective classification stored on a Question row (None if never provided)."""
    if q.classification_source is None:
        return None
    return q.upstream_effective_subject, q.classification_review, q.classification_confidence


def collect_tex(nodes, out: set | None = None) -> set:
    """All LaTeX strings in a content subtree (render model of content/render.py)."""
    out = set() if out is None else out
    if isinstance(nodes, list):
        for n in nodes:
            collect_tex(n, out)
    elif isinstance(nodes, dict):
        if nodes.get("t") == "m" and nodes.get("tex"):
            out.add(nodes["tex"])
        # node children ("c", table "rows", group "header"/"passage", option "content") and the
        # top-level keys of a question's content document
        for k in ("c", "rows", "header", "passage", "content", "stem", "options", "group", "solution", "explanation"):
            if k in nodes:
                collect_tex(nodes[k], out)
    return out


def question_tex(content: dict) -> tuple[set, set]:
    """(formulas the student sees before answering: stem/options/shared passage, formulas of the solution)."""
    seen: set = set()
    collect_tex(content.get("stem"), seen)
    for o in content.get("options") or []:
        collect_tex(o.get("content"), seen)
    if content.get("group"):
        collect_tex(content["group"], seen)
    sol: set = set()
    collect_tex(content.get("solution"), sol)
    collect_tex(content.get("explanation"), sol)
    return seen, sol - seen
