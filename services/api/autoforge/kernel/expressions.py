"""Safe arithmetic expressions for parameters and feature dimensions.

Grammar (precedence climbing)::

    expr   := term (('+'|'-') term)*
    term   := factor (('*'|'/') factor)*
    factor := unary ('^' factor)?
    unary  := ('-'|'+') unary | atom
    atom   := NUMBER [UNIT] | NAME | NAME '(' args ')' | '(' expr ')'

No Python ``eval`` is used. Identifiers resolve only to declared parameters or a
small whitelist of constants and functions. Length literals are normalised to
millimetres, angle literals to degrees. Dimensional analysis is *not* performed:
``10 mm + 5 deg`` evaluates numerically (documented limitation).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Mapping

LENGTH_UNITS_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8, "um": 0.001}
ANGLE_UNITS_TO_DEG = {"deg": 1.0, "rad": 180.0 / math.pi}
UNIT_FACTORS = {**LENGTH_UNITS_TO_MM, **ANGLE_UNITS_TO_DEG}

FUNCTIONS: dict[str, Callable[..., float]] = {
    # Trig functions take/return degrees, consistent with angle parameters.
    "sin": lambda d: math.sin(math.radians(d)),
    "cos": lambda d: math.cos(math.radians(d)),
    "tan": lambda d: math.tan(math.radians(d)),
    "asin": lambda v: math.degrees(math.asin(v)),
    "acos": lambda v: math.degrees(math.acos(v)),
    "atan": lambda v: math.degrees(math.atan(v)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
}
CONSTANTS = {"pi": math.pi, "e": math.e}

_TOKEN = re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z_0-9]*)|(\*\*|[-+*/^(),]))")
MAX_EXPR_LEN = 512


class ExpressionError(ValueError):
    pass


@dataclass
class _Tok:
    kind: str  # num | name | op | end
    value: str


def _tokenize(src: str) -> list[_Tok]:
    if len(src) > MAX_EXPR_LEN:
        raise ExpressionError("Expression is too long")
    pos, out = 0, []
    src = src.rstrip()
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise ExpressionError(f"Unexpected character {src[pos:pos + 1]!r} at position {pos}")
        num, name, op = m.groups()
        if num is not None:
            out.append(_Tok("num", num))
        elif name is not None:
            out.append(_Tok("name", name))
        else:
            out.append(_Tok("op", "^" if op == "**" else op))
        pos = m.end()
    out.append(_Tok("end", ""))
    return out


class _Parser:
    def __init__(self, toks: list[_Tok], lookup: Callable[[str], float]):
        self.t = toks
        self.i = 0
        self.lookup = lookup

    def peek(self) -> _Tok:
        return self.t[self.i]

    def take(self) -> _Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, op: str) -> None:
        tok = self.take()
        if tok.kind != "op" or tok.value != op:
            raise ExpressionError(f"Expected {op!r}")

    def parse(self) -> float:
        v = self.expr()
        if self.peek().kind != "end":
            raise ExpressionError(f"Unexpected token {self.peek().value!r}")
        return v

    def expr(self) -> float:
        v = self.term()
        while self.peek().kind == "op" and self.peek().value in "+-":
            op = self.take().value
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self) -> float:
        v = self.factor()
        while self.peek().kind == "op" and self.peek().value in "*/":
            op = self.take().value
            r = self.factor()
            if op == "/":
                if r == 0:
                    raise ExpressionError("Division by zero")
                v = v / r
            else:
                v = v * r
        return v

    def factor(self) -> float:
        v = self.unary()
        if self.peek().kind == "op" and self.peek().value == "^":
            self.take()
            exp = self.factor()
            try:
                v = float(v**exp)
            except (OverflowError, ZeroDivisionError) as exc:
                raise ExpressionError(str(exc)) from exc
        return v

    def unary(self) -> float:
        tok = self.peek()
        if tok.kind == "op" and tok.value in "+-":
            self.take()
            v = self.unary()
            return -v if tok.value == "-" else v
        return self.atom()

    def atom(self) -> float:
        tok = self.take()
        if tok.kind == "num":
            v = float(tok.value)
            nxt = self.peek()
            if nxt.kind == "name" and nxt.value in UNIT_FACTORS:
                self.take()
                v *= UNIT_FACTORS[nxt.value]
            return v
        if tok.kind == "name":
            if self.peek().kind == "op" and self.peek().value == "(":
                fn = FUNCTIONS.get(tok.value)
                if fn is None:
                    raise ExpressionError(f"Unknown function {tok.value!r}")
                self.take()
                args = [self.expr()]
                while self.peek().kind == "op" and self.peek().value == ",":
                    self.take()
                    args.append(self.expr())
                self.expect(")")
                try:
                    return float(fn(*args))
                except (TypeError, ValueError) as exc:
                    raise ExpressionError(f"Invalid arguments for {tok.value}(): {exc}") from exc
            if tok.value in CONSTANTS:
                return CONSTANTS[tok.value]
            return self.lookup(tok.value)
        if tok.kind == "op" and tok.value == "(":
            v = self.expr()
            self.expect(")")
            return v
        raise ExpressionError("Unexpected end of expression" if tok.kind == "end" else f"Unexpected {tok.value!r}")


def referenced_names(src: str) -> set[str]:
    toks = _tokenize(str(src))
    names = set()
    for i, t in enumerate(toks):
        if t.kind != "name" or t.value in CONSTANTS or t.value in UNIT_FACTORS:
            continue
        nxt = toks[i + 1]
        if nxt.kind == "op" and nxt.value == "(":
            continue
        names.add(t.value)
    return names


def evaluate(src: str | float | int, env: Mapping[str, float] | None = None) -> float:
    if isinstance(src, (int, float)) and not isinstance(src, bool):
        v = float(src)
    else:
        env = env or {}

        def lookup(name: str) -> float:
            if name not in env:
                raise ExpressionError(f"Unknown parameter {name!r}")
            return env[name]

        v = _Parser(_tokenize(str(src)), lookup).parse()
    if not math.isfinite(v):
        raise ExpressionError("Expression does not evaluate to a finite number")
    return v


def resolve_parameters(params: list[tuple[str, str]]) -> tuple[dict[str, float], dict[str, str]]:
    """Evaluate named parameters that may reference each other.

    Returns (values, errors). Circular references are reported for every
    parameter on the cycle instead of recursing forever.
    """
    exprs = dict(params)
    values: dict[str, float] = {}
    errors: dict[str, str] = {}
    state: dict[str, int] = {}  # 0 unvisited, 1 visiting, 2 done

    def visit(name: str, stack: list[str]) -> None:
        if state.get(name) == 2:
            return
        if state.get(name) == 1:
            cycle = stack[stack.index(name):] + [name]
            for n in cycle:
                errors.setdefault(n, "Circular reference: " + " -> ".join(cycle))
            return
        state[name] = 1
        try:
            deps = referenced_names(exprs[name])
        except ExpressionError as exc:
            errors[name] = str(exc)
            state[name] = 2
            return
        for d in sorted(deps):
            if d in exprs:
                visit(d, stack + [name])
        if name not in errors:
            bad = [d for d in deps if d in errors or d not in exprs]
            if bad:
                errors[name] = f"Depends on undefined or invalid parameter(s): {', '.join(sorted(bad))}"
            else:
                try:
                    values[name] = evaluate(exprs[name], values)
                except ExpressionError as exc:
                    errors[name] = str(exc)
        state[name] = 2

    for n in exprs:
        visit(n, [])
    return values, errors
