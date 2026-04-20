from __future__ import annotations

from .i18n import tr

# ---------------------------------------------------------------------------
# Prompt preset categories. All managed locally in the plugin.
# Each category has a key, icon, color, and list of presets.
# Favorites (top picks) are assembled at runtime from other categories.
# ---------------------------------------------------------------------------

_CATEGORY_META = {
    "favorites": {"icon": "\u2605", "color": "#b89868"},   # muted warm gold
    "clean": {"icon": "\u232b", "color": "#b07878"},       # muted dusty rose
    "add": {"icon": "+", "color": "#68a868"},              # muted sage green
    "style": {"icon": "\u2726", "color": "#9880b0"},       # muted soft lavender
    "detect": {"icon": "\u25c9", "color": "#b08858"},      # muted warm amber
    "simulate": {"icon": "\u21bb", "color": "#a0a058"},    # muted olive
}

# Presets by category (key -> list of presets).
# Each preset: id, label (English source, translated at runtime), prompt.
_PRESETS_BY_CATEGORY = {
    "clean": [
        {
            "id": "remove_clouds",
            "label": "Remove clouds",
            "prompt": (
                "Remove all clouds and atmospheric haze from this aerial image. "
                "Reveal the clear terrain, roads, and structures beneath"
            ),
        },
        {
            "id": "remove_shadows",
            "label": "Remove shadows",
            "prompt": (
                "Remove all shadows cast by buildings and trees. "
                "Reveal the ground surface, roads, and features hidden beneath the shadows"
            ),
        },
        {
            "id": "remove_trees",
            "label": "Remove trees",
            "prompt": (
                "Remove all trees and vegetation canopy. "
                "Reveal the bare ground, terrain, and any structures hidden beneath"
            ),
        },
        {
            "id": "remove_buildings",
            "label": "Remove buildings",
            "prompt": (
                "Remove all existing buildings and structures. "
                "Show cleared, empty land as if the site was never built on"
            ),
        },
        {
            "id": "enhance_aerial",
            "label": "Enhance image resolution",
            "prompt": (
                "Upscale and enhance this image into a sharp, high-resolution "
                "aerial photograph with crisp details, clear textures, and "
                "improved contrast"
            ),
        },
    ],
    "add": [
        {
            "id": "add_trees",
            "label": "Add trees along road",
            "prompt": (
                "Add rows of mature trees along both sides of any visible roads. "
                "Match the existing vegetation style visible in the surroundings"
            ),
        },
        {
            "id": "add_buildings",
            "label": "Add residential buildings",
            "prompt": (
                "Add residential buildings in open areas. "
                "Match the surrounding architectural style and street layout if visible"
            ),
        },
        {
            "id": "add_solar_panels",
            "label": "Add solar panels",
            "prompt": (
                "Add blue photovoltaic solar panel arrays on any visible flat "
                "rooftops. Panels should face south with realistic reflections"
            ),
        },
        {
            "id": "add_road",
            "label": "Add road with sidewalks",
            "prompt": (
                "Add a paved road with sidewalks through the area. "
                "Match the existing road style if other roads are visible"
            ),
        },
        {
            "id": "add_park",
            "label": "Add public park",
            "prompt": (
                "Add a public park with walking paths, mature trees, "
                "open lawn areas, and benches in any open space visible"
            ),
        },
    ],
    "style": [
        {
            "id": "style_site_plan",
            "label": "Architectural site plan",
            "prompt": (
                "Transform into a clean architectural site plan. "
                "Represent each feature type visible with simple fills: "
                "buildings as white footprints with thin black outlines, "
                "roads as light gray, vegetation as light green. "
                "Top-down view, minimal detail, presentation-ready"
            ),
        },
        {
            "id": "style_ink_drawing",
            "label": "Ink drawing",
            "prompt": (
                "Turn the entire image into a black-and-white ink drawing. "
                "Fine line work for buildings and roads, stippling for vegetation, "
                "white background. Architectural illustration style"
            ),
        },
        {
            "id": "style_nighttime",
            "label": "Nighttime view",
            "prompt": (
                "Transform into a nighttime aerial view. Streetlights glowing "
                "warm yellow, building windows illuminated, roads visible under "
                "artificial lighting, dark sky"
            ),
        },
        {
            "id": "style_land_use",
            "label": "Land use map",
            "prompt": (
                "Transform into a simplified flat-color land use map. "
                "Use distinct colors for each land use type actually visible: "
                "yellow for residential, red for commercial, purple for industrial, "
                "dark green for forest, light green for parks, brown for agriculture, "
                "blue for water, gray for roads. "
                "Only color categories that are present in the image. "
                "Clean edges, no photographic texture"
            ),
        },
        {
            "id": "style_figure_ground",
            "label": "Figure-ground diagram",
            "prompt": (
                "Generate a figure-ground diagram. All buildings as solid black "
                "shapes with clean edges. Everything else (roads, vegetation, "
                "water, empty land) in white. No texture, no labels, no gray. "
                "Pure black and white only"
            ),
        },
        {
            "id": "style_thematic_map",
            "label": "Thematic urban map",
            "prompt": (
                "Generate a stylized urban map. Buildings as solid dark shapes "
                "with clean edges, roads as thin white lines or paths, green "
                "areas (parks, lawns, vegetation) as solid green blocks. "
                "Maintain realistic scale and spatial relationships. "
                "Top-down view, flat graphic style, high contrast, "
                "presentation-ready"
            ),
        },
        {
            "id": "style_minimal_carto",
            "label": "Minimal cartographic",
            "prompt": (
                "Simplify into a minimal cartographic style. Clean outlines, "
                "muted pastel tones, no photographic texture. "
                "Suitable as a neutral base map for overlaying GIS data layers"
            ),
        },
        {
            "id": "style_isometric",
            "label": "Stylized isometric map",
            "prompt": (
                "Transform into a stylized isometric map with soft colors, "
                "clean geometric shapes, and elegant labeling. "
                "High-end urban illustration style, like a planning poster"
            ),
        },
    ],
    "detect": [
        {
            "id": "detect_buildings_mask",
            "label": "Detect buildings (mask)",
            "prompt": (
                "Detect all buildings in this image. Mark the building areas "
                "in red (#FF0000) and everything else in white (#FFFFFF). "
                "Solid fill, clean edges, no gradients"
            ),
        },
        {
            "id": "detect_buildings_overlay",
            "label": "Detect buildings (overlay)",
            "prompt": (
                "Detect and outline all buildings on the original image. "
                "Draw bright red (#FF0000) borders around each building "
                "with solid 2-pixel lines. Keep the original image visible"
            ),
        },
        {
            "id": "detect_vegetation_mask",
            "label": "Detect vegetation (mask)",
            "prompt": (
                "Detect all vegetation (trees, grass, parks, hedges). "
                "Mark vegetation areas in green (#00FF00) and everything "
                "else in white (#FFFFFF). Solid fill, clean edges"
            ),
        },
        {
            "id": "detect_vegetation_overlay",
            "label": "Detect vegetation (overlay)",
            "prompt": (
                "Detect and outline all tree canopy and vegetation areas "
                "on the original image. Draw bright green (#00FF00) borders "
                "around each vegetated zone. Keep the original image visible"
            ),
        },
        {
            "id": "detect_water_overlay",
            "label": "Detect water bodies (overlay)",
            "prompt": (
                "Detect and outline all water bodies (lakes, rivers, ponds, "
                "pools) on the original image. Draw bright blue (#0000FF) "
                "borders around each water body. Keep the original image visible"
            ),
        },
        {
            "id": "detect_roads_overlay",
            "label": "Detect roads (overlay)",
            "prompt": (
                "Detect and outline all roads and paved surfaces on the "
                "original image. Draw bright yellow (#FFD700) borders along "
                "each road edge. Keep the original image visible"
            ),
        },
        {
            "id": "detect_impervious",
            "label": "Impervious surfaces (mask)",
            "prompt": (
                "Classify this image into pervious and impervious surfaces. "
                "Mark all impervious areas (buildings, roads, parking lots, "
                "paved surfaces) in red (#FF0000). Mark all pervious areas "
                "(vegetation, soil, water) in green (#00FF00). "
                "Solid fill, clean boundaries"
            ),
        },
        {
            "id": "detect_construction",
            "label": "Detect construction sites",
            "prompt": (
                "Find any construction sites or areas under development if present. "
                "Mark them in red (#FF0000), everything else in white (#FFFFFF). "
                "If no construction is visible, output a fully white image"
            ),
        },
    ],
    "simulate": [
        {
            "id": "simulate_sea_level",
            "label": "Sea level rise",
            "prompt": (
                "Simulate rising sea water flooding the low-lying areas visible "
                "in this image. Water should fill naturally from the lowest points. "
                "Keep elevated terrain and any structures intact"
            ),
        },
        {
            "id": "simulate_urban_forest",
            "label": "Urban forest",
            "prompt": (
                "Replace paved or bare open areas with a dense urban forest "
                "of mature trees. Keep existing buildings and roads intact"
            ),
        },
        {
            "id": "simulate_new_building",
            "label": "New building",
            "prompt": (
                "Add a modern building in any open or undeveloped area visible. "
                "Match the scale of surrounding structures if any are present"
            ),
        },
        {
            "id": "simulate_mixed_use",
            "label": "Mixed-use neighborhood",
            "prompt": (
                "Transform open or underused areas into a mixed-use neighborhood "
                "with housing, shops, and green spaces. "
                "Blend with the existing surroundings"
            ),
        },
        {
            "id": "simulate_solar_farm",
            "label": "Solar farm",
            "prompt": (
                "Convert open land into a solar farm with orderly rows of "
                "photovoltaic panels. Keep any existing access roads. "
                "Maintain visible field or parcel boundaries"
            ),
        },
        {
            "id": "simulate_green_roof",
            "label": "Green rooftops",
            "prompt": (
                "Add green vegetation and rooftop gardens on any visible flat "
                "rooftops. Keep sloped roofs unchanged. Vegetation should be "
                "lush and varied"
            ),
        },
    ],
}

# IDs of presets to feature in Favorites (one per category, order matters)
_FAVORITE_IDS = [
    "enhance_aerial",           # Clean (enhance)
    "add_solar_panels",         # Add
    "style_land_use",           # Style
    "detect_buildings_mask",    # Detect
    "simulate_sea_level",       # Simulate
]


def _build_favorites() -> list[dict]:
    """Collect favorite presets from all categories."""
    lookup: dict[str, tuple[str, dict]] = {}
    for cat_key, presets in _PRESETS_BY_CATEGORY.items():
        for p in presets:
            lookup[p["id"]] = (cat_key, p)

    favorites = []
    for fav_id in _FAVORITE_IDS:
        if fav_id in lookup:
            cat_key, preset = lookup[fav_id]
            favorites.append({**preset, "source_category": cat_key})
    return favorites


def get_all_categories() -> list[dict]:
    """Return all categories with translated labels, including favorites first."""
    result = []

    fav_presets = _build_favorites()
    result.append({
        "key": "favorites",
        "label": tr("Top Picks"),
        "presets": [
            {
                "id": p["id"],
                "label": tr(p["label"]),
                "prompt": p["prompt"],
                "source_category": p.get("source_category", ""),
            }
            for p in fav_presets
        ],
    })

    for cat_key in ["clean", "add", "style", "detect", "simulate"]:
        presets = _PRESETS_BY_CATEGORY.get(cat_key, [])
        result.append({
            "key": cat_key,
            "label": tr(cat_key.capitalize()),
            "presets": [
                {
                    "id": p["id"],
                    "label": tr(p["label"]),
                    "prompt": p["prompt"],
                }
                for p in presets
            ],
        })

    return result
