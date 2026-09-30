"""Serving policy (eligibility rules), deterministic permutations and VietQR payloads."""
from app.commerce.service import CODE_ALPHABET, find_code, new_code
from app.commerce.vietqr import ascii_content, build_payload, crc16_ccitt
from app.exam.selection import hkey, option_permutation
from app.sync.policy import DEFAULT_POLICY, PolicyInput, effective_served, evaluate, merged_policy


def ev(state="READY_TO_SERVE", qtype="single_choice", mode="auto", reasons=(), removed=False, policy=None):
    return evaluate(PolicyInput(state, qtype, mode, list(reasons), removed), policy or DEFAULT_POLICY)


def test_only_ready_to_serve_by_default():
    assert ev() == (True, [])
    for st in ["NEEDS_REVIEW", "NEEDS_FORMULA_REVIEW", "NEEDS_VISUAL_REVIEW", "NEEDS_ANSWER_LINKING", "REJECTED"]:
        ok, reasons = ev(state=st)
        assert not ok and reasons == [f"state:{st}"]


def test_policy_is_configurable():
    p = merged_policy({"allowed_states": ["READY_TO_SERVE", "NEEDS_REVIEW"]})
    assert ev(state="NEEDS_REVIEW", policy=p)[0]
    assert not ev(state="NEEDS_ANSWER_LINKING", policy=p)[0]
    p = merged_policy({"practice_allow_self_check": False})
    assert ev(mode="self_check", policy=p) == (False, ["scoring:self_check"])
    assert merged_policy({"bogus": 1}) == DEFAULT_POLICY


def test_app_content_checks_block():
    assert ev(reasons=["group_context_missing"]) == (False, ["group_context_missing"])
    assert ev(mode="none")[1] == ["scoring:none"]
    assert ev(qtype="open_or_unknown")[1] == ["type:open_or_unknown"]
    assert ev(removed=True)[1] == ["removed_upstream"]


def test_admin_override_wins():
    assert effective_served(False, "enable") and not effective_served(True, "disable")
    assert effective_served(True, None) and not effective_served(False, None)


def test_option_permutation_is_deterministic_and_seed_dependent():
    p1 = option_permutation("seed1", "cq_x", list("ABCD"))
    assert p1 == option_permutation("seed1", "cq_x", list("ABCD"))
    assert sorted(p1) == list("ABCD")
    perms = {tuple(option_permutation(f"s{i}", "cq_x", list("ABCD"))) for i in range(40)}
    assert len(perms) > 5
    assert hkey("a", 1) == hkey("a", 1) != hkey("a", 2)


def test_vietqr_payload_structure_and_crc():
    p = build_payload("970436", "0123456789", 20000, "HSAABCD2345")
    assert p.startswith("000201010212")
    assert "0010A000000727" in p and "0208QRIBFTTA" in p
    assert "5303704" in p and "540520000" in p and "5802VN" in p
    assert "0811HSAABCD2345" in p
    assert p[-8:-4] == "6304" and crc16_ccitt(p[:-4]) == p[-4:]
    assert crc16_ccitt("123456789") == "29B1"  # CRC-16/CCITT-FALSE check value


def test_transfer_content_is_ascii():
    assert ascii_content("Thanh toán đơn HSA-123!") == "Thanh toan don HSA 123"


def test_order_codes_found_in_messy_bank_content():
    code = new_code("HSA")
    assert len(code) == 11 and all(c in CODE_ALPHABET for c in code[3:])
    spaced = f"MBVCB.123 {code[:5]} {code[5:].lower()} chuyen tien"
    assert find_code(spaced, "HSA") == code
    assert find_code("khong co ma", "HSA") is None
