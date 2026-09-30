"""Serving policy: decides which questions students may receive. Pure and configurable.

The configuration lives in app_setting['serving_policy'] (admin-editable); DEFAULT_POLICY is used
when unset. Every exclusion is recorded as a reason string so admins can see *why* a question is
not served. An admin override (enable/disable) always wins, but never changes upstream data.
"""
from dataclasses import dataclass

EDITORIAL_STATES = ["REJECTED", "NEEDS_FORMULA_REVIEW", "NEEDS_VISUAL_REVIEW", "NEEDS_ANSWER_LINKING",
                    "NEEDS_REVIEW", "READY_TO_SERVE"]

# app-level content checks computed by the builders (see sync/hsa_upstream.py)
APP_REASONS = {
    "group_context_missing": "Câu thuộc nhóm nhưng đoạn dữ liệu chung bị thiếu",
    "inline_options_in_short_response": "Câu trả lời ngắn có phương án A/B/C/D nằm trong đề",
    "empty_stem": "Đề bài trống",
    "too_few_options": "Ít hơn 2 phương án",
    "empty_option": "Có phương án trống",
    "render_warning": "Có nội dung không hiển thị được (công thức/hình/bảng)",
    "answer_not_in_options": "Đáp án không khớp phương án",
    "answer_unparseable": "Không đọc được đáp án",
    "answer_empty": "Đáp án trống",
    "answer_without_options": "Đáp án dạng nhãn nhưng không có phương án",
    "answer_kind_unknown": "Dạng đáp án không hỗ trợ",
    "no_input": "Không xác định được cách trả lời",
    "removed_upstream": "Câu hỏi đã bị gỡ khỏi ngân hàng nguồn",
    "unsupported_for_serving": "Dạng câu hỏi chưa xác định được cách làm/chấm (open_or_unknown)",
    "group_membership_suspect": "Đoạn dữ liệu chung ghi phạm vi câu khác với số câu này (gắn nhầm đoạn)",
    "formula_render_error": "Công thức trong đề/phương án không hiển thị được trên web (KaTeX)",
    "solution_formula_render_error": "Công thức trong lời giải không hiển thị được trên web (KaTeX)",
}
# reasons recorded for admins but not blocking by default
NON_BLOCKING_REASONS = {"solution_formula_render_error"}

DEFAULT_POLICY = {
    "allowed_states": ["READY_TO_SERVE"],
    "allowed_types": ["single_choice", "multiple_choice", "true_false", "true_false_statements",
                      "numeric_response", "short_response", "error_identification", "constructed_response"],
    "blocking_reasons": sorted(set(APP_REASONS) - {"solution_formula_render_error"}),
    "practice_allow_self_check": True,     # practice may include items the student checks against the key
    "text_answers_auto_scored": False,     # free-text keys are compared by the student, not auto-scored
    "numeric_tolerance": 1e-6,             # relative tolerance for numeric answers
    "topic_min_coverage": 0.6,             # topic practice only when ≥60% of served questions have a topic
    # second-pass subject inference: which confidences may set the effective subject, and which
    # original subject values count as generic/unknown (inference may refine them)
    "subject_inference_confidence": ["high", "medium"],
    "generic_source_subjects": ["", "science", "logic_reasoning"],
    # upstream's validated final classification (editorial/subject_effective.jsonl) takes precedence
    "use_upstream_effective_subject": True,
}


def merged_policy(stored: dict | None) -> dict:
    p = dict(DEFAULT_POLICY)
    if stored:
        p.update({k: v for k, v in stored.items() if k in DEFAULT_POLICY})
    return p


@dataclass
class PolicyInput:
    editorial_state: str
    question_type: str
    scoring_mode: str
    app_reasons: list
    removed_upstream: bool = False


def evaluate(pi: PolicyInput, policy: dict) -> tuple[bool, list[str]]:
    reasons = []
    if pi.editorial_state not in policy["allowed_states"]:
        reasons.append(f"state:{pi.editorial_state}")
    if pi.question_type not in policy["allowed_types"]:
        reasons.append(f"type:{pi.question_type}")
    blocking = set(policy["blocking_reasons"])
    reasons += [r for r in pi.app_reasons if r in blocking]
    if pi.removed_upstream:
        reasons.append("removed_upstream")
    if pi.scoring_mode == "none":
        reasons.append("scoring:none")
    elif pi.scoring_mode == "self_check" and not policy["practice_allow_self_check"]:
        reasons.append("scoring:self_check")
    return (not reasons), reasons


def effective_served(policy_eligible: bool, admin_override: str | None) -> bool:
    if admin_override == "enable":
        return True
    if admin_override == "disable":
        return False
    return policy_eligible
