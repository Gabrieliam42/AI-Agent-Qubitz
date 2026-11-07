"""Closed, bounded AST comparisons of explicitly preserved default calls; never exec/import."""
from __future__ import annotations

import ast
import itertools
import operator
import re

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
ERRORS = {"ValueError": ValueError, "TypeError": TypeError, "ZeroDivisionError": ZeroDivisionError,
          "OverflowError": OverflowError}


def counterexample(receipt: dict) -> dict | None:
    """Explain one rejected candidate; this is not evidence for a future candidate."""
    if receipt.get("status") != "failed":
        return None
    for case in receipt.get("cases", ()):
        if not isinstance(case, dict) or case.get("matched") is not False:
            continue
        arguments = case.get("arguments_repr")
        before, after = case.get("before"), case.get("after")
        if (not isinstance(arguments, str) or not all(
            isinstance(value, (tuple, list)) and len(value) in {2, 3}
            and all(isinstance(item, str) for item in value) for value in (before, after)
        )):
            return None
        return {
            "function": str(case.get("function", ""))[:128],
            "arguments_repr": arguments[:512],
            "before": [value[:256] for value in before], "after": [value[:256] for value in after],
            "truncated": len(arguments) > 512 or any(len(value) > 256 for value in (*before, *after)),
            "scope": "One bounded same-input mismatch in the rejected candidate, not universal equivalence.",
        }
    return None


def recovery_counterexamples(comparisons: dict, *, turn_id, workspace_generation, snapshots: dict) -> list[dict]:
    results = []
    for path, receipt in comparisons.items():
        if not isinstance(receipt, dict):
            continue
        if (receipt.get("turn_id") != turn_id or receipt.get("workspace_generation") != workspace_generation
                or receipt.get("before_sha256") != snapshots.get(path)
                or not all(isinstance(receipt.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", receipt[key])
                           for key in ("before_sha256", "after_sha256"))):
            continue
        detail = counterexample(receipt)
        if detail is not None:
            results.append({"path": path, "before_sha256": receipt["before_sha256"],
                            "rejected_candidate_sha256": receipt["after_sha256"], **detail})
        if len(results) == 6:
            break
    return results


def requested(prompt: str) -> bool:
    pattern = r"\b(?:preserv\w*|retain\w*|keep|unchanged)\b[^.\n]{0,200}" + (
        r"\b(?:default\w*|omitted\w*|without\s+[^.\n]{0,50}(?:argument|parameter))")
    for clause in re.split(r"[.;\n]", prompt):
        for match in re.finditer(pattern, clause, re.IGNORECASE):
            prefix = clause[:match.start()]
            if not re.search(r"\b(?:not|never|don't|without)(?:\s+\w+){0,2}\s*$", prefix, re.IGNORECASE):
                return True
    return False


def definitions(source: str) -> dict:
    if len(source) > 24000:
        raise ValueError("source bound")
    tree = ast.parse(source)
    if sum(1 for _ in ast.walk(tree)) > 1000:
        raise ValueError("AST bound")
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if not isinstance(node, ast.FunctionDef) or node.name in result:
            raise ValueError("only pure top-level functions supported")
        if (node.decorator_list or node.returns or node.args.kwonlyargs or node.args.vararg or node.args.kwarg
                or any(arg.annotation for arg in [*node.args.posonlyargs, *node.args.args])):
            raise ValueError("unsupported signature")
        result[node.name] = node
    return result


def run(node: ast.FunctionDef, arguments: tuple):
    parameters = [a.arg for a in [*node.args.posonlyargs, *node.args.args]]
    defaults = [ast.literal_eval(n) for n in node.args.defaults]
    if not len(parameters) - len(defaults) <= len(arguments) <= len(parameters):
        raise ValueError("incompatible default signature")
    scope = dict(zip(parameters, arguments, strict=False))
    missing = len(parameters) - len(arguments)
    scope.update(zip(parameters[len(arguments):], defaults[-missing:] if missing else [], strict=True))
    steps = 0

    def bounded(value):
        if type(value) not in {int, float, bool, type(None), list, tuple, str}:
            raise ValueError("unsupported value")
        if type(value) is int and value.bit_length() > 4096:
            raise ValueError("integer bound")
        if isinstance(value, (list, tuple, str)) and len(value) > 64:
            raise ValueError("sequence bound")
        return value

    def expression(item):
        nonlocal steps
        steps += 1
        if steps > 1000:
            raise ValueError("step bound")
        if isinstance(item, ast.Constant):
            return bounded(item.value)
        if isinstance(item, ast.Name) and item.id in scope:
            return scope[item.id]
        if isinstance(item, ast.BinOp) and type(item.op) in OPS:
            a, b = expression(item.left), expression(item.right)
            if isinstance(a, (str, list, tuple)) or isinstance(b, (str, list, tuple)):
                raise ValueError("sequence arithmetic unsupported")
            return bounded(OPS[type(item.op)](a, b))
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, (ast.USub, ast.UAdd, ast.Not)):
            value = expression(item.operand)
            return not value if isinstance(item.op, ast.Not) else -value if isinstance(item.op, ast.USub) else +value
        if isinstance(item, ast.BoolOp) and isinstance(item.op, (ast.And, ast.Or)):
            for operand in item.values:
                value = expression(operand)
                if (isinstance(item.op, ast.And) and not value) or (isinstance(item.op, ast.Or) and value):
                    return value
            return value
        if isinstance(item, ast.Compare) and all(type(op) in CMP for op in item.ops):
            left = expression(item.left)
            for operation, comparator in zip(item.ops, item.comparators, strict=True):
                right = expression(comparator)
                if not CMP[type(operation)](left, right):
                    return False
                left = right
            return True
        if isinstance(item, ast.IfExp):
            return expression(item.body if expression(item.test) else item.orelse)
        if (isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "sum"
                and item.func.id not in scope and len(item.args) == 1 and not item.keywords):
            values = expression(item.args[0])
            if type(values) in {int, float, bool, type(None)}:
                return bounded(sum(values))
            if not isinstance(values, (list, tuple)) or len(values) > 64:
                raise ValueError("bounded sum needs a sequence")
            return bounded(sum(values))
        raise ValueError("unsupported expression")

    def statements(nodes):
        for item in nodes:
            if isinstance(item, ast.Return):
                return True, expression(item.value) if item.value else None
            if isinstance(item, ast.Assign) and all(isinstance(t, ast.Name) for t in item.targets):
                value = expression(item.value)
                for target in item.targets:
                    scope[target.id] = value
            elif isinstance(item, ast.If):
                returned, value = statements(item.body if expression(item.test) else item.orelse)
                if returned:
                    return True, value
            elif (isinstance(item, ast.Raise) and item.cause is None and isinstance(item.exc, ast.Call)
                  and isinstance(item.exc.func, ast.Name) and item.exc.func.id in ERRORS
                  and item.exc.func.id not in scope and not item.exc.keywords and len(item.exc.args) <= 1
                  and all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in item.exc.args)):
                return True, ("exception", item.exc.func.id,
                              str(item.exc.args[0].value) if item.exc.args else "")
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                continue
            else:
                raise ValueError("unsupported statement")
        return False, None

    try:
        _, value = statements(node.body)
        return value if isinstance(value, tuple) and value[:1] == ("exception",) else (type(value).__name__, repr(value))
    except (TypeError, OverflowError, ZeroDivisionError) as exc:
        return "exception", type(exc).__name__, str(exc)


def default_calls(arity: int):
    inputs = [0, 1, -1, 0.5, 9007199254740993, 10**400,
              [], [20, 30], [9007199254740993], [10**400], [0.25, 0.5], [-1, 1]]
    if arity == 0:
        yield ()
    elif arity == 1:
        yield from ((value,) for value in inputs)
    else:
        # Rotate pairs so the existing bound includes sequences, not just scalar first arguments.
        for shift in range(len(inputs)):
            for index, value in enumerate(inputs):
                yield value, inputs[(index + shift) % len(inputs)]


def compare(before: str, after: str) -> dict:
    cases = []
    try:
        old, new = definitions(before), definitions(after)
        changed = [name for name in old if name not in new or ast.dump(old[name]) != ast.dump(new[name])]
        for name in changed:
            previous, current = old[name], new.get(name)
            if current is None:
                return {"status": "failed", "reason": "previous function removed", "function": name, "cases": cases}
            old_args = [a.arg for a in [*previous.args.posonlyargs, *previous.args.args]]
            new_args = [a.arg for a in [*current.args.posonlyargs, *current.args.args]]
            required = len(old_args) - len(previous.args.defaults)
            new_required = len(new_args) - len(current.args.defaults)
            if len(old_args) > 2 or new_args[:len(old_args)] != old_args or new_required > required:
                raise ValueError("signature comparison unavailable")
            function_cases = []
            # Omit old optional parameters too: their explicit semantics may intentionally change.
            for arguments in itertools.islice(default_calls(required), 48):
                a, b = run(previous, arguments), run(current, arguments)
                case = {"function": name, "arguments_repr": repr(arguments),
                        "omitted_parameters": old_args[required:],
                        "before": a, "after": b, "matched": a == b}
                cases.append(case)
                function_cases.append(case)
                if a != b:
                    return {"status": "failed", "cases": cases, "scope": "bounded same-input default comparison"}
            if not any(case["before"][0] != "exception" for case in function_cases):
                raise ValueError("no supported successful default calls for " + name)
        if not changed or not cases:
            raise ValueError("no supported changed default calls")
        return {"status": "passed_bounded_comparison", "cases": cases,
                "scope": "listed same-input calls only, not universal behavioral equivalence"}
    except (ValueError, SyntaxError, RecursionError) as exc:
        return {"status": "unavailable", "reason": str(exc), "cases": cases}
