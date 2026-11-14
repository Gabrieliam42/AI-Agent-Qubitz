"""Request-local reasoning controls verified from template structure, not model names."""
from __future__ import annotations

from typing import Any

REQUIRED_EFFORT_CONTRACT = "template_required_effort_low_high_max"


def template_reasoning_capabilities(template: Any) -> dict[str, Any]:
    """Fail closed unless the effort header and unconditional thinking prefill match."""
    if not isinstance(template, str) or len(template) > 262144:
        return {}
    from jinja2 import Environment, TemplateSyntaxError, nodes

    environment = Environment(extensions=["jinja2.ext.loopcontrols"])
    try:
        tree = environment.parse(template)
    except (TemplateSyntaxError, RecursionError):
        return {}
    expected = environment.parse(
        "{%- set effective_reasoning_effort = reasoning_effort if reasoning_effort is defined "
        "and reasoning_effort in ['low', 'high'] else 'max' -%}"
        "{%- if effective_reasoning_effort is not none -%}<|system|>Reasoning Effort: "
        "{{ effective_reasoning_effort | capitalize }}{%- endif -%}"
        "{%- set clear_thinking = clear_thinking if clear_thinking is defined else false -%}"
        "{%- if add_generation_prompt -%}<|assistant|>{{- '<think>' -}}{%- endif -%}"
    )
    body = list(tree.body)
    if body and isinstance(body[0], nodes.Output) and all(
        isinstance(node, nodes.TemplateData) and node.data.strip() == "[gMASK]<sop>"
        for node in body[0].nodes
    ):
        body.pop(0)
    if len(body) < 4 or body[:3] != expected.body[:3] or body[-1] != expected.body[-1]:
        return {}
    # A later assignment, scope or include must not silently replace the verified controls.
    protected = {"reasoning_effort", "effective_reasoning_effort", "clear_thinking", "add_generation_prompt"}
    for statement in body[3:-1]:
        descendants = [statement, *statement.find_all(nodes.Node)]
        if any(isinstance(node, (nodes.Extends, nodes.Include, nodes.Import, nodes.FromImport))
               or isinstance(node, nodes.Name) and node.ctx != "load" and node.name in protected
               or isinstance(node, nodes.Macro) and node.name in protected for node in descendants):
            return {}
    clear_guard = environment.parse(
        "{% if (not clear_thinking or loop.index0 > ns.last_user_index) "
        "and reasoning_content is defined %}{% endif %}"
    ).body[0].test
    clear_verified = any(node.test == clear_guard for node in tree.find_all(nodes.If))
    return {
        "reasoning_control": REQUIRED_EFFORT_CONTRACT,
        "reasoning_effort_values": ["low", "high", "max"],
        "reasoning_effort_location": "chat_template_kwargs",
        "reasoning_effort_default": "max",
        "supports_thinking_disable": False,
        "supports_clear_thinking": clear_verified,
    }


def apply_template_reasoning_controls(payload: dict, capabilities: Any) -> str:
    """Normalize supported explicit controls without mutating caller-owned options."""
    if not isinstance(capabilities, dict) or capabilities.get("reasoning_control") != REQUIRED_EFFORT_CONTRACT:
        return "unchanged_unverified_template"
    options = payload.get("chat_template_kwargs", {})
    if not isinstance(options, dict):
        raise TypeError("chat_template_kwargs must be an object")
    options = dict(options)
    if "enable_thinking" in options:
        if options["enable_thinking"] is not True:
            raise ValueError("This template requires thinking; enable_thinking=false is unsupported")
        options.pop("enable_thinking")
    top = payload.get("reasoning_effort")
    nested = options.get("reasoning_effort")
    if "reasoning_effort" in payload and (not isinstance(top, str) or top not in ("low", "high", "max")):
        raise ValueError("This template accepts reasoning_effort low, high or max only")
    if "reasoning_effort" in options and (not isinstance(nested, str) or nested not in ("low", "high", "max")):
        raise ValueError("This template accepts reasoning_effort low, high or max only")
    if top is not None and nested is not None and top != nested:
        raise ValueError("Conflicting top-level and template reasoning_effort values")
    if top is not None:
        options["reasoning_effort"] = top
    if capabilities.get("supports_clear_thinking") is True:
        if "clear_thinking" in options and type(options["clear_thinking"]) is not bool:
            raise ValueError("clear_thinking must be a boolean")
        options.setdefault("clear_thinking", True)
    payload.pop("reasoning_effort", None)
    if options or "chat_template_kwargs" in payload:
        payload["chat_template_kwargs"] = options
    return "template_required_thinking"


def diagnostic_reasoning_controls(capabilities: dict) -> dict:
    """Use low effort for a required-thinking diagnostic, never pretend it is off."""
    if capabilities.get("reasoning_control") == REQUIRED_EFFORT_CONTRACT:
        payload = {"reasoning_effort": "low"}
        apply_template_reasoning_controls(payload, capabilities)
        return payload["chat_template_kwargs"]
    if capabilities.get("structured_thinking_control") in {
        "closed_think_prefill", "closed_channel_prefill", "normalized_thinking_option_prefill",
    }:
        return {"enable_thinking": False}
    raise ValueError("Diagnostic thinking controls are unverified; inspect the rendered prompt before inference")
