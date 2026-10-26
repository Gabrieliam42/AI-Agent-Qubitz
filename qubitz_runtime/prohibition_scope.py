"""Target classes for negative directives, without inventing blanket permissions."""
import re as _prohibition_re
import shlex as _prohibition_shlex
from pathlib import Path as _ProhibitionPath


def class_targets(text: str, actions: set[str]) -> tuple[str, ...]:
    target = _prohibition_re.sub(r"\s+(?:and|or)\s*$", "", text.strip().rstrip(".!?"), flags=_prohibition_re.IGNORECASE)
    if actions == {"execute"} and _prohibition_re.fullmatch(
        r"(?:(?:any|new|other|additional|the|another)\s+)*(?:models?|model\s+(?:tests?|runs?))", target, _prohibition_re.IGNORECASE,
    ):
        return ("@class:models",)
    if actions <= {"edit", "create", "delete", "move"} and _prohibition_re.fullmatch(
        r"(?:(?:any|the|existing)\s+)*(?:evaluator|evaluation)\s+(?:files?|code)", target, _prohibition_re.IGNORECASE,
    ):
        return ("@class:evaluator_files",)
    if actions <= {"edit", "create", "delete", "move"} and _prohibition_re.fullmatch(
        r"(?:(?:any|the|existing|supplied|provided|test)\s+)*fixtures?(?:\s+files?)?",
        target, _prohibition_re.IGNORECASE,
    ):
        return ("@class:fixture_files",)
    if actions <= {"edit", "create", "delete", "move"} and _prohibition_re.fullmatch(
        r"(?:(?:any|the|existing|current|unit|integration)\s+)*tests?(?:\s+(?:files?|code|suite|cases?|runner))?"
        r"(?:\s+(?:and|or)\s+(?:(?:the|existing|current)\s+)*tests?(?:\s+(?:files?|code|suite|cases?|runner))?)*",
        target, _prohibition_re.IGNORECASE,
    ):
        return ("@class:test_files",)
    return ()


def _constraint_path(path: _ProhibitionPath, workspace: _ProhibitionPath) -> _ProhibitionPath:
    workspace = workspace.resolve()
    path = (path if path.is_absolute() else workspace / path).resolve()
    return path.relative_to(workspace) if path.is_relative_to(workspace) else path


def evaluator_path(path: _ProhibitionPath, workspace: _ProhibitionPath) -> bool:
    path = _constraint_path(path, workspace)
    return any(part.casefold() in {"evaluation", "evaluator", "evaluators"}
               or _ProhibitionPath(part).stem.casefold() == "evaluator"
               or part.casefold().startswith("evaluator_") for part in path.parts)


def test_path(path: _ProhibitionPath, workspace: _ProhibitionPath) -> bool:
    path = _constraint_path(path, workspace)
    return (any(part.casefold() in {"test", "tests"} for part in path.parts[:-1])
            or path.name.casefold() in {"test", "tests", "test.py", "conftest.py", "pytest.ini", "run_tests.py", "test_runner.py"}
            or bool(_prohibition_re.fullmatch(r"test_.+\.[\w]+|.+_test\.[\w]+", path.name, _prohibition_re.IGNORECASE)))


def fixture_path(path: _ProhibitionPath, workspace: _ProhibitionPath) -> bool:
    path = _constraint_path(path, workspace)
    return any(_prohibition_re.search(r"(?:^|[_.-])fixtures?(?:$|[_.-])", part, _prohibition_re.IGNORECASE)
               for part in path.parts)


def inspection_command(command: str) -> bool:
    """A deliberately narrow executable allowlist; arbitrary Python is not inspection."""
    if not command or _prohibition_re.search(r"[;&|<>`$\r\n]", command):
        return False
    try:
        words = _prohibition_shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    name = _ProhibitionPath(words[0]).name.casefold()
    if name not in {"rg", "grep", "cat", "head", "tail", "wc", "ls", "pwd"}:
        return False
    # ripgrep preprocessors launch arbitrary executables, including model runners.
    return not any(word == "--pre" or word.startswith("--pre=") for word in words[1:])
