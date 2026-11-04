"""Language-aware staged recovery; bounded edits, no inference or workspace publication."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys

import pytest

from evaluation import test_file_content_recovery as recovery
from runtime import read_variant_source
from runtime.paths import variant_path
from runtime.recovery_protocol import repair_staged_text, staged_repair_context, staged_repair_schema

variant = recovery.variant
broker = recovery.broker
runtime = recovery.runtime


def context_for(content):
    return staged_repair_context(content, ValueError("Invalid artifact"), ".html")


def syntax_failure(*, url="", line=None, column=None):
    failure = ValueError("Unexpected identifier")
    failure.html_browser_diagnostics = [{
        "kind": "pageerror", "name": "SyntaxError", "message": str(failure),
        "url": url, "line": line, "column": column,
    }]
    return failure


def separated_draft():
    return (
        '<html><script>const example = "def untouched() {}";\n// def documented() {}\n'
        'def first() { return 1; }\n' + '// padding\n' * 210
        + 'def second() { return 2; }\n' + '// padding\n' * 210
        + 'def third() { return 3; }\n</script>'
        '<script type="application/json">{"example":"def data() {}"}</script></html>'
    )


def declaration_patches():
    return {"patches": [{"old_text": f"def {name}()", "new_text": f"function {name}()"}
                        for name in ("first", "second", "third")]}


def test_separated_patches_preserve_strings_comments_and_data():
    content = separated_draft()
    patches = declaration_patches()
    assert content.index("def third") - content.index("def first") > 4096
    result = repair_staged_text(content, patches, context_for(content))
    expected = content
    for item in patches["patches"]:
        expected = expected.replace(item["old_text"], item["new_text"])
    assert result == expected
    assert '"def untouched() {}"' in result
    assert "// def documented() {}" in result
    assert '"def data() {}"' in result


def test_repeated_text_uses_original_offsets_despite_length_changes():
    content = "bad bad bad"
    patch = {"patches": [
        {"old_text": "bad", "new_text": "a", "old_offset": 8},
        {"old_text": "bad", "new_text": "much longer", "old_offset": 0},
    ]}
    assert repair_staged_text(content, patch, context_for(content)) == "much longer bad a"
    patch["patches"][0].pop("old_offset")
    with pytest.raises(ValueError, match="not unique"):
        repair_staged_text(content, patch, context_for(content))


def test_overlapping_occurrences_still_require_an_exact_offset():
    patch = {"old_text": "aa", "new_text": "x"}
    with pytest.raises(ValueError, match="not unique"):
        repair_staged_text("aaa", {"patches": [patch]}, context_for("aaa"))
    assert repair_staged_text("aaa", {"patches": [{**patch, "old_offset": 1}]}, context_for("aaa")) == "ax"


@pytest.mark.parametrize("patch", [
    {"patches": []}, {"patches": {}}, {"patches": [None]},
    {"patches": [{"old_text": "abc", "new_text": "x"}] * 9},
    {"patches": [{"old_text": "abc", "new_text": "x"}], "path": "other.html"},
    {"patches": [{"old_text": "abc", "new_text": "abc"}]},
    {"patches": [{"old_text": "abc", "new_text": ""}]},
    {"patches": [{"old_text": "abc", "new_text": "x", "old_offset": True}]},
])
def test_patchset_rejects_invalid_envelopes_and_items(patch):
    with pytest.raises(ValueError):
        repair_staged_text("abc", patch, context_for("abc"))


@pytest.mark.parametrize("second", ["bcd", "abc"])
def test_patchset_rejects_overlapping_or_duplicate_ranges(second):
    patch = {"patches": [{"old_text": "abc", "new_text": "x"},
                         {"old_text": second, "new_text": "y"}]}
    with pytest.raises(ValueError, match="overlap"):
        repair_staged_text("abcdef", patch, context_for("abcdef"))


@pytest.mark.parametrize("side", ["old", "new"])
def test_aggregate_limits_do_not_multiply_with_patch_count(side):
    content = "a" * 2048 + "b" * 2048
    patch = {"patches": [{"old_text": "a" * 2048, "new_text": "c" * 2048},
                         {"old_text": "b" * 2048, "new_text": "d" * 2048}]}
    assert repair_staged_text(content, patch, context_for(content)) == "c" * 2048 + "d" * 2048
    if side == "old":
        content += "b"
        patch["patches"][1]["old_text"] += "b"
    else:
        patch["patches"][1]["new_text"] += "d"
    with pytest.raises(ValueError, match="aggregate"):
        repair_staged_text(content, patch, context_for(content))


def test_hash_and_all_visible_ranges_are_checked_before_applying_any_edit():
    content = "a" * 6000 + "hidden" + "b" * 6000
    context = context_for(content)
    assert context["draft_was_truncated"]
    patches = {"patches": [{"old_text": "aaaa", "new_text": "x", "old_offset": 0},
                           {"old_text": "hidden", "new_text": "y"}]}
    with pytest.raises(ValueError, match="outside"):
        repair_staged_text(content, patches, context)
    with pytest.raises(ValueError, match="hash changed"):
        repair_staged_text(content + "!", declaration_patches(), context)
    assert context["draft_sha256"] == hashlib.sha256(content.encode()).hexdigest()


def test_stage_capacity_is_unchanged():
    content = "head" + "x" * 159996
    with pytest.raises(ValueError, match="staging capacity"):
        repair_staged_text(content, {"patches": [{"old_text": "head", "new_text": "longer"}]},
                           context_for(content))
    schema = staged_repair_schema(len(content))
    items = schema["properties"]["patches"]
    assert items["maxItems"] == 8
    assert items["items"]["properties"]["new_text"]["maxLength"] == 4096


def test_declared_script_languages_and_missing_locations_remain_honest():
    content = ('<html><script type="importmap">{}</script>'
               '<script type="module">const value = :;</script>'
               '<script type="text/custom">not JavaScript</script></html>')
    context = staged_repair_context(content, syntax_failure(), ".html")
    assert context["error_class"] == "SyntaxError" and context["validation_phase"] == "syntax"
    assert context["reported_location"] == {"url": "", "line": None, "column": None}
    assert context["located_script"] is None
    assert [item["language"] for item in context["script_regions"]] == ["JSON", "JavaScript", "data"]
    assert context["script_regions"][1]["script_type"] == "module"


def test_supplied_inline_location_maps_to_exact_script_not_an_external_caller():
    content = '<html>\n<script type="module">const value = :;</script>\n</html>'
    column = content.splitlines()[1].index(":") + 1
    context = staged_repair_context(content, syntax_failure(url="about:blank", line=2, column=column), ".htm")
    source = context["located_script"]
    assert context["expected_language"] == "JavaScript"
    assert content[source["start_offset"]:source["end_offset"]] == "const value = :;"
    context = staged_repair_context(content, syntax_failure(url="https://example.test/lib.js", line=2,
                                                          column=column), ".html")
    assert context["located_script"] is None


def test_python_diagnostic_keeps_language_and_original_coordinates():
    content = "def f():\n  return :\n"
    failure = SyntaxError("invalid syntax", ("draft.py", 2, 10, "  return :\n"))
    context = staged_repair_context(content, failure, ".py")
    assert context["expected_language"] == "Python"
    assert context["validation_phase"] == "syntax"
    assert context["reported_location"]["line"] == 2
    assert context["reported_location"]["column"] == 10


def test_non_ascii_browser_columns_are_preserved_without_guessing_script_offsets():
    content = '<html>\U0001f600<script>const value = :;</script></html>'
    column = content.index(":") + 2
    context = staged_repair_context(content, syntax_failure(url="about:blank", line=1, column=column), ".html")
    assert context["reported_location"]["column"] == column
    assert context["located_script"] is None


def test_script_source_metadata_is_bounded_without_losing_truncation_status():
    content = '<script src="https://example.test/' + 'x' * 20000 + '"></script>'
    context = staged_repair_context(content, syntax_failure(), ".html")
    script = context["script_regions"][0]
    assert len(script["src"]) == 240 and script["src_was_truncated"]
    assert len(context["rejected_draft_excerpt"]) <= 12000 + len("\n[omitted]\n")


@pytest.mark.parametrize("complete_fix", [True, False])
def test_wrapper_one_repair_revalidates_the_entire_candidate(runtime, variant, broker, complete_fix):
    _, client, _ = recovery.wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    draft = "<html><script>const left = :; const right = :;</script></html>"
    patches = [{"old_text": "left = :", "new_text": "left = 1"}]
    if complete_fix:
        patches.append({"old_text": "right = :", "new_text": "right = 2"})
    validated = []

    def validate(content):
        validated.append(content)
        if "= :" in content:
            raise syntax_failure()
        return {"browser_load": "passed"}

    runtime[2]["_validate_html_artifact"] = validate
    replies = recovery.scoped_replies(
        recovery.raw(json.dumps({"path": "scene.html", "content": draft, "complete": True})),
        recovery.raw(json.dumps({"patches": patches})),
    )
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))
    response = client.qubitz_file_creation_chat(**recovery.request(tools=recovery.TOOLS))
    assert len(seen) == 4 and len(validated) == 2
    repair = seen[-1]
    assert repair["response_format"]["json_schema"]["schema"]["required"] == ["patches"]
    assert repair["max_tokens"] <= 4096
    assert '"error_class": "SyntaxError"' in str(repair["messages"])
    assert "not evidence of a missing dependency" in str(repair["messages"])
    if complete_fix:
        assert response["message"]["tool_calls"][0]["function"]["arguments"]["content"] == validated[-1]
    else:
        assert not response["message"].get("tool_calls")
        assert "nothing was written" in broker._file_creation_blocker
    assert not (broker.workspace / "scene.html").exists()


def test_real_browser_parser_rejects_original_and_accepts_only_revalidated_patchset():
    source = read_variant_source(variant_path("AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"))
    tree = ast.parse(source)
    probe_source = ast.literal_eval(next(node.value for node in tree.body if isinstance(node, ast.Assign)
                                        and any(isinstance(target, ast.Name)
                                                and target.id == "_HTML_BROWSER_PROBE_SOURCE"
                                                for target in node.targets)))

    def probe(content):
        result = subprocess.run([sys.executable, "-c", probe_source], input=json.dumps({
            "content": content, "online": False, "cdn_hosts": [],
        }), capture_output=True, text=True, timeout=20, check=False)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    draft = separated_draft()
    before = probe(draft)
    assert before["status"] == "failed", before
    assert any(item["name"] == "SyntaxError" for item in before["diagnostics"])
    failure = ValueError("; ".join(before["errors"]))
    failure.html_browser_diagnostics = before["diagnostics"]
    context = staged_repair_context(draft, failure, ".html")
    repaired = repair_staged_text(draft, declaration_patches(), context)
    after = probe(repaired)
    assert after["status"] == "passed", after
    assert not after["diagnostics"] and not after["blocked_requests"]
