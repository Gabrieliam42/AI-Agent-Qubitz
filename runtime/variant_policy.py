"""Public discovery and validation scope; developer launchers remain maintainable."""
from collections.abc import Iterable as _VisibilityIterable

DEVELOPER_ONLY_VARIANTS = frozenset({
    "AI_Agent_Qubitz_GLM-4.6-WX-v3-357B-A32B-IQ3.py",
    "AI_Agent_Qubitz_GLM_4.7_Flash_WX-30B-A3B-Q4.py",
    "AI_Agent_Qubitz_Xorton.WX-Computing.LARGE.2026.3-Q4.py",
})
_DEVELOPER_ONLY_NAMES = frozenset(name.casefold() for name in DEVELOPER_ONLY_VARIANTS)


def user_variant_allowed(filename: str) -> bool:
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name not in _DEVELOPER_ONLY_NAMES


def require_user_variants_allowed(filenames: _VisibilityIterable[str]) -> None:
    rejected = [str(name) for name in filenames if not user_variant_allowed(name)]
    if rejected:
        raise ValueError("Developer-only variants excluded from automated validation by user policy: " + ", ".join(rejected))
