







from __future__ import annotations


REGISTRY_VERSION = 38


PLUGIN_OPENED = "plugin_opened"
ACTIVATION_SCREEN_VIEWED = "activation_screen_viewed"
LAUNCH_CLICKED = "launch_clicked"


LAUNCH_BLOCKED = "launch_blocked"
BASEMAP_CTA_CLICKED = "basemap_cta_clicked"
ACTIVATION_ATTEMPTED = "activation_attempted"
PLUGIN_ACTIVATED = "plugin_activated"


AI_EDIT_PAIR_STARTED = "ai_edit_pair_started"
AI_EDIT_PAIR_SUCCEEDED = "ai_edit_pair_succeeded"
AI_EDIT_PAIR_FAILED = "ai_edit_pair_failed"
AI_EDIT_PAIR_TIMEOUT = "ai_edit_pair_timeout"
AI_EDIT_PAIR_CANCELLED = "ai_edit_pair_cancelled"


ZONE_DRAWN = "zone_drawn"


GENERATE_BLOCKED = "generate_blocked"
TEMPLATE_SELECTED = "template_selected"
GENERATION_STARTED = "generation_started"
GENERATION_COMPLETED = "generation_completed"
GENERATION_FAILED = "generation_failed"
GENERATION_CANCELLED = "generation_cancelled"
FIRST_GENERATION_MILESTONE = "first_generation_milestone"


TRIAL_EXHAUSTED_VIEWED = "trial_exhausted_viewed"
SUBSCRIBE_LINK_CLICKED = "subscribe_link_clicked"


PAYWALL_PREWALL_SHOWN = "paywall_prewall_shown"



PRO_UPSELL_VIEWED = "pro_upsell_viewed"



PLUGIN_UPDATE_PROMPT_SHOWN = "plugin_update_prompt_shown"
PLUGIN_UPDATE_PROMPT_CLICKED = "plugin_update_prompt_clicked"



PLUGIN_UPDATE_PROMPT_SUPPRESSED = "plugin_update_prompt_suppressed"







TUTORIAL_OPENED = "tutorial_opened"


GUIDANCE_TIP_SHOWN = "ai_edit_guidance_tip_shown"
GUIDANCE_TIP_DISMISSED = "ai_edit_guidance_tip_dismissed"


SEG_REDIRECT_CLICKED = "ai_edit_seg_redirect_clicked"



REFERENCE_ADDED = "ai_edit_reference_added"


MARKUP_OPENED = "markup_opened"


MARKUP_HIDDEN_WARNED = "ai_edit_markup_hidden_warned"
MARKUP_HIDDEN_RESOLVED = "ai_edit_markup_hidden_resolved"




RESULT_HIDDEN_WARNED = "ai_edit_result_hidden_warned"
RESULT_HIDDEN_RESOLVED = "ai_edit_result_hidden_resolved"
VECTORIZE_PANEL_OPENED = "vectorize_panel_opened"


VECTORIZE_HINT_SHOWN = "vectorize_hint_shown"
VECTORIZE_SUGGESTION_CLICKED = "vectorize_suggestion_clicked"
VECTORIZE_COMPLETED = "vectorize_completed"
SWIPE_ARMED = "swipe_armed"
SWIPE_DISARMED = "swipe_disarmed"


FAVORITE_TOGGLED = "favorite_toggled"
RECENT_SELECTED = "recent_selected"


HISTORY_RESTORED = "history_restored"
HISTORY_EXPORTED = "history_exported"




CONVERSATIONS_OPENED = "ai_edit_conversations_opened"
CONVERSATION_DELETED = "ai_edit_conversation_deleted"
CONVERSATION_RENAMED = "ai_edit_conversation_renamed"
RESULT_VALIDATED = "ai_edit_result_validated"


GENERATION_REFUND_ATTEMPTED = "generation_refund_attempted"
GENERATION_REFUND_FAILED = "generation_refund_failed"


EXPORT_FAILED = "export_failed"
PLUGIN_ERROR = "plugin_error"



ALL_EVENTS = frozenset({
    PLUGIN_OPENED,
    ACTIVATION_SCREEN_VIEWED,
    LAUNCH_CLICKED,
    LAUNCH_BLOCKED,
    BASEMAP_CTA_CLICKED,
    ACTIVATION_ATTEMPTED,
    PLUGIN_ACTIVATED,
    AI_EDIT_PAIR_STARTED,
    AI_EDIT_PAIR_SUCCEEDED,
    AI_EDIT_PAIR_FAILED,
    AI_EDIT_PAIR_TIMEOUT,
    AI_EDIT_PAIR_CANCELLED,
    ZONE_DRAWN,
    GENERATE_BLOCKED,
    TEMPLATE_SELECTED,
    GENERATION_STARTED,
    GENERATION_COMPLETED,
    GENERATION_FAILED,
    GENERATION_CANCELLED,
    FIRST_GENERATION_MILESTONE,
    TRIAL_EXHAUSTED_VIEWED,
    SUBSCRIBE_LINK_CLICKED,
    PAYWALL_PREWALL_SHOWN,
    PRO_UPSELL_VIEWED,
    PLUGIN_UPDATE_PROMPT_SHOWN,
    PLUGIN_UPDATE_PROMPT_CLICKED,
    PLUGIN_UPDATE_PROMPT_SUPPRESSED,
    TUTORIAL_OPENED,
    GUIDANCE_TIP_SHOWN,
    GUIDANCE_TIP_DISMISSED,
    SEG_REDIRECT_CLICKED,
    REFERENCE_ADDED,
    MARKUP_OPENED,
    MARKUP_HIDDEN_WARNED,
    MARKUP_HIDDEN_RESOLVED,
    RESULT_HIDDEN_WARNED,
    RESULT_HIDDEN_RESOLVED,
    VECTORIZE_PANEL_OPENED,
    VECTORIZE_HINT_SHOWN,
    VECTORIZE_SUGGESTION_CLICKED,
    VECTORIZE_COMPLETED,
    SWIPE_ARMED,
    SWIPE_DISARMED,
    FAVORITE_TOGGLED,
    RECENT_SELECTED,
    HISTORY_RESTORED,
    HISTORY_EXPORTED,
    CONVERSATIONS_OPENED,
    CONVERSATION_DELETED,
    CONVERSATION_RENAMED,
    RESULT_VALIDATED,
    GENERATION_REFUND_ATTEMPTED,
    GENERATION_REFUND_FAILED,
    EXPORT_FAILED,
    PLUGIN_ERROR,
})




REQUIRED_PROPS: dict[str, tuple[str, ...]] = {
    PLUGIN_OPENED: (),
    ACTIVATION_SCREEN_VIEWED: (),
    LAUNCH_CLICKED: (),
    LAUNCH_BLOCKED: ("reason",),
    BASEMAP_CTA_CLICKED: ("success",),
    ACTIVATION_ATTEMPTED: ("success",),
    PLUGIN_ACTIVATED: (),
    AI_EDIT_PAIR_STARTED: (),
    AI_EDIT_PAIR_SUCCEEDED: (),
    AI_EDIT_PAIR_FAILED: (),
    AI_EDIT_PAIR_TIMEOUT: (),
    AI_EDIT_PAIR_CANCELLED: (),
    ZONE_DRAWN: (),
    GENERATE_BLOCKED: ("reason",),
    TEMPLATE_SELECTED: ("template_id",),
    GENERATION_STARTED: ("used_template", "used_markup", "has_geo_context", "is_retry"),
    GENERATION_COMPLETED: ("is_retry", "used_markup", "used_template", "output_rescued"),
    GENERATION_FAILED: ("error_code", "is_retry"),
    GENERATION_CANCELLED: (),
    FIRST_GENERATION_MILESTONE: (),
    TRIAL_EXHAUSTED_VIEWED: ("is_free_tier",),
    SUBSCRIBE_LINK_CLICKED: (),
    PAYWALL_PREWALL_SHOWN: (),
    PRO_UPSELL_VIEWED: (),
    PLUGIN_UPDATE_PROMPT_SHOWN: ("offered_version", "trigger"),
    PLUGIN_UPDATE_PROMPT_CLICKED: ("offered_version", "action"),
    PLUGIN_UPDATE_PROMPT_SUPPRESSED: ("served_version", "reason"),
    TUTORIAL_OPENED: ("tutorial_source",),
    GUIDANCE_TIP_SHOWN: (),
    GUIDANCE_TIP_DISMISSED: (),
    REFERENCE_ADDED: ("source_kind",),
    SEG_REDIRECT_CLICKED: ("guidance_kind", "installed"),
    MARKUP_OPENED: (),
    MARKUP_HIDDEN_WARNED: (),
    MARKUP_HIDDEN_RESOLVED: ("choice",),
    RESULT_HIDDEN_WARNED: ("covering_count",),
    RESULT_HIDDEN_RESOLVED: ("choice",),
    VECTORIZE_PANEL_OPENED: (),
    VECTORIZE_HINT_SHOWN: (),
    VECTORIZE_SUGGESTION_CLICKED: (),
    VECTORIZE_COMPLETED: (),
    SWIPE_ARMED: (),
    SWIPE_DISARMED: (),
    FAVORITE_TOGGLED: ("now_favorited",),
    RECENT_SELECTED: (),
    HISTORY_RESTORED: (),
    HISTORY_EXPORTED: (),
    CONVERSATIONS_OPENED: (),
    CONVERSATION_DELETED: ("scope",),
    CONVERSATION_RENAMED: (),
    RESULT_VALIDATED: (),
    GENERATION_REFUND_ATTEMPTED: (),
    GENERATION_REFUND_FAILED: (),
    EXPORT_FAILED: ("stage", "error_code"),
    PLUGIN_ERROR: ("stage", "error_code"),
}
