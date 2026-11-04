"""Version-two trajectories and independent provenance oracles; no product imports."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from followup_matrix_cases import (
    MCP_FILES,
    MCP_WRITES,
    Check,
    Scenario,
    Turn,
    fixture_path,
    prepare_scenario,
    scenarios,
    verify_turn,
    write_fixture,
)

SUITE = "expanded-v2"
SOURCE_URL = "https://pypi.org/pypi/packaging/25.0/json"
START = "<!-- selected:start -->\n"
END = "<!-- selected:end -->"
DESTINATION = "# Handbook\n\nKeep this introduction unchanged.\n\n" + START + END + "\n\nKeep this conclusion unchanged.\n"
SOURCE_DOCUMENT_PROMPT = (
    "Create source.md as an original prose document about safe file changes, with exactly "
    "three level-two sections named Overview, Transfer, and Closing in that order. Each "
    "section must contain one paragraph of 40 to 80 words, not code, lists, or placeholders."
)
TRANSFER_PROMPT = (
    "Insert only the complete paragraph under ## Transfer from the source.md you actually "
    "created into handbook.md between <!-- selected:start --> and <!-- selected:end -->. "
    "Copy it verbatim, followed by one newline. Preserve both markers and every byte "
    "outside them. Do not copy Overview or Closing or modify source.md."
)
ONLINE_INITIAL = (
    "# Package note\n\nPreserve this introduction.\n\n" + START + "{}\n" + END
    + "\n\nPreserve this footer.\n"
)
CUSTOM_CHECKS = {
    "document_sections", "fragment_transfer", "fragment_revision", "online_section",
    "mcp_file", "workspace_context", "no_source_fetch", "solar_browser",
}


@dataclass(frozen=True)
class ExpandedScenario(Scenario):
    secondary_files: dict[str, str] = field(default_factory=dict)
    source_url: str = ""


def catalog() -> tuple[Scenario, ...]:
    """Keep the original 21 definitions; only v2 adds initial GUI selection."""
    existing = tuple(replace(case, gui_only=True) for case in scenarios())
    additions = (
        ExpandedScenario("generated_fragment_revision_chain", "document_provenance", {
            "handbook.md": DESTINATION,
        }, (
            Turn(SOURCE_DOCUMENT_PROMPT, (Check("document_sections", "source.md"),), ("source.md",)),
            Turn(TRANSFER_PROMPT, (Check("fragment_transfer", "handbook.md", "source.md"),), ("handbook.md",)),
            Turn("Revise only the Transfer paragraph in source.md to explain independent checks "
                 "as well as safe changes. Include the exact phrase independent checks. Keep it "
                 "one paragraph of 40 to 80 words. Preserve all other sections and do not edit handbook.md yet.",
                 (Check("fragment_revision", "source.md", "independent checks"),), ("source.md",)),
            Turn("Refresh only the marked paragraph in handbook.md from the CURRENT Transfer "
                 "paragraph in source.md. Replace the old fragment verbatim with the new one, "
                 "followed by one newline; do not append a second copy. Preserve everything outside the markers.",
                 (Check("fragment_transfer", "handbook.md", "source.md"),), ("handbook.md",)),
        ), gui_only=True),
        ExpandedScenario("online_source_section_update", "online_provenance", {
            "package.md": ONLINE_INITIAL,
        }, (
            Turn(f"Fetch the authoritative package metadata from {SOURCE_URL}. In package.md "
                 "replace only the content between the selected markers with a JSON object "
                 "containing exactly name, version, and source_url. Take name and version "
                 "from the fetched info object; source_url must be the exact URL I supplied. "
                 "Follow the JSON with one newline. Preserve both markers and everything outside. "
                 "Do not install anything. If the source cannot be retrieved, leave the file unchanged and report the blocker.",
                 (Check("online_section", "package.md", ["name", "version"]),), ("package.md",)),
            Turn("Using only the source content verified in your previous turn, add requires_python "
                 "from its info object to that marked JSON object. Preserve the existing keys, "
                 "both markers, and everything outside; do not fetch again, invent a value, or install anything.",
                 (Check("online_section", "package.md", ["name", "version", "requires_python"]),
                  Check("no_source_fetch")), ("package.md",)),
            Turn("What version did the source you fetched report? Answer only the version. "
                 "Do not fetch again or modify anything.", (Check("exact_answer", expected="25.0"),
                                                           Check("no_tools"), Check("no_source_fetch"))),
        ), gui_only=True, source_url=SOURCE_URL),
        ExpandedScenario("mcp_result_to_file_chain", "mcp_provenance", MCP_FILES, (
            Turn("Discover fixture_mcp.py and call typed_echo with value=7 and label='kept-label'. "
                 "Write selected.json with exactly the returned value and label fields, ignoring "
                 "all other fields. Use the actual structured result, not a claimed result.",
                 (Check("mcp", expected=[{"tool": "typed_echo", "value": 7, "label": "kept-label"}]),
                  Check("mcp_file", "selected.json", "typed_echo")), (*MCP_WRITES, "selected.json")),
            Turn("Call that same typed_echo with value=11 and label='kept-label'. Update selected.json "
                 "from this NEW returned result, with exactly value and label; do not reuse the old value.",
                 (Check("mcp", expected=[{"tool": "typed_echo", "value": 11, "label": "kept-label"}]),
                  Check("mcp_file", "selected.json", "typed_echo")), (*MCP_WRITES, "selected.json")),
            Turn("Read selected.json and verify its value is 11 and its label is kept-label. "
                 "Do not invoke MCP again or change files. Answer only the value.",
                 (Check("json", "selected.json", {"value": 11, "label": "kept-label"}),
                  Check("exact_answer", expected="11"), Check("mcp_count", expected={"typed_echo": 0}))),
        ), gui_only=True),
        ExpandedScenario("workspace_a_b_a_file_context", "workspace_isolation", {
            "shared.txt": "workspace=A\nstate=original\n",
            "related.txt": "workspace=A-related\nstate=original\n",
            "facts.md": "# Project defaults\n\nThe project's default region is amber.\n",
        }, (
            Turn("Read shared.txt, report its workspace marker, and remember this file for my next instruction.",
                 (Check("answer", expected=r"\bA\b"),)),
            Turn("In qfile replace state=original with state=revised-a. Keep the workspace line unchanged.",
                 (Check("text", "shared.txt", "workspace=A\nstate=revised-a\n"),), ("shared.txt",)),
            Turn("What file does qfile refer to after this workspace change? If none is verified "
                 "here, ask me to identify it. Do not inherit a target or edit anything.",
                 (Check("answer", expected=r"\?|no .*file|no .*target|specify|which|identify"),),
                 workspace="b", allow_partial=True),
            Turn("Read shared.txt and related.txt in this workspace and report both workspace markers. "
                 "Do not edit them.", (Check("answer", expected=r"B-related"),), workspace="b"),
            Turn("In qfiles replace state=original with state=revised-b in both files, "
                 "preserving each workspace line.",
                 (Check("text", "shared.txt", "workspace=B\nstate=revised-b\n"),
                  Check("text", "related.txt", "workspace=B-related\nstate=revised-b\n")),
                 ("shared.txt", "related.txt"), workspace="b"),
            Turn("From qspace project documentation, what is the project's default region? "
                 "Answer only the region; do not modify anything.",
                 (Check("exact_answer", expected="cobalt"), Check("workspace_context", expected="amber")), workspace="b"),
            Turn("What file does qfile refer to after switching back? If none is verified here, "
                 "ask me to identify it rather than inheriting the other workspace's target. Do not edit.",
                 (Check("answer", expected=r"\?|no .*file|no .*target|specify|which|identify"),), allow_partial=True),
            Turn("Read shared.txt and related.txt in this workspace and report their current state lines. "
                 "Do not edit them.", (Check("answer", expected=r"revised-a"), Check("answer", expected=r"original"))),
            Turn("In qfiles set each state line to state=final-a, preserving their workspace lines.",
                 (Check("text", "shared.txt", "workspace=A\nstate=final-a\n"),
                  Check("text", "related.txt", "workspace=A-related\nstate=final-a\n")), ("shared.txt", "related.txt")),
            Turn("From qspace project documentation, what is the project's default region now? "
                 "Answer only the region; do not modify anything.",
                 (Check("exact_answer", expected="amber"), Check("workspace_context", expected="cobalt"))),
        ), gui_only=True, secondary_files={
            "shared.txt": "workspace=B\nstate=original\n",
            "related.txt": "workspace=B-related\nstate=original\n",
            "facts.md": "# Project defaults\n\nThe project's default region is cobalt.\n",
        }),
        ExpandedScenario("workspace_a_b_a_mcp_bindings", "workspace_isolation", MCP_FILES, (
            Turn("Use this workspace's fixture_mcp.py to invoke echo with value='a-first' and label='workspace-a'.",
                 (Check("mcp", expected=[{"tool": "echo", "value": "a-first", "label": "workspace-a"}]),), MCP_WRITES),
            Turn("Discover the CURRENT workspace's fixture_mcp.py and invoke its echo tool "
                 "with value='b-only' and label='workspace-b'. Do not reuse a server from another workspace.",
                 (Check("mcp", expected=[{"tool": "echo", "value": "b-only", "label": "workspace-b"}]),),
                 MCP_WRITES, workspace="b"),
            Turn("Use the CURRENT workspace's fixture_mcp.py to invoke echo with value='a-returned' "
                 "and label='workspace-a'. Do not call the other workspace's server.",
                 (Check("mcp", expected=[{"tool": "echo", "value": "a-returned", "label": "workspace-a"}]),), MCP_WRITES),
            Turn("What value did the latest tool invocation return? Answer only that value; do not call a tool again.",
                 (Check("exact_answer", expected="a-returned"), Check("no_tools"))),
        ), gui_only=True, secondary_files=MCP_FILES),
    )
    return (*existing, *additions)


def prepare(case: Scenario, parent: Path) -> dict[str, Path]:
    roots = prepare_scenario(case, parent)
    if case.name == "solar_system_3d_followup":
        from followup_matrix_solar import prepare as prepare_solar

        prepare_solar(roots["a"])
    for name, content in getattr(case, "secondary_files", {}).items():
        if name == "protected.txt":
            raise ValueError("Secondary fixtures cannot replace the protected sentinel")
        if name == "fixture_mcp.py":
            content = Path(__file__).with_name("followup_mcp_fixture.py").read_text(encoding="utf-8")
        write_fixture(roots["b"], name, content)
    return roots


def preflight_source(url: str) -> dict[str, Any]:
    """Independent availability receipt, kept outside the agent's workspace/context."""
    if url != SOURCE_URL:
        raise ValueError("Online fixture must use the declared authoritative URL")
    receipt: dict[str, Any] = {"url": url, "time": time.time(), "status": "unavailable"}
    request = urllib.request.Request(url, headers={"User-Agent": "Qubitz-Evaluation/2", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(1000001)
            receipt.update(http_status=response.status, final_url=response.geturl())
        if len(body) > 1000000:
            receipt.update(status="unsupported", reason="source_exceeds_capture_bound")
        else:
            data = json.loads(body)
            if not isinstance(data.get("info"), dict) or any(
                not isinstance(data["info"].get(key), str) for key in ("name", "version", "requires_python")
            ):
                receipt.update(status="unsupported", reason="source_schema_changed")
            else:
                receipt.update(status="available", body=body.decode("utf-8"),
                               sha256=hashlib.sha256(body).hexdigest())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        receipt["error"] = repr(exc)
    except (ValueError, UnicodeError, TypeError, AttributeError) as exc:
        receipt.update(status="unsupported", error=repr(exc))
    return receipt


def install_capture(module: Any, base: Any, observer: Any) -> None:
    """Capture exposed preflight fetches/RAG without substituting their behavior."""
    original_fetch = getattr(module, "_fetch_named_source_for_grounding", None)
    if callable(original_fetch):
        def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
            row = {"turn": observer.turn, "capture_origin": "wrapper_source_grounding", "url": url,
                   "started": time.time()}
            try:
                result = original_fetch(url, *args, **kwargs)
                row["result"] = copy.deepcopy(result)
                return result
            except Exception as exc:
                row["exception"] = repr(exc)
                raise
            finally:
                row["finished"] = time.time()
                observer.emit("source_fetch", receipt=row)
                with observer.lock:
                    observer.source_fetches.append(row)
        module._fetch_named_source_for_grounding = fetch

    original_context = base.RepoRetriever.format_context

    def context(retriever: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_context(retriever, *args, **kwargs)
        row = {"turn": observer.turn, "workspace": str(retriever.workspace), "content": copy.deepcopy(result)}
        observer.emit("retrieval_context", receipt=row)
        with observer.lock:
            observer.retrieval_contexts.append(row)
        return result

    base.RepoRetriever.format_context = context


def snapshot_text(root: Path, turn: Turn) -> dict[str, str]:
    paths = {check.path for check in turn.checks if check.kind in CUSTOM_CHECKS and check.path}
    paths.update(check.expected for check in turn.checks if check.kind == "fragment_transfer")
    result = {}
    for name in paths:
        path = fixture_path(root, name)
        if path.is_file():
            result[name] = path.read_bytes().decode("utf-8")
    return result


def section(text: str, heading: str = "Transfer") -> tuple[str, str, str]:
    matches = list(re.finditer(r"(?m)^## " + re.escape(heading) + r"\r?\n", text))
    if len(matches) != 1:
        raise ValueError("Source heading must occur exactly once")
    start = matches[0].end()
    next_heading = re.search(r"(?m)^## ", text[start:])
    end = start + next_heading.start() if next_heading else len(text)
    raw = text[start:end]
    fragment = raw.strip()
    offset = start + len(raw) - len(raw.lstrip())
    return text[:offset], fragment, text[offset + len(fragment):]


def marked(text: str) -> tuple[str, str, str]:
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("Destination markers must occur exactly once")
    prefix, rest = text.split(START, 1)
    body, suffix = rest.split(END, 1)
    return prefix + START, body, END + suffix


def result_payload(row: dict[str, Any], tool: str = "") -> dict[str, Any] | None:
    if row.get("exception") or row.get("capture_origin") != "mcp_host":
        return None
    result = row.get("result")
    if not isinstance(result, dict) or result.get("is_error"):
        return None
    payload = result.get("structured_content")
    if not isinstance(payload, dict):
        return None
    if tool:
        if (row.get("name") != "call_project_mcp_tool" or
            row.get("arguments", {}).get("tool_name") != tool or payload.get("tool") != tool or
            type(payload.get("returncode")) is not int or payload["returncode"] != 0):
            return None
        nested = payload.get("result")
        if not isinstance(nested, dict) or nested.get("isError") or nested.get("is_error"):
            return None
        payload = nested.get("structuredContent", nested.get("structured_content"))
    return payload if isinstance(payload, dict) else None


def fetched_source(observed: dict[str, Any], url: str) -> dict[str, Any] | None:
    candidates = []
    for row in observed.get("source_fetches", []):
        if (row.get("capture_origin") == "wrapper_source_grounding" and not row.get("exception") and
            row.get("url") == url and row.get("turn") == observed.get("turn")):
            candidates.append(row.get("result"))
    for row in observed.get("tools", []):
        if row.get("name") == "fetch_url" and row.get("arguments", {}).get("url") == url and row.get("turn") == observed.get("turn"):
            candidates.append(result_payload(row))
    for data in reversed(candidates):
        if not isinstance(data, dict) or data.get("requested_url") != url or data.get("retrieved") is not True:
            continue
        status = data.get("http_status", data.get("status"))
        if type(status) is not int or not 200 <= status < 300 or data.get("truncated"):
            continue
        try:
            payload = json.loads(data["body"])
            if isinstance(payload.get("info"), dict):
                return {"url": url, "final_url": data.get("final_url", url), "info": payload["info"],
                        "body_sha256": hashlib.sha256(data["body"].encode("utf-8")).hexdigest(),
                        "turn": observed["turn"]}
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return None


def source_unavailable(observed: dict[str, Any]) -> bool:
    candidates = [row.get("result", {}) for row in observed.get("source_fetches", [])
                  if row.get("url") == SOURCE_URL and row.get("turn") == observed.get("turn")]
    candidates.extend(row.get("result", {}).get("structured_content", {}) for row in observed.get("tools", [])
                      if row.get("name") == "fetch_url" and row.get("arguments", {}).get("url") == SOURCE_URL
                      and row.get("turn") == observed.get("turn") and not row.get("exception"))
    return any(isinstance(data, dict) and data.get("retrieved") is False and data.get("status") in {
        "network_request_failed", "http_request_denied", "http_request_failed",
    } for data in candidates)


def only_source_blockers(issues: list[str], observed: dict[str, Any], before: dict[str, Any],
                         after: dict[str, Any]) -> bool:
    return (before == after and not observed.get("exception") and not observed.get("errors") and
            "[Completion status: verified]" not in observed.get("assistant", "") and
            all(issue.startswith("postcondition_failed:online_section:") or issue == "partial_completion_report"
                for issue in issues))


def verify(root: Path, turn: Turn, observed: dict[str, Any], before: dict[str, str],
           after: dict[str, str], texts: dict[str, str], receipts: dict[str, Any],
           *, behavior_baselines: dict[str, dict[str, str]] | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    ordinary = replace(turn, checks=tuple(check for check in turn.checks if check.kind not in CUSTOM_CHECKS))
    issues, evidence = verify_turn(root, ordinary, observed, before, after, behavior_baselines=behavior_baselines)
    for check in turn.checks:
        if check.kind not in CUSTOM_CHECKS:
            continue
        if check.kind == "solar_browser":
            from followup_matrix_solar import browser_check

            receipt = browser_check(root, observed, check.expected)
            evidence.append({"check": check.kind, "path": check.path, "browser_receipt": receipt})
            if receipt["status"] == "unavailable":
                issues.append("browser_infrastructure_unavailable")
            elif receipt["status"] != "passed":
                issues.append(f"postcondition_failed:{check.kind}:{check.path}")
            continue
        receipt: dict[str, Any] = {"check": check.kind, "path": check.path}
        try:
            content = fixture_path(root, check.path).read_bytes().decode("utf-8") if check.path else ""
            if check.kind == "document_sections":
                headings = [heading.rstrip("\r") for heading in re.findall(r"(?m)^## (.+)$", content)]
                bodies = [section(content, heading)[1] for heading in ("Overview", "Transfer", "Closing")]
                ok = headings == ["Overview", "Transfer", "Closing"] and all(
                    40 <= len(body.split()) <= 80 and not re.search(r"\n\s*\n|```|(?m:^[*-] )", body)
                    for body in bodies
                )
            elif check.kind == "fragment_transfer":
                source = fixture_path(root, check.expected).read_bytes().decode("utf-8")
                fragment = section(source)[1]
                prefix, _, suffix = marked(texts[check.path])
                ok = bool(fragment) and content == prefix + fragment + "\n" + suffix
                receipt.update(source=check.expected, fragment_sha256=hashlib.sha256(fragment.encode()).hexdigest())
            elif check.kind == "fragment_revision":
                old_prefix, old, old_suffix = section(texts[check.path])
                prefix, fragment, suffix = section(content)
                ok = (prefix == old_prefix and suffix == old_suffix and old != fragment and
                      check.expected in fragment and 40 <= len(fragment.split()) <= 80 and
                      not re.search(r"\n\s*\n|```|(?m:^[*-] )", fragment))
            elif check.kind == "online_section":
                source = fetched_source(observed, SOURCE_URL)
                if source:
                    receipts[SOURCE_URL] = source
                else:
                    source = receipts.get(SOURCE_URL)
                # A new update may reuse only a receipt captured in this trajectory.
                prefix, body, suffix = marked(content)
                old_prefix, _, old_suffix = marked(texts[check.path])
                expected = {key: source["info"][key] for key in check.expected} if source else None
                if expected is not None:
                    expected["source_url"] = SOURCE_URL
                ok = (expected is not None and json.loads(body) == expected and
                      body.endswith("\n") and not body.endswith("\n\n") and
                      prefix == old_prefix and suffix == old_suffix)
                receipt["source_receipt"] = source
            elif check.kind == "mcp_file":
                payloads = [payload for row in observed.get("tools", []) if row.get("turn") == observed.get("turn")
                            and (payload := result_payload(row, check.expected)) is not None]
                expected = {key: payloads[-1][key] for key in ("value", "label")} if payloads else None
                ok = expected is not None and json.loads(content) == expected
                receipt["captured_fields"] = expected
            elif check.kind == "workspace_context":
                contexts = observed.get("retrieval_contexts", [])
                ok = all(Path(row["workspace"]).resolve() == root.resolve() and
                         check.expected not in str(row["content"]) for row in contexts)
                receipt.update(retrieval_exercised=bool(contexts), captured_contexts=len(contexts))
            else:
                ok = not observed.get("source_fetches") and not any(
                    row.get("name") in {"fetch_url", "search_web"} for row in observed.get("tools", [])
                )
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            ok = False
            receipt["error"] = repr(exc)
        evidence.append(receipt)
        if not ok:
            issues.append(f"postcondition_failed:{check.kind}:{check.path}")
    return issues, evidence
