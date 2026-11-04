"""Compatibility names for the shared public-variant validation policy."""
from __future__ import annotations

from runtime.variant_policy import (
    DEVELOPER_ONLY_VARIANTS as EXCLUDED_LIVE_VARIANTS,
)
from runtime.variant_policy import (
    require_user_variants_allowed as require_live_variants_allowed,
)
from runtime.variant_policy import (
    user_variant_allowed as live_variant_allowed,
)

__all__ = ["EXCLUDED_LIVE_VARIANTS", "live_variant_allowed", "require_live_variants_allowed"]
