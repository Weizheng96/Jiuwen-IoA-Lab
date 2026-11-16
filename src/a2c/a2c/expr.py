"""Price expressions (design: 设计/线上编码.md §8).

Formulas come from an untrusted provider, so they are never ``eval``-ed: a lexical check
admits only decimal literals without leading zeros, identifiers with dots, ``+ - * /``,
parentheses and spaces, with a unary sign only directly before a number or a name; a
whitelist AST walk evaluates them in IEEE 754 double precision. No calls, no loops, so
evaluation always terminates; size, node count and depth are bounded.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from typing import Callable

MAX_LENGTH = 2048
MAX_NODES = 256
MAX_DEPTH = 32

_TOKEN = re.compile(r" *(?:((?:0|[1-9][0-9]*)(?:\.[0-9]+)?)|([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
                    r"|([-+*/()]))")
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


class ExprError(ValueError):
    pass


def _lex(expr: str) -> None:
    if not isinstance(expr, str) or not expr.strip(" "):
        raise ExprError("empty expression")
    if len(expr) > MAX_LENGTH:
        raise ExprError(f"expression longer than {MAX_LENGTH}")
    pos, tokens = 0, []
    while pos < len(expr):
        if expr[pos:].strip(" ") == "":
            break
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ExprError(f"unexpected character at {pos}: {expr[pos]!r}")
        kind = "num" if m.group(1) else "name" if m.group(2) else m.group(3)
        if tokens and tokens[-1] in ("num", "name") and kind in ("num", "name"):
            raise ExprError(f"malformed literal or name at {pos}")
        tokens.append(kind)
        pos = m.end()
    for i, tok in enumerate(tokens):
        unary = tok in "+-" and (i == 0 or tokens[i - 1] in ("+", "-", "*", "/", "("))
        if unary and (i + 1 == len(tokens) or tokens[i + 1] not in ("num", "name")):
            raise ExprError("a unary sign applies only directly to a number or a name")


def _parse(expr: str) -> ast.Expression:
    _lex(expr)
    try:
        tree = ast.parse(expr.strip(" "), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {expr}") from e
    nodes = 0

    def check(n: ast.AST, depth: int) -> None:
        nonlocal nodes
        if not isinstance(n, ast.Expression):           # nodes: numbers, names, unary signs, binary operators
            nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ExprError("expression too large")
        if isinstance(n, ast.Expression):
            check(n.body, depth + 1)
        elif isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(n.value, (int, float)):
                raise ExprError(f"only numeric literals, got {n.value!r}")
        elif isinstance(n, ast.BinOp):
            if type(n.op) not in _BINOPS:
                raise ExprError(f"operator {type(n.op).__name__} not allowed")
            check(n.left, depth + 1)
            check(n.right, depth + 1)
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            if isinstance(n.operand, ast.UnaryOp):
                raise ExprError("repeated unary operator")
            check(n.operand, depth + 1)
        elif isinstance(n, (ast.Name, ast.Attribute)):
            _path_of(n)
        else:
            raise ExprError(f"node {type(n).__name__} not allowed")

    check(tree, 0)
    return tree


def _path_of(node: ast.AST) -> list[str]:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        raise ExprError("a variable is identifiers joined by dots")
    parts.append(cur.id)
    return list(reversed(parts))


def variables(expr: str) -> list[list[str]]:
    """Variable paths referenced by ``expr``; also validates it."""
    out: list[list[str]] = []

    def walk(n: ast.AST) -> None:
        if isinstance(n, (ast.Name, ast.Attribute)):
            out.append(_path_of(n))
            return
        for child in ast.iter_child_nodes(n):
            walk(child)

    walk(_parse(expr))
    return out


def evaluate(expr: str, resolve: Callable[[list[str]], float]) -> float:
    """Evaluate in binary64; ``resolve`` maps a variable path to a number."""

    def walk(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant):
            try:
                value = float(n.value)
            except OverflowError as e:
                raise ExprError("numeric literal too large") from e
            if not math.isfinite(value):
                raise ExprError("numeric literal too large")
            return value
        if isinstance(n, ast.BinOp):
            a, b = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Div) and b == 0:
                raise ExprError("division by zero")
            out = _BINOPS[type(n.op)](a, b)
            if not math.isfinite(out):
                raise ExprError("intermediate result is not finite")
            return out
        if isinstance(n, ast.UnaryOp):
            v = walk(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        value = resolve(_path_of(n))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ExprError(f"{'.'.join(_path_of(n))} is not a number")
        return float(value)

    result = walk(_parse(expr))
    if not math.isfinite(result):
        raise ExprError("result is not finite")
    return result


__all__ = ["ExprError", "variables", "evaluate", "MAX_LENGTH", "MAX_NODES", "MAX_DEPTH"]
