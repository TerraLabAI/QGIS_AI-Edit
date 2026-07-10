"""Event name constants, mirrored from the website analytics registry.

Single source of truth lives in the website repo; the ai-edit subset is
vendored here (and in analytics_events.json) the same way prompt presets are
mirrored. Never pass a raw string to telemetry.track(); use a constant below.
Run scripts/check_telemetry.py to verify this module and analytics_events.json
stay in sync.
"""
from __future__ import annotations

# Registry schema version this mirror was generated against.
REGISTRY_VERSION = 1

# Activation events
PLUGIN_OPENED = "plugin_opened"
ACTIVATION_SCREEN_VIEWED = "activation_screen_viewed"
LAUNCH_CLICKED = "launch_clicked"
BASEMAP_CTA_CLICKED = "basemap_cta_clicked"
ACTIVATION_ATTEMPTED = "activation_attempted"
PLUGIN_ACTIVATED = "plugin_activated"

# One-click connect (browser pairing handoff)
AI_EDIT_PAIR_STARTED = "ai_edit_pair_started"
AI_EDIT_PAIR_SUCCEEDED = "ai_edit_pair_succeeded"
AI_EDIT_PAIR_FAILED = "ai_edit_pair_failed"
AI_EDIT_PAIR_TIMEOUT = "ai_edit_pair_timeout"
AI_EDIT_PAIR_CANCELLED = "ai_edit_pair_cancelled"

# Generation events
ZONE_DRAWN = "zone_drawn"
TEMPLATE_SELECTED = "template_selected"
GENERATION_STARTED = "generation_started"
GENERATION_COMPLETED = "generation_completed"
GENERATION_FAILED = "generation_failed"
GENERATION_CANCELLED = "generation_cancelled"
FIRST_GENERATION_MILESTONE = "first_generation_milestone"

# Monetization
TRIAL_EXHAUSTED_VIEWED = "trial_exhausted_viewed"
SUBSCRIBE_LINK_CLICKED = "subscribe_link_clicked"

# Onboarding / guidance
# tutorial_source is the touchpoint id (footer_tutorial, post_signin, ...).
# NOTE: the per-event prop is tutorial_source, NOT source: `source` is a
# reserved session/universal dimension here (server-injected origin surface),
# so reusing it would collide with that enum. Mirrors AI Segmentation's
# tutorial_opened, adapted to this plugin's registry.
TUTORIAL_OPENED = "tutorial_opened"

# Tools
MARKUP_OPENED = "markup_opened"
VECTORIZE_PANEL_OPENED = "vectorize_panel_opened"
VECTORIZE_SUGGESTION_CLICKED = "vectorize_suggestion_clicked"
VECTORIZE_COMPLETED = "vectorize_completed"
SWIPE_ARMED = "swipe_armed"
SWIPE_DISARMED = "swipe_disarmed"

# Library
FAVORITE_TOGGLED = "favorite_toggled"
RECENT_SELECTED = "recent_selected"
# Cross-product history events (name-keyed globally; AI Segmentation registers
# these too). Restore = re-add/reuse a past generation; export = download it.
HISTORY_RESTORED = "history_restored"
HISTORY_EXPORTED = "history_exported"

# Refund visibility
GENERATION_REFUND_ATTEMPTED = "generation_refund_attempted"
GENERATION_REFUND_FAILED = "generation_refund_failed"

# Failure surfaces
EXPORT_FAILED = "export_failed"
PLUGIN_ERROR = "plugin_error"

# Every event this plugin may emit. The check script verifies this matches the
# vendored registry subset exactly.
ALL_EVENTS = frozenset({
    PLUGIN_OPENED,
    ACTIVATION_SCREEN_VIEWED,
    LAUNCH_CLICKED,
    BASEMAP_CTA_CLICKED,
    ACTIVATION_ATTEMPTED,
    PLUGIN_ACTIVATED,
    AI_EDIT_PAIR_STARTED,
    AI_EDIT_PAIR_SUCCEEDED,
    AI_EDIT_PAIR_FAILED,
    AI_EDIT_PAIR_TIMEOUT,
    AI_EDIT_PAIR_CANCELLED,
    ZONE_DRAWN,
    TEMPLATE_SELECTED,
    GENERATION_STARTED,
    GENERATION_COMPLETED,
    GENERATION_FAILED,
    GENERATION_CANCELLED,
    FIRST_GENERATION_MILESTONE,
    TRIAL_EXHAUSTED_VIEWED,
    SUBSCRIBE_LINK_CLICKED,
    TUTORIAL_OPENED,
    MARKUP_OPENED,
    VECTORIZE_PANEL_OPENED,
    VECTORIZE_SUGGESTION_CLICKED,
    VECTORIZE_COMPLETED,
    SWIPE_ARMED,
    SWIPE_DISARMED,
    FAVORITE_TOGGLED,
    RECENT_SELECTED,
    HISTORY_RESTORED,
    HISTORY_EXPORTED,
    GENERATION_REFUND_ATTEMPTED,
    GENERATION_REFUND_FAILED,
    EXPORT_FAILED,
    PLUGIN_ERROR,
})

# Required non-session properties per event (session/universal props such as
# product_id, source, plugin_version are added by the relay or session props).
# Mirrors the "required: true" fields of the vendored registry subset.
REQUIRED_PROPS: dict[str, tuple[str, ...]] = {
    PLUGIN_OPENED: (),
    ACTIVATION_SCREEN_VIEWED: (),
    LAUNCH_CLICKED: (),
    BASEMAP_CTA_CLICKED: ("success",),
    ACTIVATION_ATTEMPTED: ("success",),
    PLUGIN_ACTIVATED: (),
    AI_EDIT_PAIR_STARTED: (),
    AI_EDIT_PAIR_SUCCEEDED: (),
    AI_EDIT_PAIR_FAILED: (),
    AI_EDIT_PAIR_TIMEOUT: (),
    AI_EDIT_PAIR_CANCELLED: (),
    ZONE_DRAWN: (),
    TEMPLATE_SELECTED: ("template_id",),
    GENERATION_STARTED: ("used_template", "used_markup", "has_geo_context", "is_retry"),
    GENERATION_COMPLETED: ("is_retry", "used_markup", "used_template", "output_rescued"),
    GENERATION_FAILED: ("error_code", "is_retry"),
    GENERATION_CANCELLED: (),
    FIRST_GENERATION_MILESTONE: (),
    TRIAL_EXHAUSTED_VIEWED: ("is_free_tier",),
    SUBSCRIBE_LINK_CLICKED: (),
    TUTORIAL_OPENED: ("tutorial_source",),
    MARKUP_OPENED: (),
    VECTORIZE_PANEL_OPENED: (),
    VECTORIZE_SUGGESTION_CLICKED: (),
    VECTORIZE_COMPLETED: (),
    SWIPE_ARMED: (),
    SWIPE_DISARMED: (),
    FAVORITE_TOGGLED: ("now_favorited",),
    RECENT_SELECTED: (),
    HISTORY_RESTORED: (),
    HISTORY_EXPORTED: (),
    GENERATION_REFUND_ATTEMPTED: (),
    GENERATION_REFUND_FAILED: (),
    EXPORT_FAILED: ("stage", "error_code"),
    PLUGIN_ERROR: ("stage", "error_code"),
}
