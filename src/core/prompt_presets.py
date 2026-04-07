from typing import Dict, List, Optional

from .activation_manager import tr, _get_locale
from .logger import log, log_warning

# Local fallback presets: id-based, translated at runtime via tr().
_PRESET_SPECS = [
    {
        "key": "remove",
        "label_key": "remove",
        "presets": [
            {"id": "remove_clouds", "label_key": "remove_clouds",
             "prompt": "Remove clouds and haze, reveal clear terrain beneath"},
            {"id": "remove_shadows", "label_key": "remove_shadows",
             "prompt": "Remove shadows to reveal features beneath"},
            {"id": "remove_trees", "label_key": "remove_trees",
             "prompt": "Remove trees and vegetation, reveal bare ground beneath"},
            {"id": "remove_water", "label_key": "remove_water",
             "prompt": "Remove water bodies, reveal dry terrain beneath"},
            {"id": "remove_haze", "label_key": "remove_haze",
             "prompt": "Remove atmospheric haze and fog, restore clear visibility"},
        ],
    },
    {
        "key": "add",
        "label_key": "add",
        "presets": [
            {"id": "add_trees", "label_key": "add_trees",
             "prompt": "Add trees and vegetation to bare areas"},
            {"id": "add_buildings", "label_key": "add_buildings",
             "prompt": "Add new buildings to empty areas"},
            {"id": "add_solar_panels", "label_key": "add_solar_panels",
             "prompt": "Add solar panels on rooftops"},
            {"id": "add_road", "label_key": "add_road",
             "prompt": "Add a road through this area"},
            {"id": "add_park", "label_key": "add_park",
             "prompt": "Transform this area into a park with trees and paths"},
            {"id": "add_crops", "label_key": "add_crops",
             "prompt": "Show crop fields at harvest time"},
        ],
    },
]


def get_translated_categories() -> List[Dict]:
    """Return local categories with translated display labels."""
    return [
        {
            "key": cat["key"],
            "label": tr(cat["label_key"]),
            "presets": [
                {
                    "id": p["id"],
                    "label": tr(p["label_key"]),
                    "prompt": p["prompt"],
                }
                for p in cat["presets"]
            ],
        }
        for cat in _PRESET_SPECS
    ]


def parse_remote_presets(data: dict) -> Optional[List[Dict]]:
    """Parse remote presets response into the format expected by the UI.

    Returns translated categories or None if parsing fails.
    """
    try:
        categories = data.get("categories", [])
        if not categories:
            return None
        locale = _get_locale()
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


def fetch_remote_presets(client) -> Optional[List[Dict]]:
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
                "Remote presets: loaded {} categories, "
                "{} presets".format(len(result), total)
            )
        return result
    except Exception:
        log_warning("Remote presets: fetch failed, using local fallback")
        return None


def get_preset_prompt(preset_id: str) -> Optional[str]:
    """Get English prompt by preset ID."""
    for cat in _PRESET_SPECS:
        for p in cat["presets"]:
            if p["id"] == preset_id:
                return p["prompt"]
    return None


def get_preset_names() -> List[str]:
    """Return all preset label keys."""
    return [p["label_key"] for cat in _PRESET_SPECS for p in cat["presets"]]
