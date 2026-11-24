"""Bounded diagnostics and request-supplied checks for exact Python creations."""

from __future__ import annotations

import ast
import asyncio
import builtins
import hashlib
import json
import math
import re
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path
from types import ModuleType

_MAX_SOURCE = 128 * 1024
_SELF_TEST_FLAGS = frozenset({"--selftest", "--self-test"})
_STARTUP_MARKER = "QUBITZ_TK_CONSTRUCTION "
_BEHAVIOR_MARKER = "QUBITZ_REQUEST_BEHAVIOR_PASSED "
_CHECKS_OPEN = re.compile(r"^```qubitz-checks[ \t]*\r?$", re.MULTILINE)
_TK_BUTTON_CLASSES = frozenset({
    "Button", "Checkbutton", "Radiobutton", "Menubutton",
    "TButton", "TCheckbutton", "TRadiobutton", "TMenubutton",
})


_SAVED_CREATION_FAILURE: ContextVar[tuple[object, str, str] | None] = ContextVar(
    "qubitz_saved_creation_failure", default=None,
)


def saved_creation_failure_for(broker) -> str:
    failure = _SAVED_CREATION_FAILURE.get()
    return failure[2] if failure and failure[0] is broker and failure[1] == broker.turn_id else ""


async def repair_saved_creation(runner, prompt, callback, path: str, digest: str, detail: str) -> str | None:
    broker = runner._tool_permission_broker
    if broker.transaction_verification_failures or broker._test_correction_targets:
        return None
    broker.transaction_verification_failures = 1
    broker._test_correction_targets = {path: digest}
    token = _SAVED_CREATION_FAILURE.set((broker, broker.turn_id, detail))
    try:
        # Variant wrappers can keep bounding recovery through the established hook.
        return await runner._wrapper_transaction_proposal_recovery(prompt, "", callback)
    finally:
        _SAVED_CREATION_FAILURE.reset(token)
        broker._test_correction_targets.clear()


def _public_name(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z_0-9]{0,63}", value) is not None


def _json_value(value, depth=0) -> bool:
    if depth > 6:
        return False
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_json_value(item, depth + 1) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item, depth + 1) for key, item in value.items())
    return type(value) in {str, int, bool, type(None)}


def _request_cases(raw: str) -> list[dict]:
    if len(raw.encode("utf-8")) > 4096:
        raise ValueError("Check data exceeds 4 KiB")

    def unique_keys(pairs):
        data = dict(pairs)
        if len(data) != len(pairs):
            raise ValueError("Duplicate check field")
        return data

    def valid_call(call, name, required, optional):
        return (isinstance(call, dict) and required <= call.keys() <= required | optional
                and _public_name(call.get(name)) and type(call.get("args", [])) is list)

    cases = json.loads(raw, object_pairs_hook=unique_keys)
    if type(cases) is not list or not 1 <= len(cases) <= 8 or not _json_value(cases):
        raise ValueError("Expected 1 to 8 cases with bounded JSON values")
    total_calls = 0
    for case in cases:
        if valid_call(case, "function", {"function", "expect"}, {"args"}):
            total_calls += 1
            continue
        if not valid_call(case, "class", {"class", "steps"}, {"args"}):
            raise ValueError("Each case must name one public function or class")
        steps = case["steps"]
        if (type(steps) is not list or not 1 <= len(steps) <= 8
                or not all(valid_call(step, "method", {"method"}, {"args", "expect"}) for step in steps)
                or "expect" not in steps[-1]):
            raise ValueError("Stateful cases need 1 to 8 method calls and a final expected result")
        total_calls += 1 + len(steps)
    if total_calls > 24:
        raise ValueError("Check data exceeds 24 calls")
    return cases


def request_behavior_checks(prompt: str) -> str:
    """Read an oracle only from an explicit block in the original user request."""
    from runtime.workflow import _PYTHON_CHECKS_FENCE

    openings = _CHECKS_OPEN.findall(prompt)
    if not openings:
        return ""
    blocks = _PYTHON_CHECKS_FENCE.findall(prompt)
    if len(openings) != 1 or len(blocks) != 1:
        raise ValueError("Provide one complete qubitz-checks block")
    return json.dumps(_request_cases(blocks[0]), separators=(",", ":"), ensure_ascii=False)


def _matches_expected(actual, expected) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _matches_expected(left, right) for left, right in zip(actual, expected, strict=True)
        )
    if type(expected) is dict:
        return (all(type(key) is str for key in actual) and actual.keys() == expected.keys()
                and all(_matches_expected(actual[key], value) for key, value in expected.items()))
    return actual == expected


def _run_request_behavior(target: Path, source: bytes, raw_cases: str) -> None:
    cases = _request_cases(raw_cases)
    definitions = {node.name: type(node) for node in ast.parse(source).body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    for case in cases:
        kind = "function" if "function" in case else "class"
        if definitions.get(case[kind]) is not (ast.FunctionDef if kind == "function" else ast.ClassDef):
            raise ValueError(f"Requested public {kind} is not defined in the saved file: {case[kind]}")

    # Import the saved implementation without its script-only entrypoint.
    module = _execute(target, source, [], as_main=False)
    checks = 0
    for index, case in enumerate(cases, 1):
        if "function" in case:
            name = case["function"]
            result = vars(module)[name](*case.get("args", []))
            if not _matches_expected(result, case["expect"]):
                raise ValueError(f"Case {index}: {name} returned an unexpected value or type")
            checks += 1
            continue
        instance = vars(module)[case["class"]](*case.get("args", []))
        for position, step in enumerate(case["steps"], 1):
            name = step["method"]
            result = getattr(instance, name)(*step.get("args", []))
            if "expect" not in step:
                continue
            if not _matches_expected(result, step["expect"]):
                raise ValueError(f"Case {index}, step {position}: {name} returned an unexpected value or type")
            checks += 1
    print(_BEHAVIOR_MARKER + str(checks))


def creation_source(workspace: Path, target: Path, digest: str) -> bytes:
    if target.suffix.casefold() != ".py" or not target.is_relative_to(workspace) or target.resolve() != target:
        raise ValueError("Verification requires an exact workspace Python file")
    if not target.is_file() or target.stat().st_size > _MAX_SOURCE:
        raise ValueError("Saved Python target is unavailable or exceeds the probe limit")
    source = target.read_bytes()
    if len(source) > _MAX_SOURCE or hashlib.sha256(source).hexdigest() != digest:
        raise ValueError("Saved Python revision changed before verification")
    return source


def probe_plan(source: bytes) -> list[tuple[str, str]]:
    tree = ast.parse(source)
    tk = any(
        isinstance(node, ast.ImportFrom) and node.module == "tkinter"
        or isinstance(node, ast.Import) and any(alias.name == "tkinter" for alias in node.names)
        for node in ast.walk(tree)
    )
    flags = {
        child.value for node in ast.walk(tree) if isinstance(node, ast.Compare)
        for child in (node.left, *node.comparators)
        if isinstance(child, ast.Constant) and isinstance(child.value, str) and child.value in _SELF_TEST_FLAGS
    }
    plan = [("tk_widget_construction", "")] if tk else []
    if len(flags) == 1:
        plan.append(("artifact_self_test", next(iter(flags))))
    return plan


def _execute(target: Path, source: bytes, arguments: list[str], *, as_main: bool = True) -> ModuleType:
    sys.argv = [str(target), *arguments]
    sys.path.insert(0, str(target.parent))
    module = ModuleType("__main__" if as_main else "_qubitz_saved_artifact")
    module.__file__ = str(target)
    sys.modules[module.__name__] = module
    exec(compile(source, str(target), "exec"), vars(module))  # noqa: S102
    return module


def _interactive_code_risk(source: bytes, plan: list[tuple[str, str]]) -> str:
    """Flag unrestricted dynamic Python evaluation in a Tk application before probing it."""
    if not any(kind == "tk_widget_construction" for kind, _ in plan):
        return ""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Call) or len(node.args) != 1 or node.keywords
                or isinstance(node.args[0], ast.Constant)):
            continue
        func = node.func
        name = (func.id if isinstance(func, ast.Name) else func.attr
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "builtins" else "")
        if name in {"eval", "exec"}:
            return (f"Line {node.lineno}: dynamic {name}() without an explicit namespace in a GUI script. "
                    "Use a dedicated parser or a small allowlist for user input, not Python execution.")
    return ""


def _check_tk_grid_buttons(root) -> None:
    pending = [root]
    while pending:
        parent = pending.pop()
        occupied = set()
        for widget in parent.grid_slaves():
            if widget.winfo_class() not in _TK_BUTTON_CLASSES:
                continue
            info = widget.grid_info()
            cell = (int(info["row"]), int(info["column"]))
            if cell in occupied:
                raise ValueError(f"Multiple Tk buttons share grid row {cell[0]}, column {cell[1]} in {parent}.")
            occupied.add(cell)
        pending.extend(child for child in parent.winfo_children() if child.winfo_manager())


def _tk_construction(target: Path, source: bytes) -> None:
    roots = []
    callback_errors = []
    widgets = []
    real_import = builtins.__import__
    tk = None

    def check(root, _threshold=0):
        root.withdraw()
        root.update_idletasks()
        _check_tk_grid_buttons(root)
        widgets.extend(root.winfo_children())

    def callback_error(_root, _type, value, _traceback):
        callback_errors.append(value)

    def intercept_import(name, *args, **kwargs):
        nonlocal tk
        module = real_import(name, *args, **kwargs)
        candidate = sys.modules.get("tkinter")
        if (tk is None and (name == "tkinter" or name.startswith("tkinter."))
                and candidate is not None and not getattr(candidate.__spec__, "_initializing", False)
                and hasattr(candidate, "Tk")):
            tk = candidate
            original_init = tk.Tk.__init__

            def root_init(root, *args, **kwargs):
                original_init(root, *args, **kwargs)
                roots.append(root)

            tk.Tk.__init__ = root_init
            tk.Misc.mainloop = check
            tk.mainloop = lambda n=0: check(tk._get_default_root(), n)
            tk.Tk.report_callback_exception = callback_error
        return module

    builtins.__import__ = intercept_import
    try:
        try:
            _execute(target, source, [])
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise
        if callback_errors:
            raise RuntimeError(f"Tk callback failed: {callback_errors[0]}")
        print(_STARTUP_MARKER + json.dumps({"widgets": len(widgets)}))
    finally:
        builtins.__import__ = real_import
        for root in reversed(roots):
            try:
                root.destroy()
            except tk.TclError:
                pass


def _wait_verified(receipt: dict, code: int) -> bool:
    start, end = receipt.get("started") or {}, receipt.get("exit") or {}
    return bool(start.get("token") and start.get("identity") and start["token"] == end.get("token")
                and start["identity"] == end.get("identity") and end.get("actual_wait") is True
                and end.get("tree_verified") is True and end.get("returncode") == code
                and receipt.get("supervisor_actual_wait") is True)


def verify_creation(workspace, target, digest, python, *, windows=False, cancel_event=None,
                    timeout=20, convert=str, requested_checks="", startup_only=False) -> list[dict]:
    from runtime.command_receipts import run_owned_command

    source = creation_source(workspace, target, digest)
    plan = probe_plan(source)
    risk = _interactive_code_risk(source, plan)
    if risk:
        return [{"kind": "python_interactive_static", "path": str(target), "sha256": digest,
                 "behavior_verified": False, "status": "failed", "reason": risk}]
    if startup_only:
        plan = [probe for probe in plan if probe[0] == "tk_widget_construction"]
    if requested_checks:
        _request_cases(requested_checks)
        plan.insert(0, ("request_behavior", "--request-behavior"))
    results = []
    for kind, argument in plan:
        if creation_source(workspace, target, digest) != source:
            raise ValueError("Saved Python revision changed between probes")
        flag = "--tk-construction" if kind == "tk_widget_construction" else argument
        command = [str(python), "-IB", convert(Path(__file__).resolve()), flag, convert(target), digest]
        if kind == "request_behavior":
            command.append(requested_checks)
        result = {"kind": kind, "path": str(target), "sha256": digest,
                  "interpreter": str(python), "behavior_verified": False, "status": "unavailable"}
        try:
            code, stdout, stderr, receipt = run_owned_command(
                command, cwd=workspace, timeout_seconds=timeout, windows=windows, cancel_event=cancel_event,
            )
        except InterruptedError:
            raise
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result.update(reason=f"{type(exc).__name__}: {exc}"[:600],
                          receipt=getattr(exc, "execution_receipt", None))
            results.append(result)
            break
        output = stdout.decode("utf-8", errors="replace")
        errors = stderr.decode("utf-8", errors="replace")
        result.update(receipt=receipt, returncode=code)
        if not _wait_verified(receipt, code):
            result["reason"] = "Owned process exit and cleanup could not be verified"
        elif code != 0:
            detail = (errors.strip() or output.strip() or f"Exit code {code}")[-2500:]
            infrastructure = kind == "tk_widget_construction" and any(marker in detail for marker in (
                "no display name", "couldn't connect to display", "No module named 'tkinter'",
                "No module named '_tkinter'", "Can't find a usable init.tcl",
            ))
            result.update(status="unavailable" if infrastructure else "failed", reason=detail)
        elif kind == "tk_widget_construction" and not any(
            line.startswith(_STARTUP_MARKER) and line != _STARTUP_MARKER + '{"widgets": 0}'
            for line in output.splitlines()
        ):
            result["reason"] = "Entrypoint did not construct a Tk widget tree through a supported mainloop"
        elif kind == "request_behavior":
            expected = sum(1 if "function" in case else sum("expect" in step for step in case["steps"])
                           for case in _request_cases(requested_checks))
            if output.splitlines().count(_BEHAVIOR_MARKER + str(expected)) == 1:
                result.update(status="passed", behavior_verified=True, assertions=expected)
            else:
                result["reason"] = "The request-supplied assertions did not report completion"
        else:
            result["status"] = "passed"
        results.append(result)
        if not _wait_verified(receipt, code):
            break
    creation_source(workspace, target, digest)
    return results


async def verify_committed_tk_edit(runner, prompt, callback, *, select_python, convert) -> str | None:
    """Check a hash-bound Tk repair without substituting tests or replaying edits."""
    broker = runner._tool_permission_broker
    changed = broker.transaction_changed_hashes
    startup_failure = broker.follow_up_outcome_feedback or re.search(
        r"(?m)^[ \t]*(?:Traceback \(most recent call last\):|Python exited with code [1-9][0-9]*\b)",
        prompt,
    )
    if (not broker.follow_up_repair_prompt or not startup_failure or len(changed) != 1
            or set(changed) != set(broker.transaction_explicit_file_targets)):
        return None
    raw, digest = next(iter(changed.items()))
    if Path(raw).suffix.casefold() != ".py":
        return None
    workspace, target = broker.workspace.resolve(), Path(raw)
    turn, generation = broker.turn_id, broker.workspace_generation
    if not any(
        item.get("tool") in {"apply_text_patch", "apply_text_patches"}
        and item.get("success") and "changed_files" in item.get("categories", ())
        and item.get("workspace_generation") == generation
        and item.get("file_hashes") == {raw: digest}
        for item in broker._current_turn_evidence()
    ):
        return "Startup verification unavailable: no matching current-turn patch receipt."
    key = (turn, generation, raw, digest)

    def allowed():
        semantic = broker.task_intent.semantic
        return (broker.turn_id == turn and broker.workspace_generation == generation
                and broker.access_mode == "full" and not runner._cancel_event.is_set()
                and not broker.no_progress_recovery_exhausted
                and not semantic.prohibited_actions.intersection({"read", "execute", "tool_use"})
                and not semantic.scoped_prohibitions)

    if not allowed():
        return "Startup verification unavailable: execution is not authorized for this turn."
    try:
        source = creation_source(workspace, target, digest)
        if ("tk_widget_construction", "") not in probe_plan(source):
            return None
    except (OSError, ValueError, SyntaxError, RecursionError) as exc:
        return f"Startup verification unavailable: {exc}."
    previous = getattr(broker, "_committed_tk_diagnostic", None)
    if previous and previous[0] == key:
        return previous[1]

    broker._committed_tk_diagnostic = (key, "Startup verification did not finish; startup and behavior are unverified.")
    if callback is not None:
        callback("status", "Checking Tk startup on the exact edited revision; no project suite will run.")
    try:
        chosen = await asyncio.wait_for(asyncio.to_thread(select_python), 15)
        if not allowed() or chosen is None:
            return "Startup verification unavailable: no compatible authorized Python was verified."
        python, _runner_kind = chosen
        native = python.suffix.casefold() == ".exe"
        checks = await asyncio.to_thread(
            verify_creation, workspace, target, digest, python, windows=native, startup_only=True,
            cancel_event=runner._cancel_event, convert=convert if native else str,
        )
    except InterruptedError:
        runner._raise_if_cancelled()
        raise
    except (OSError, ValueError, RuntimeError, SyntaxError, RecursionError) as exc:
        checks = [{"kind": "verification", "status": "unavailable", "reason": str(exc)[:500]}]
    if not allowed():
        runner._raise_if_cancelled()
        return "Startup verification unavailable: the verification authority changed."
    broker.evidence.append({"tool": "wrapper_edited_python_startup", "turn_id": turn,
                            "workspace_generation": generation, "categories": [],
                            "success": bool(checks) and all(check["status"] == "passed" for check in checks),
                            "verification_checks": checks})
    if checks and all(check["status"] == "passed" for check in checks):
        summary = "Tk widget construction passed for the saved edit. Interactive behavior was not verified."
    else:
        detail = "; ".join(f"{check['status']}: {check.get('reason', '')[-500:]}" for check in checks)
        summary = "Tk startup not verified: " + (detail or "no startup check completed") + "."
    broker._committed_tk_diagnostic = (key, summary)
    return summary


async def verify_saved_python_turn(runner, prompt, callback, *, select_python, convert, repair) -> str:
    """Run once after the save handoff and optionally recheck one settled correction."""
    from runtime.workflow import current_creations, saved_code_targets

    broker = runner._tool_permission_broker
    targets = saved_code_targets(broker, *broker.completion_report(prompt))
    if len(targets) != 1 or Path(targets[0]).suffix.casefold() != ".py":
        return ""
    workspace = broker.workspace.resolve()
    target = workspace / targets[0]
    saved = current_creations(broker)
    initial = saved.get(str(target))
    if initial is None:
        return ""
    try:
        requested_checks = request_behavior_checks(prompt)
        required_probes = {kind for kind, _ in probe_plan(creation_source(workspace, target, initial))}
    except (OSError, ValueError, SyntaxError, RecursionError) as exc:
        return f"Saved-code verification unavailable: {exc}."
    if requested_checks:
        required_probes.add("request_behavior")
    if not required_probes:
        return ""
    turn, generation = broker.turn_id, broker.workspace_generation

    def permitted():
        intent = broker.task_intent.semantic
        return (broker.turn_id == turn and broker.workspace_generation == generation
                and broker.access_mode == "full" and not broker.no_progress_recovery_exhausted
                and not intent.prohibited_actions.intersection({"tool_use", "read", "execute"})
                and not intent.scoped_prohibitions and not runner._cancel_event.is_set())

    if not permitted():
        return "Saved-code checks were not run because execution is not authorized for this turn."
    if getattr(broker, "_saved_python_diagnostics_turn", None) == turn:
        return ""
    broker._saved_python_diagnostics_turn = turn
    chosen = await asyncio.to_thread(select_python)
    if chosen is None:
        return "Saved-code checks were not run because no compatible target Python was verified."
    python, _runner_kind = chosen

    async def inspect_saved(digest):
        runner._raise_if_cancelled()
        if not permitted():
            return [{"kind": "verification", "status": "unavailable", "reason": "Turn authority changed"}]
        if callback is not None:
            callback("status", "Checking the saved Python revision before completing the turn.")
        native = python.suffix.casefold() == ".exe"
        try:
            results = await asyncio.to_thread(
                verify_creation, workspace, target, digest, python, windows=native,
                cancel_event=runner._cancel_event, convert=convert if native else str,
                requested_checks=requested_checks,
            )
        except InterruptedError:
            runner._raise_if_cancelled()
            raise
        except (OSError, ValueError, SyntaxError, RecursionError) as exc:
            results = [{"kind": "verification", "status": "unavailable", "reason": str(exc)[:600]}]
        runner._raise_if_cancelled()
        if permitted():
            broker.evidence.append({"tool": "wrapper_saved_python_diagnostics", "turn_id": turn,
                                    "workspace_generation": generation, "categories": [],
                                    "success": bool(results) and all(item["status"] == "passed" for item in results),
                                    "verification_checks": results})
        return results

    digest = initial
    checks = await inspect_saved(digest)
    failures = [item for item in checks if item["status"] == "failed"]
    progress = getattr(broker, "action_progress", None)
    if (failures and all(item["status"] in {"passed", "failed"} for item in checks)
            and permitted() and progress is not None and progress.reserve_recovery("verification_correction")):
        feedback = "\n".join(f"{item['kind']}: {item['reason']}" for item in failures)[-4000:]
        checkpoint = len(broker.evidence)
        corrected = await repair(str(target), digest, feedback)
        new_digest = current_creations(broker).get(str(target))
        commits = [item for item in broker.evidence[checkpoint:]
                   if item.get("success") and "changed_files" in item.get("categories", ())]
        if (corrected and new_digest and new_digest != digest and broker.turn_id == turn
                and broker.workspace_generation == generation + 1 and len(commits) == 1
                and commits[0].get("tool") == "apply_text_patch"
                and commits[0].get("turn_id") == turn
                and commits[0].get("workspace_generation") == broker.workspace_generation
                and commits[0].get("file_hashes") == {str(target): new_digest}):
            generation = broker.workspace_generation
            digest = new_digest
            checks = await inspect_saved(digest)
            if not required_probes <= {item["kind"] for item in checks}:
                return "A scoped repair was saved, but the original probes could not all be repeated."
        elif new_digest != digest:
            return "The saved revision changed during repair; no verification was attributed to the new revision."

    if checks and all(item["status"] == "passed" for item in checks):
        kinds = ", ".join(item["kind"].replace("_", " ") for item in checks)
        passed_behavior = next((item for item in checks if item.get("behavior_verified")), None)
        if passed_behavior:
            return (f"Saved-code checks passed ({kinds}); {passed_behavior['assertions']} request-supplied "
                    "assertion(s) verified. Behavior outside those assertions was not verified.")
        return f"Saved-code diagnostics passed ({kinds}). They do not prove requested behavior."
    notices = [f"{item['kind'].replace('_', ' ')} {item['status']}: {item.get('reason', '')[-500:]}"
               for item in checks if item["status"] != "passed"]
    return "Saved-code diagnostics: " + ("; ".join(notices) or "no conclusive check ran") + "."


if __name__ == "__main__":
    flag, raw_target, sha256, *payload = sys.argv[1:]
    path = Path(raw_target)
    saved = creation_source(Path.cwd().resolve(), path, sha256)
    if flag == "--request-behavior" and len(payload) == 1:
        _run_request_behavior(path, saved, payload[0])
    elif flag == "--tk-construction" and not payload:
        _tk_construction(path, saved)
    elif flag in _SELF_TEST_FLAGS and not payload:
        _execute(path, saved, [flag])
    else:
        raise SystemExit(2)
