










from __future__ import annotations


WORKFLOW = {
    "start_here": "get_status",
    "typical_order": [
        "get_status",
        "get_credits",
        "set_input_layer",
        "prepare_interactive",
        "get_interactive_state",
        "set_zone",
        "get_presets",
        "attach_reference",
        "markup",
        "set_resolution",
        "generate",
        "generation_status",
        "select_version",
        "vectorize",
        "finish_session",
    ],
    "costs_credits": ["generate"],
    "slow_poll_required": {
        "generate": "generation_status",
    },
    "needs_a_zone": [
        "generate",
        "markup",
        "attach_reference",
    ],
    "makes_a_network_call": [
        "get_credits",
        "get_account",
        "rename_session",
        "delete_session",
        "list_generations (only with refresh=True)",
        "add_favorite_prompt (mirrors the star, best effort)",
        "remove_favorite_prompt (mirrors the star, best effort)",
    ],
    "permanent": ["delete_session"],
    "free_and_local": ["vectorize"],
    "read_only": [
        "capabilities",
        "guide",
        "get_status",
        "get_input_layer",
        "get_interactive_state",
        "get_account",
        "get_credits",
        "get_presets",
        "get_preset",
        "get_preset_families",
        "get_preset_family",
        "get_prompt_guidance",
        "get_resolutions",
        "get_top_picks",
        "get_zone",
        "get_session",
        "get_generation",
        "generation_status",
        "list_favorite_prompts",
        "list_generations",
        "list_recent_prompts",
        "list_references",
        "list_sessions",
        "list_versions",
        "markup_status",
        "search_presets",
    ],
    "never_available_here": (
        "Signing in, entering an activation key and accepting terms are the "
        "user's own actions and have no method here. When get_status() reports "
        "the plugin is not ready, hand its action_required line to the user and "
        "wait."
    ),
    "one_at_a_time": (
        "AI Edit holds one zone and one run. A second generate() while one is "
        "running is refused, not queued. Never resubmit a run that looks slow: "
        "the first one is still going and a second one charges the user again."
    ),
}

GUIDE_MISSING_TEXT = "Sign in to load the full guide."


def guide_text() -> str | None:

    from .core.config_store import ConfigMissing, require_str

    try:
        return require_str("agent_guide.guide")
    except ConfigMissing:
        return None


def prompt_hints() -> list[str]:

    from .core.config_store import ConfigMissing, require_table

    try:
        hints = require_table("agent_guide.prompt_hints")
    except ConfigMissing:
        return []
    if not isinstance(hints, list):
        return []
    return [h.strip() for h in hints[:20] if isinstance(h, str) and h.strip()]


def reference_limit_note() -> str:


    from .core.config_store import ConfigMissing, require_str

    try:
        return require_str("agent_guide.reference_limit_note")
    except ConfigMissing:
        return "Not added. Call clear_references() first."
