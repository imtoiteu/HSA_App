"""Algebraic equivalence of math answers written in LaTeX (MathLive output), without eval.

A small recursive-descent parser turns a LaTeX expression into a tree of numbers, variables, constants
(π, e), + − × ÷ / ^, \\frac, \\sqrt (and n-th roots), | |, and the usual functions (sin, cos, tan, cot, ln,
log, log_b, exp). Implicit multiplication (2a, a\\sqrt{6}, (x+1)(x−1)) is understood. Two expressions are
equivalent when they agree, within a relative tolerance, at several random points of their variables:
a\\sqrt{6}/6, \\frac{a}{\\sqrt{6}} and \\sqrt{6}\\cdot a/6 are the same answer. Nothing is executed: the tree
is evaluated by a fixed interpreter, sizes and exponents are bounded.
"""
from __future__ import annotations

import math
import random
import re

MAX_LEN = 400
MAX_NODES = 300
GREEK = {"alpha", "beta", "gamma", "delta", "varepsilon", "epsilon", "theta", "lambda", "mu", "omega", "varphi",
         "phi", "rho", "sigma", "tau", "Delta", "Omega"}
FUNCS = {"sin", "cos", "tan", "cot", "tg", "cotg", "ln", "log", "exp", "arcsin", "arccos", "arctan"}


class MathError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------
# tokens
# ------------------------------------------------------------------------------------------------
TOKEN = re.compile(r"""
    (?P<num>\d+(?:[.,]\d+)?)
  | (?P<cmd>\\[A-Za-z]+|\\[,;:!\ ])
  | (?P<op>[-+*/^_=(){}\[\]|!])
  | (?P<let>[A-Za-z])
  | (?P<uni>[√π·×÷−])
  | (?P<sp>\s+)
""", re.X)


def tokenize(tex: str) -> list[tuple[str, str]]:
    if len(tex) > MAX_LEN:
        raise MathError("biểu thức quá dài")
    t = tex.strip().strip("$")
    out, i = [], 0
    while i < len(t):
        m = TOKEN.match(t, i)
        if not m:
            raise MathError(f"ký tự không hỗ trợ: {t[i]!r}")
        i = m.end()
        kind = m.lastgroup
        v = m.group(kind)
        if kind == "sp":
            continue
        if kind == "cmd":
            name = v[1:]
            if name in (",", ";", ":", "!", " ", "quad", "qquad", "left", "right", "displaystyle", "mathrm",
                        "operatorname", "mathit", "limits"):
                if name in ("mathrm", "operatorname", "mathit"):
                    out.append(("wrap", name))
                continue
            if name in ("dfrac", "tfrac"):
                name = "frac"
            if name in ("cdot", "times"):
                out.append(("op", "*"))
                continue
            if name == "div":
                out.append(("op", "/"))
                continue
            out.append(("cmd", name))
            continue
        if kind == "uni":
            v = {"√": "\\sqrt", "π": "\\pi", "·": "*", "×": "*", "÷": "/", "−": "-"}[v]
            out.append(("cmd", v[1:]) if v.startswith("\\") else ("op", v))
            continue
        if kind == "num":
            out.append(("num", v.replace(",", ".")))
            continue
        out.append((kind, v))
    return out


# ------------------------------------------------------------------------------------------------
# parser: expression → tree of tuples
# ------------------------------------------------------------------------------------------------
class Parser:
    def __init__(self, toks, declared=None):
        self.t = toks
        self.i = 0
        self.nodes = 0
        self.declared = set(declared or [])

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tk = self.peek()
        if tk[0] is None or (kind and tk[0] != kind) or (val is not None and tk[1] != val):
            raise MathError(f"cần {val or kind}, gặp {tk[1]!r}")
        self.i += 1
        return tk

    def node(self, *n):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise MathError("biểu thức quá phức tạp")
        return n

    def parse(self):
        e = self.expr()
        if self.peek()[0] is not None:
            raise MathError(f"thừa {self.peek()[1]!r}")
        return e

    def expr(self):
        e = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            e = self.node(op, e, self.term())
        return e

    def term(self):
        e = self.unary()
        while True:
            tk = self.peek()
            if tk in (("op", "*"), ("op", "/")):
                self.take()
                e = self.node(tk[1], e, self.unary())
            elif self.starts_factor():
                e = self.node("*", e, self.power())      # implicit multiplication
            else:
                return e

    def starts_factor(self):
        k, v = self.peek()
        if k in ("num", "let", "wrap"):
            return True
        if k == "cmd" and v not in ("right",):
            return True
        return k == "op" and v in ("(", "[", "{") or (k == "op" and v == "|" and not self.closing_bar())

    def closing_bar(self):
        return getattr(self, "_bars", 0) > 0

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            return self.node("neg", self.unary())
        if self.peek() == ("op", "+"):
            self.take()
            return self.unary()
        return self.power()

    def power(self):
        base = self.postfix(self.atom())
        if self.peek() == ("op", "^"):
            self.take()
            ex = self.group_or_atom(unary=True)
            return self.node("^", base, ex)
        return base

    def postfix(self, e):
        while self.peek() == ("op", "!"):
            self.take()
            e = self.node("fact", e)
        return e

    def group_or_atom(self, unary=False):
        if self.peek() == ("op", "{"):
            self.take()
            e = self.expr()
            self.take("op", "}")
            return e
        if unary and self.peek() == ("op", "-"):
            self.take()
            return self.node("neg", self.group_or_atom())
        return self.atom()

    def atom(self):
        k, v = self.peek()
        if k == "num":
            self.take()
            return self.node("num", float(v))
        if k == "let":
            self.take()
            if v == "e" and "e" not in self.declared and self.peek() != ("op", "_"):
                return self.node("const", math.e)   # Euler's number, unless e is a declared variable
            name = v
            if self.peek() == ("op", "_"):
                self.take()
                sub = self.subscript()
                name = f"{v}_{sub}"
            return self.node("var", name)
        if k == "wrap":     # \mathrm{…} / \operatorname{…}: a function name or an upright constant
            self.take()
            self.take("op", "{")
            word = ""
            while self.peek() != ("op", "}"):
                word += self.take()[1]
            self.take("op", "}")
            if word in FUNCS:
                return self.func(word)
            if word == "e":
                return self.node("const", math.e)
            if len(word) == 1 and word.isalpha():
                return self.node("var", word)
            raise MathError(f"không hiểu {word!r}")
        if k == "op" and v in ("(", "["):
            self.take()
            e = self.expr()
            self.take("op", ")" if v == "(" else "]")
            return e
        if k == "op" and v == "{":
            self.take()
            e = self.expr()
            self.take("op", "}")
            return e
        if k == "op" and v == "|":
            self.take()
            self._bars = getattr(self, "_bars", 0) + 1
            e = self.expr()
            self._bars -= 1
            self.take("op", "|")
            return self.node("abs", e)
        if k == "cmd":
            self.take()
            if v == "frac":
                num = self.group_or_atom()
                den = self.group_or_atom()
                return self.node("/", num, den)
            if v == "sqrt":
                n = None
                if self.peek() == ("op", "["):
                    self.take()
                    n = self.expr()
                    self.take("op", "]")
                arg = self.group_or_atom()
                return self.node("root", arg, n)
            if v == "pi":
                return self.node("const", math.pi)
            if v in GREEK:
                return self.node("var", v)
            if v in FUNCS:
                return self.func(v)
            if v == "infty":
                raise MathError("vô cực không phải là một giá trị")
            raise MathError(f"lệnh \\{v} không hỗ trợ")
        raise MathError(f"không đọc được {v!r}")

    def subscript(self):
        if self.peek() == ("op", "{"):
            self.take()
            s = ""
            while self.peek() != ("op", "}"):
                tk = self.take()
                s += tk[1]
            self.take("op", "}")
            return s
        return self.take()[1]

    def func(self, name):
        base = None
        if name == "log" and self.peek() == ("op", "_"):
            self.take()
            base = self.group_or_atom()
        power = None
        if self.peek() == ("op", "^"):     # sin^2 x
            self.take()
            power = self.group_or_atom(unary=True)
        arg = self.power() if self.peek()[1] not in ("(",) else self.atom()
        f = self.node("fn", name, arg, base)
        return self.node("^", f, power) if power is not None else f


def parse(tex: str, declared=None):
    if not tex or not tex.strip():
        raise MathError("biểu thức trống")
    if "=" in tex:
        # "y = …": the answer is the right-hand side when the left is a single letter
        left, _, right = tex.partition("=")
        if re.fullmatch(r"\s*[A-Za-z](?:_\{?\w+\}?)?\s*", left) and right.strip():
            tex = right
        else:
            raise MathError("đáp án là một biểu thức, không phải một phương trình")
    return Parser(tokenize(tex), declared).parse()


def variables(tree) -> set[str]:
    out = set()

    def walk(n):
        if not isinstance(n, tuple):
            return
        if n[0] == "var":
            out.add(n[1])
            return
        for x in n[1:]:
            walk(x)
    walk(tree)
    return out


# ------------------------------------------------------------------------------------------------
# evaluation (a fixed interpreter over the tree)
# ------------------------------------------------------------------------------------------------
def evaluate(n, env: dict) -> float:
    k = n[0]
    if k == "num":
        return n[1]
    if k == "const":
        return n[1]
    if k == "var":
        if n[1] not in env:
            raise MathError(f"biến {n[1]} chưa khai báo")
        return env[n[1]]
    if k == "neg":
        return -evaluate(n[1], env)
    if k in ("+", "-", "*", "/"):
        a, b = evaluate(n[1], env), evaluate(n[2], env)
        if k == "+":
            return a + b
        if k == "-":
            return a - b
        if k == "*":
            return a * b
        if b == 0:
            raise ZeroDivisionError
        return a / b
    if k == "^":
        a, b = evaluate(n[1], env), evaluate(n[2], env)
        if abs(b) > 64 or (abs(a) > 1e6 and b > 4):
            raise MathError("số mũ quá lớn")
        r = a ** b
        if isinstance(r, complex):
            raise ValueError
        return r
    if k == "root":
        a = evaluate(n[1], env)
        deg = evaluate(n[2], env) if n[2] is not None else 2.0
        if deg == 0:
            raise ZeroDivisionError
        if a < 0:
            if abs(deg - round(deg)) < 1e-12 and int(round(deg)) % 2 == 1:
                return -((-a) ** (1 / deg))
            raise ValueError
        return a ** (1 / deg)
    if k == "abs":
        return abs(evaluate(n[1], env))
    if k == "fact":
        a = evaluate(n[1], env)
        if a < 0 or a > 50 or abs(a - round(a)) > 1e-9:
            raise ValueError
        return float(math.factorial(int(round(a))))
    if k == "fn":
        name, arg, base = n[1], evaluate(n[2], env), n[3]
        if name == "sin":
            return math.sin(arg)
        if name == "cos":
            return math.cos(arg)
        if name in ("tan", "tg"):
            return math.tan(arg)
        if name in ("cot", "cotg"):
            return 1 / math.tan(arg)
        if name == "ln":
            return math.log(arg)
        if name == "log":
            b = evaluate(base, env) if base is not None else 10.0
            return math.log(arg) / math.log(b)
        if name == "exp":
            if arg > 700:
                raise ValueError
            return math.exp(arg)
        if name == "arcsin":
            return math.asin(arg)
        if name == "arccos":
            return math.acos(arg)
        if name == "arctan":
            return math.atan(arg)
    raise MathError(f"không tính được {k}")


def check(tex: str, declared: list[str] | None = None) -> str | None:
    """None when the expression is understood (and uses only declared variables, if any are declared)."""
    try:
        tree = parse(tex, declared)
    except MathError as ex:
        return str(ex)
    free = variables(tree)
    if declared is not None and declared and not free <= set(declared):
        return "biến không khai báo: " + ", ".join(sorted(free - set(declared)))
    return None


def equivalent(a: str, b: str, declared: list[str] | None = None, tolerance: float = 1e-6, samples: int = 8,
               seed: int = 7) -> bool:
    """Do two LaTeX expressions denote the same value for every value of their variables?"""
    try:
        ta, tb = parse(a, declared), parse(b, declared)
    except MathError:
        return False
    names = sorted(variables(ta) | variables(tb) | set(declared or []))
    if declared and not (variables(ta) | variables(tb)) <= set(declared):
        return False
    rng = random.Random(seed)
    ok = tried = 0
    for _ in range(samples * 4):
        if ok >= samples:
            break
        env = {v: rng.uniform(0.3, 3.0) * (1 if rng.random() < 0.8 else -1) for v in names}
        tried += 1
        try:
            x, y = evaluate(ta, env), evaluate(tb, env)
        except (ValueError, ZeroDivisionError, OverflowError, MathError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if abs(x - y) > tolerance * max(1.0, abs(x), abs(y)):
            return False
        ok += 1
        if not names:
            break   # constants: one evaluation decides
    return ok >= (1 if not names else 3)


def canonical(tex: str) -> str:
    """Stored form: LaTeX as entered, with \\dfrac/\\tfrac → \\frac and spacing commands removed."""
    t = (tex or "").strip().strip("$")
    t = re.sub(r"\\[dt]frac", r"\\frac", t)
    t = re.sub(r"\\[,;:!]|\\quad|\\qquad", "", t)
    return re.sub(r"\s+", " ", t).strip()
