from __future__ import annotations

from .i18n import get_locale, tr
from .logger import log, log_warning

# Local fallback presets: id-based, translated at runtime via tr().
_PRESET_SPECS = [
    {
        "key": "remove",
        "label": "Remove",
        "presets": [
            {"id": "remove_clouds", "label": "Remove clouds",
             "prompt": "Remove clouds and haze, reveal clear terrain beneath"},
            {"id": "remove_shadows", "label": "Remove shadows",
             "prompt": "Remove shadows to reveal features beneath"},
            {"id": "remove_trees", "label": "Remove trees",
             "prompt": "Remove trees and vegetation, reveal bare ground beneath"},
            {"id": "remove_water", "label": "Remove water",
             "prompt": "Remove water bodies, reveal dry terrain beneath"},
            {"id": "remove_haze", "label": "Remove haze",
             "prompt": "Remove atmospheric haze and fog, restore clear visibility"},
        ],
    },
    {
        "key": "add",
        "label": "Add",
        "presets": [
            {"id": "add_trees", "label": "Add trees",
             "prompt": "Add trees and vegetation to bare areas"},
            {"id": "add_buildings", "label": "Add buildings",
             "prompt": "Add new buildings to empty areas"},
            {"id": "add_solar_panels", "label": "Add solar panels",
             "prompt": "Add solar panels on rooftops"},
            {"id": "add_road", "label": "Add road",
             "prompt": "Add a road through this area"},
            {"id": "add_park", "label": "Add park",
             "prompt": "Transform this area into a park with trees and paths"},
            {"id": "add_crops", "label": "Add crops",
             "prompt": "Show crop fields at harvest time"},
        ],
    },
]


def get_translated_categories() -> list[dict]:
    """Return local categories with translated display labels."""
    return [
        {
            "key": cat["key"],
            "label": tr(cat["label"]),
            "presets": [
                {
                    "id": p["id"],
                    "label": tr(p["label"]),
                    "prompt": p["prompt"],
                }
                for p in cat["presets"]
            ],
        }
        for cat in _PRESET_SPECS
    ]


def parse_remote_presets(data: dict) -> list[dict] | None:
    """Parse remote presets response into the format expected by the UI.

    Returns translated categories or None if parsing fails.
    """
    try:
        categories = data.get("categories", [])
        if not categories:
            return None
        locale = get_locale()
        result = []
        for cat in categories:
            presets = []
            for p in cat.get("presets", []):
                label_map = p.get("label", {})
                label = label_map.get(locale, label_map.get("en", p.get("id", "")))
                presets.append({
                    "id": p["id"],
                    "label": label,
                    "prompt": p["prompt"],
                })
            result.append({
                "key": cat["key"],
                "label": cat["key"].capitalize(),
                "presets": presets,
            })
        return result
    except (KeyError, TypeError, AttributeError):
        return None


def fetch_remote_presets(client) -> list[dict] | None:
    """Fetch presets from the server. Returns parsed categories or None."""
    try:
        log("Remote presets: fetching from server...")
        data = client.get_presets()
        if "error" in data:
            log_warning("Remote presets: server returned error")
            return None
        result = parse_remote_presets(data)
        if result:
            total = sum(len(c["presets"]) for c in result)
            log(
                f"Remote presets: loaded {len(result)} categories, "
                f"{total} presets"
            )
        return result
    except Exception:
        log_warning("Remote presets: fetch failed, using local fallback")
        return None


def get_preset_prompt(preset_id: str) -> str | None:
    """Get English prompt by preset ID."""
    for cat in _PRESET_SPECS:
        for p in cat["presets"]:
            if p["id"] == preset_id:
                return p["prompt"]
    return None


def get_preset_names() -> list[str]:
    """Return all preset labels (English source text)."""
    return [p["label"] for cat in _PRESET_SPECS for p in cat["presets"]]
