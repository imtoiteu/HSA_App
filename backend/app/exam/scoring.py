"""Deterministic scoring: a pure function of (frozen items, responses, scoring config).

Never scores an item whose answer is unknown: such items are `ungraded` and excluded from the
maximum score.
"""
from dataclasses import dataclass

from ..content.answers import norm_text, parse_number

THPT2025_TF = {0: 0.0, 1: 0.1, 2: 0.25, 3: 0.5, 4: 1.0}


@dataclass(frozen=True)
class ItemScore:
    outcome: str          # correct | incorrect | partial | unanswered | ungraded
    points: float         # awarded
    max_points: float     # counted towards the maximum (0 for ungraded)


def is_blank(resp: dict | None) -> bool:
    if not resp:
        return True
    if "labels" in resp:
        return not resp["labels"]
    if "values" in resp:
        return all(v is None for v in resp["values"]) if resp["values"] else True
    if "value" in resp:
        return resp["value"] is None or (isinstance(resp["value"], str) and not resp["value"].strip())
    if "text" in resp:
        return not (resp["text"] or "").strip()
    return True


def score_item(answer: dict | None, scoring_mode: str, resp: dict | None, points: float, cfg: dict) -> ItemScore:
    if scoring_mode != "auto" or not answer:
        return ItemScore("unanswered" if is_blank(resp) else "ungraded", 0.0, 0.0)
    if is_blank(resp):
        return ItemScore("unanswered", cfg.get("unanswered", 0.0) * points, points)
    frac = fraction_correct(answer, resp, cfg)
    if frac >= 1.0:
        return ItemScore("correct", cfg.get("correct", 1.0) * points, points)
    if frac > 0:
        return ItemScore("partial", round(frac * cfg.get("correct", 1.0) * points, 6), points)
    return ItemScore("incorrect", cfg.get("incorrect", 0.0) * points, points)


def fraction_correct(answer: dict, resp: dict, cfg: dict) -> float:
    k = answer["kind"]
    if k == "choice":
        want, got = set(answer["labels"]), set(resp.get("labels") or [])
        if got == want:
            return 1.0
        if len(want) > 1 and cfg.get("multi_choice") == "partial" and got and got <= want:
            return len(got) / len(want)
        return 0.0
    if k == "numeric":
        v = parse_number(resp.get("value"))
        if v is None:
            return 0.0
        tol = float(cfg.get("numeric_tolerance", 1e-6))
        target = float(answer["value"])
        return 1.0 if abs(v - target) <= tol * max(1.0, abs(target)) else 0.0
    if k == "boolean":
        return 1.0 if resp.get("value") is answer["value"] else 0.0
    if k == "tf_sequence":
        want = answer["values"]
        got = list(resp.get("values") or [])
        got += [None] * (len(want) - len(got))
        n_ok = sum(1 for w, g in zip(want, got) if g is not None and g == w)
        if n_ok == len(want):
            return 1.0
        rule = cfg.get("tf_sequence", "all_or_nothing")
        if rule == "per_statement":
            return n_ok / len(want)
        if rule == "thpt2025" and len(want) == 4:
            return THPT2025_TF[n_ok]
        return 0.0
    if k == "text":
        got = norm_text(resp.get("text") or "")
        return 1.0 if any(norm_text(a) == got for a in answer.get("accepted") or [answer["text"]]) else 0.0
    return 0.0


def score_session(items: list[dict], cfg: dict, sections: list[dict]) -> dict:
    """items: [{position, section_index, answer, scoring_mode, response, points}] → result summary.

    Returns {"score", "max_score", "scaled", "counts", "sections":[...], "items": {position: ItemScore}}.
    """
    per_item = {}
    counts = {"correct": 0, "incorrect": 0, "partial": 0, "unanswered": 0, "ungraded": 0}
    secs = [{"key": s.get("key"), "title": s.get("title"), "score": 0.0, "max_score": 0.0, "total": 0,
             "counts": {k: 0 for k in counts}} for s in sections]
    score = max_score = 0.0
    for it in items:
        r = score_item(it["answer"], it["scoring_mode"], it["response"], it["points"], cfg)
        per_item[it["position"]] = r
        counts[r.outcome] += 1
        score += r.points
        max_score += r.max_points
        if 0 <= it["section_index"] < len(secs):
            s = secs[it["section_index"]]
            s["score"] += r.points
            s["max_score"] += r.max_points
            s["total"] += 1
            s["counts"][r.outcome] += 1
    score = round(score, 6)
    scale_to = cfg.get("scale_to")
    scaled = round(score / max_score * scale_to, 2) if scale_to and max_score > 0 else None
    for s in secs:
        s["score"] = round(s["score"], 6)
    return {"score": score, "max_score": max_score, "scaled": scaled, "scale_to": scale_to, "counts": counts,
            "sections": secs, "items": per_item, "total": len(items)}
