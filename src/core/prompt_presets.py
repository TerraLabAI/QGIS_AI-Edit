from typing import List, Optional

PRESET_CATEGORIES = [
    {
        "label": "\U0001f9f9 Cleanup",
        "presets": [
            {
                "label": "Remove clouds",
                "prompt": "Remove clouds and haze, reveal clear terrain beneath",
            },
            {
                "label": "Remove shadows",
                "prompt": "Remove shadows to reveal features beneath",
            },
            {
                "label": "Enhance clarity",
                "prompt": "Enhance image clarity, sharpen details and improve contrast",
            },
        ],
    },
    {
        "label": "\U0001f33f Nature",
        "presets": [
            {
                "label": "Add trees",
                "prompt": "Add trees and vegetation to bare areas",
            },
            {
                "label": "Season change",
                "prompt": "Transform to autumn with orange and red foliage",
            },
            {
                "label": "Show crops",
                "prompt": "Show crop fields at harvest time",
            },
            {
                "label": "Deforestation",
                "prompt": "Remove forest cover, show cleared land",
            },
        ],
    },
    {
        "label": "\U0001f3d9 Urban",
        "presets": [
            {
                "label": "Add buildings",
                "prompt": "Add new buildings to empty areas",
            },
            {
                "label": "Add solar panels",
                "prompt": "Add solar panels on rooftops",
            },
            {
                "label": "Add road",
                "prompt": "Add a road through this area",
            },
            {
                "label": "Green park",
                "prompt": "Transform this area into a park with trees and paths",
            },
        ],
    },
    {
        "label": "\U0001f30a Simulation",
        "presets": [
            {
                "label": "Flood",
                "prompt": "Simulate flooding with water covering low areas",
            },
            {
                "label": "Fire damage",
                "prompt": "Show wildfire damage with burned vegetation",
            },
            {
                "label": "Snow cover",
                "prompt": "Add realistic snow cover on terrain and rooftops",
            },
        ],
    },
]

# Flat list for backward compatibility
PRESETS = [p for cat in PRESET_CATEGORIES for p in cat["presets"]]


def get_preset_names() -> List[str]:
    return [p["label"] for p in PRESETS]


def get_preset_prompt(label: str) -> Optional[str]:
    for p in PRESETS:
        if p["label"] == label:
            return p["prompt"]
    return None
