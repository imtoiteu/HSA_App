"""Builds a sample of REAL canonical records from the read-only upstream (skipped when absent).

Run: HSA_REAL_UPSTREAM=/root/imtoiteu/HSA-question-bank pytest -m upstream
"""
import itertools
import json
import os
from pathlib import Path

import pytest

from app.content.render import plain_text
from app.sync.hsa_upstream import UpstreamSource, build_question
from app.sync.media import MediaStore

ROOT = Path(os.environ.get("HSA_REAL_UPSTREAM", "/root/imtoiteu/HSA-question-bank"))
pytestmark = [pytest.mark.upstream,
              pytest.mark.skipif(not (ROOT / "question-bank/sqlite/hsa_question_bank.sqlite").exists(),
                                 reason="real upstream bank not available")]
NODE_TYPES = {"p", "img", "table", "warn", "s", "m", "br", "tab", "range"}


def walk(n, seen):
    if isinstance(n, list):
        for x in n:
            walk(x, seen)
    elif isinstance(n, dict):
        if "t" in n:
            seen.add(n["t"])
        for k in ("c", "rows", "header", "passage"):
            if k in n:
                walk(n[k], seen)


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return UpstreamSource(ROOT, MediaStore(tmp_path_factory.mktemp("media")))


def test_sample_builds_cleanly(src):
    types, states = set(), set()
    for q in itertools.islice(src.iter_questions(after="cq_8"), 300):
        b = build_question(src, q)
        json.dumps(b.content)  # serialisable
        seen = set()
        walk([b.content["stem"], [o["content"] for o in b.content["options"]], b.content["solution"] or []], seen)
        assert seen <= NODE_TYPES
        if b.answer:
            assert q["answer_source_type"] in ("SOURCE_PROVIDED_ANSWER", "SOURCE_PROVIDED_SOLUTION") or \
                src.q_over.get(q["canonical_question_id"])
        else:
            assert b.scoring_mode != "auto"
        if b.states[0] == "READY_TO_SERVE" and not b.app_reasons:
            assert plain_text(b.content["stem"]) or any(x["t"] in ("img", "table") for x in b.content["stem"])
        types.add(b.question_type)
        states.add(b.states[0])
    assert "single_choice" in types and "READY_TO_SERVE" in states


def test_known_record_formulas(src):
    q = next(src.iter_questions(only=["cq_000b8460cfc1ed33"]), None)
    if q is None:
        pytest.skip("record not present in this upstream snapshot")
    b = build_question(src, q)
    opt_math = [n for o in b.content["options"] for p in o["content"] for n in p["c"] if n["t"] == "m"]
    assert len(opt_math) == 4 and all("1" in n["tex"] for n in opt_math)
    assert b.answer == {"kind": "choice", "labels": ["C"]}


def test_real_answers_normalise_and_match_their_input(src):
    """Representative REAL records of every answer kind: a trusted key must normalise to an answer the
    scoring engine can use with the rendered question (or be excluded with a recorded reason)."""
    import collections
    from app.sync.audit import _answer_compatible
    con = src.con
    picked = []
    for kind, qtype in [("labels", "single_choice"), ("numeric", "numeric_response"), ("text", "short_response"),
                        ("text", "single_choice"), ("labels", "error_identification"), ("numeric", "true_false_statements"),
                        ("text", "true_false"), ("text", "true_false_statements")]:
        picked += [r[0] for r in con.execute(
            "SELECT canonical_question_id FROM canonical_question WHERE question_type=? AND "
            "json_extract(correct_answer,'$.kind')=? AND answer_source_type IN "
            "('SOURCE_PROVIDED_ANSWER','SOURCE_PROVIDED_SOLUTION') ORDER BY canonical_question_id LIMIT 25", (qtype, kind))]
    assert len(picked) > 80
    outcome = collections.Counter()
    for q in src.iter_questions(only=picked):
        b = build_question(src, q)
        if b.answer is None:
            # a provided key that cannot be used must leave a reason and must not be auto-scored
            assert b.app_reasons and b.scoring_mode != "auto", q["canonical_question_id"]
            outcome["rejected_with_reason"] += 1
            continue
        if b.scoring_mode == "auto":
            assert _answer_compatible(b.answer, b.content) is None, (q["canonical_question_id"], b.answer)
            outcome["auto"] += 1
        else:
            outcome[b.scoring_mode] += 1
    assert outcome["auto"] > 50


def test_real_subject_inference_loaded(src):
    inferred = [o for o in src.inference.values() if o.get("inferred_subject")]
    assert len(src.inference) > 40000 and len(inferred) > 15000
    assert {o["confidence"] for o in inferred} <= {"high", "medium"}
