"""Bounded scalar-function checks, not a Python executor or a general test runner."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import operator
from pathlib import Path
from typing import Any

ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
COMPARISONS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
               ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
EXCEPTIONS = {"ValueError", "ZeroDivisionError", "TypeError", "OverflowError"}


def scalar(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) not in {int, float} or abs(value) > 10**12 or not math.isfinite(value):
        raise ValueError("Only finite bounded scalar values are supported")
    return value


def functions(source: str) -> dict[str, ast.FunctionDef]:
    if len(source) > 24000:
        raise ValueError("Source exceeds the bounded probe limit")
    tree = ast.parse(source)
    if sum(1 for _ in ast.walk(tree)) > 1000:
        raise ValueError("Source exceeds the bounded AST limit")
    result = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if not isinstance(node, ast.FunctionDef) or node.name in result or node.name in EXCEPTIONS:
            raise ValueError("Module-level effects, imports, classes, duplicate definitions and shadowed exceptions are unsupported")
        args = node.args
        if (node.decorator_list or node.returns or args.vararg or args.kwarg or args.kwonlyargs
                or any(arg.annotation for arg in [*args.posonlyargs, *args.args])):
            raise ValueError("Decorators, annotations and variadic functions are unsupported")
        if len(args.posonlyargs) + len(args.args) > 6:
            raise ValueError("Too many function arguments")
        for default in args.defaults:
            scalar(ast.literal_eval(default))
        names = {arg.arg for arg in [*args.posonlyargs, *args.args]}
        names.update(item.id for item in ast.walk(node) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store))
        if names.intersection(EXCEPTIONS):
            raise ValueError("Shadowed exceptions are unsupported")
        raises = {item.exc for item in ast.walk(node) if isinstance(item, ast.Raise)}
        allowed = (ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.If, ast.Assign, ast.Name,
                   ast.Load, ast.Store, ast.Constant, ast.Expr, ast.BinOp, ast.UnaryOp, ast.Compare,
                   ast.BoolOp, ast.IfExp, ast.And, ast.Or, ast.Not, ast.UAdd, ast.USub, ast.Raise,
                   ast.Call, *ARITHMETIC, *COMPARISONS)
        for item in ast.walk(node):
            if not isinstance(item, allowed) or isinstance(item, ast.FunctionDef) and item is not node:
                raise ValueError("Unsupported function syntax; no Python code was executed")
            if isinstance(item, ast.Name) and item.id not in names | EXCEPTIONS:
                raise ValueError("Global lookups are unsupported")
            if isinstance(item, ast.Assign) and any(not isinstance(t, ast.Name) for t in item.targets):
                raise ValueError("Only local scalar assignments are supported")
            if isinstance(item, ast.Expr) and not (
                isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)
            ):
                raise TypeError("Only docstring expression statements are supported")
            if isinstance(item, ast.Raise) and (item.cause is not None or not isinstance(item.exc, ast.Call)):
                raise ValueError("Only explicit bounded exceptions are supported")
            if isinstance(item, ast.Call) and (
                item not in raises or not isinstance(item.func, ast.Name) or item.func.id not in EXCEPTIONS
                or item.keywords or len(item.args) > 1 or any(
                    not isinstance(arg, ast.Constant) or not isinstance(arg.value, str) or len(arg.value) > 160
                    for arg in item.args
                )
            ):
                raise ValueError("Function calls are unsupported except literal exception construction")
        result[node.name] = node
    return result


def numeric_contract(before: str, after: str) -> dict[str, dict[str, Any]]:
    old, new = functions(before), functions(after)
    if old.keys() - new.keys():
        raise ValueError("Removing functions requires an existing test suite")
    changed = {name: node for name, node in new.items()
               if name not in old or ast.dump(node) != ast.dump(old[name])}
    if not changed:
        raise ValueError("No changed numeric function to verify")
    return {name: {"arguments": [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]],
                   "raises": sorted({item.exc.func.id for item in ast.walk(node) if isinstance(item, ast.Raise)})}
            for name, node in changed.items()}


class FunctionReturn(Exception):
    def __init__(self, value):
        self.value = value


class FunctionRaise(Exception):
    def __init__(self, name):
        self.name = name


def evaluate(node: ast.FunctionDef, arguments: list[Any]) -> tuple[Any, str]:
    """Interpret a closed arithmetic AST; no eval, exec, imports or arbitrary calls."""
    parameters = [arg.arg for arg in [*node.args.posonlyargs, *node.args.args]]
    defaults = [scalar(ast.literal_eval(value)) for value in node.args.defaults]
    if not isinstance(arguments, list) or not len(parameters) - len(defaults) <= len(arguments) <= len(parameters):
        raise ValueError("Probe arguments do not match the function signature")
    if any(type(value) not in {int, float} for value in arguments):
        raise ValueError("Probe inputs must be numeric, not booleans or null")
    values = [scalar(value) for value in arguments]
    missing = len(parameters) - len(values)
    values += defaults[-missing:] if missing else []
    scope = dict(zip(parameters, values, strict=True))
    steps = 0

    def expression(item):
        nonlocal steps
        steps += 1
        if steps > 512:
            raise ValueError("Probe step limit exceeded")
        if isinstance(item, ast.Constant):
            return scalar(item.value)
        if isinstance(item, ast.Name) and item.id in scope:
            return scope[item.id]
        if isinstance(item, ast.BinOp):
            return scalar(ARITHMETIC[type(item.op)](expression(item.left), expression(item.right)))
        if isinstance(item, ast.UnaryOp):
            return scalar({ast.Not: operator.not_, ast.UAdd: operator.pos, ast.USub: operator.neg}[type(item.op)](
                expression(item.operand)))
        if isinstance(item, ast.IfExp):
            return expression(item.body if expression(item.test) else item.orelse)
        if isinstance(item, ast.BoolOp):
            value = expression(item.values[0])
            for other in item.values[1:]:
                if (isinstance(item.op, ast.And) and not value) or (isinstance(item.op, ast.Or) and value):
                    break
                value = expression(other)
            return value
        if isinstance(item, ast.Compare):
            left = expression(item.left)
            for op, right_node in zip(item.ops, item.comparators, strict=True):
                right = expression(right_node)
                if not COMPARISONS[type(op)](left, right):
                    return False
                left = right
            return True
        raise ValueError("Unsupported scalar expression")

    def statements(body):
        for item in body:
            if isinstance(item, ast.Return):
                raise FunctionReturn(expression(item.value) if item.value else None)
            if isinstance(item, ast.Raise):
                raise FunctionRaise(item.exc.func.id)
            if isinstance(item, ast.Assign):
                value = expression(item.value)
                for target in item.targets:
                    scope[target.id] = value
            elif isinstance(item, ast.If):
                statements(item.body if expression(item.test) else item.orelse)
            elif isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                continue
            elif not isinstance(item, ast.Assign):
                raise TypeError("Unsupported scalar statement")

    try:
        statements(node.body)
    except FunctionReturn as result:
        return result.value, ""
    except FunctionRaise as result:
        return None, result.name
    except (ZeroDivisionError, TypeError, OverflowError) as result:
        return None, type(result).__name__
    return None, ""


def check_numeric_cases(source: str, contract: dict, cases: list[dict]) -> int:
    nodes = functions(source)
    if not isinstance(cases, list) or not 2 <= len(cases) <= 12:
        raise ValueError("Two to twelve task-derived checks are required")
    seen, covered, normal, raised = set(), {}, set(), {}
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"function", "args", "expected", "raises"}:
            raise ValueError("Invalid bounded check schema")
        name, exception = case["function"], case["raises"]
        if (not isinstance(name, str) or name not in contract or not isinstance(exception, str)
                or exception not in EXCEPTIONS | {""} or exception and case["expected"] is not None):
            raise ValueError("Foreign function or unsupported exception")
        scalar(case["expected"])
        fingerprint = json.dumps([name, case["args"]], sort_keys=True, allow_nan=False)
        if fingerprint in seen:
            raise ValueError("Duplicate probe input is not additional coverage")
        seen.add(fingerprint)
        actual, error = evaluate(nodes[name], case["args"])
        if (error != exception or not error and (
            actual != case["expected"] or type(actual) is bool and type(case["expected"]) is not bool
            or type(case["expected"]) is bool and type(actual) is not bool
        )):
            raise ValueError("Task-derived numeric check failed")
        covered[name] = covered.get(name, 0) + 1
        if error:
            raised.setdefault(name, set()).add(error)
        else:
            normal.add(name)
    if (set(covered) != set(contract) or normal != set(contract) or any(n < 2 for n in covered.values())
            or any(not set(spec["raises"]).issubset(raised.get(name, set())) for name, spec in contract.items())):
        raise ValueError("Checks must cover every changed function, normal behavior and explicit exception branches")
    return len(cases)


def numeric_hashes_current(workspace: Path, hashes: dict[str, str]) -> bool:
    """Never read a foreign, symlinked or unbounded replacement for a probed file."""
    if not isinstance(hashes, dict) or not 1 <= len(hashes) <= 6:
        return False
    try:
        root = workspace.resolve()
        for path, digest in hashes.items():
            if not isinstance(path, str) or not isinstance(digest, str):
                return False
            target = Path(path)
            if (target.is_symlink() or not target.resolve().is_relative_to(root) or not target.is_file()
                    or target.suffix.casefold() != ".py" or target.stat().st_size > 24000
                    or hashlib.sha256(target.read_bytes()).hexdigest() != digest):
                return False
        return True
    except (OSError, ValueError):
        return False


async def verify_numeric_edit(runner, prompt: str, callback, *, explicit_tests: bool, decode):
    """One optional, hash-bound fallback when no existing test target is available."""
    broker = runner._tool_permission_broker
    semantic = (broker.task_intent or runner._task_intent_for_prompt(prompt)).semantic
    progress = getattr(broker, "action_progress", None)
    if (explicit_tests or broker.access_mode != "full" or broker.no_progress_recovery_exhausted
            or semantic.prohibited_actions.intersection({"tool_use", "read", "execute"})
            or semantic.scoped_prohibitions or progress is None
            or any(not item.get("success") and not item.get("wrapper_rejected")
                   and item.get("tool") in broker._MUTATION_TOOLS | broker._EXECUTION_TOOLS for item in broker.evidence)):
        return None
    runner._raise_if_cancelled()
    hashes = dict(broker.transaction_changed_hashes)
    original_turn, original_generation = broker.turn_id, broker.workspace_generation
    if not 1 <= len(hashes) <= 6:
        return None
    sources, contracts = {}, {}
    cases = None

    def unchanged():
        current_semantic = (broker.task_intent or runner._task_intent_for_prompt(prompt)).semantic
        return (broker.turn_id == original_turn and broker.workspace_generation == original_generation
                and broker.transaction_changed_hashes == hashes and broker.access_mode == "full"
                and not broker.no_progress_recovery_exhausted
                and not current_semantic.prohibited_actions.intersection({"tool_use", "read", "execute"})
                and not current_semantic.scoped_prohibitions
                and not any(not item.get("success") and not item.get("wrapper_rejected")
                            and item.get("tool") in broker._MUTATION_TOOLS | broker._EXECUTION_TOOLS
                            for item in broker.evidence)
                and numeric_hashes_current(runner.workspace, hashes))

    try:
        if not unchanged():
            return None
        for path in hashes:
            target = Path(path)
            if target.suffix.casefold() != ".py":
                return None
            current = decode(target.read_bytes(), str(target))[0]
            original = broker.transaction_originals.get(path, {}).get("data")
            if not isinstance(original, bytes):
                return None
            before = decode(original, str(target))[0]
            relative = target.relative_to(runner.workspace.resolve()).as_posix()
            sources[relative] = current
            contracts[relative] = numeric_contract(before, current)
        if not progress.reserve_recovery("verification"):
            return None
        if callback:
            callback("status", "No existing tests: checking one bounded, task-derived numeric behavior proposal.")
        schema = {"type": "object", "properties": {"checks": {"type": "array", "minItems": 2, "maxItems": 12,
                  "items": {"type": "object", "properties": {
                      "path": {"type": "string", "enum": list(sources)}, "function": {"type": "string"},
                      "args": {"type": "array", "items": {"type": "number"}, "maxItems": 6},
                      "expected": {"type": ["number", "boolean", "null"]},
                      "raises": {"type": "string", "enum": ["", *sorted(EXCEPTIONS)]}},
                      "required": ["path", "function", "args", "expected", "raises"], "additionalProperties": False}}},
                  "required": ["checks"], "additionalProperties": False}
        proposal = await asyncio.to_thread(
            runner._get_llm().qubitz_json_chat, model_name=runner.config.model_name,
            messages=[{"role": "system", "content": "Propose numeric input/output checks solely from the user's task, "
                       "not guessed implementation behavior. Include at least two distinct inputs and a normal result "
                       "for each changed function, plus each listed exception. Use raises='' for normal results, "
                       "expected=null for exceptions. No code, paths outside the list or actions. If the task does "
                       "not specify enough behavior, return an empty checks list; verification will remain partial."},
                      {"role": "user", "content": f"Task:\n{prompt}\nChanged function signatures:\n{json.dumps(contracts)}"}],
            schema_name="qubitz_numeric_behavior_checks", schema=schema,
            num_predict=min(int(runner.config.num_predict), 2048))
        runner._raise_if_cancelled()
        if not unchanged() or not isinstance(proposal, dict) or set(proposal) != {"checks"}:
            return None
        cases = proposal["checks"]
        if not isinstance(cases, list) or not 2 <= len(cases) <= 12:
            return None
        grouped = {path: [] for path in sources}
        for case in cases:
            if not isinstance(case, dict) or set(case) != {"path", "function", "args", "expected", "raises"}:
                raise ValueError("Invalid task-derived check schema")
            if not isinstance(case["path"], str) or case["path"] not in grouped:
                raise ValueError("Check target is not an exact committed file")
            grouped[case["path"]].append({k: v for k, v in case.items() if k != "path"})
        checked = sum(check_numeric_cases(sources[path], contracts[path], grouped[path]) for path in sources)
        runner._raise_if_cancelled()
        if not unchanged():
            return None
        cases = json.loads(json.dumps(cases, allow_nan=False))
        broker.evidence.append({"turn_id": broker.turn_id, "tool": "wrapper_bounded_numeric_probe", "success": True,
                                "categories": ["tests"], "workspace_generation": broker.workspace_generation,
                                "verification_kind": "bounded_ast_numeric_probe", "checked_cases": checked,
                                "file_hashes": hashes, "checks": cases, "case_source": "task-derived model proposal",
                                "module_executed": False, "full_suite": False})
        return True, (f"Saved edits passed {checked} bounded numeric behavioral checks by AST interpretation. "
                      "No module was executed; this is not a full test suite or universal behavior proof.")
    except (OSError, ValueError, SyntaxError, TypeError, RuntimeError) as exc:
        # Cancellation must retain the runner's own typed outcome, never become a test failure.
        runner._raise_if_cancelled()
        if cases is not None:
            broker.evidence.append({"turn_id": broker.turn_id, "tool": "wrapper_bounded_numeric_probe",
                                    "success": False, "categories": [], "workspace_generation": broker.workspace_generation,
                                    "verification_kind": "bounded_ast_numeric_probe", "file_hashes": hashes,
                                    "checks": cases, "reason": str(exc), "module_executed": False, "full_suite": False})
        if callback:
            callback("status", f"Bounded numeric verification unavailable: {type(exc).__name__}: {exc}")
        return None, "Saved edits were preserved; bounded numeric behavior could not be verified."
