"""Palette detection and class naming for Vectorize.

Turns a generated flat-color map into a list of candidate classes: the
dominant colors, how much of the map each covers, whether one of them is
the background, and a best-effort human name per color. Pure numpy + GDAL,
no QGIS layer objects, safe on the main thread (decimated read).
"""
from __future__ import annotations

import os

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from osgeo import gdal

from ..i18n import tr

# Colors that name themselves. The ESA WorldCover palette is the one users
# ask for by name ("ESA land cover" template) so an exact-ish hit on those
# hexes is safe to label. Matched by summed-channel distance, tightest first.
_KNOWN_CLASS_COLORS: tuple[tuple[tuple[int, int, int], str], ...] = (
    ((0, 100, 0), "tree cover"),
    ((255, 187, 34), "shrubland"),
    ((255, 255, 76), "grassland"),
    ((240, 150, 255), "cropland"),
    ((250, 0, 0), "built-up"),
    ((180, 180, 180), "bare ground"),
    ((240, 240, 240), "snow and ice"),
    ((0, 100, 200), "water"),
    ((0, 150, 160), "wetland"),
    ((0, 207, 117), "mangroves"),
    ((250, 230, 160), "moss and lichen"),
)
_KNOWN_MATCH_L1 = 36


def suggest_class_label(rgb: tuple[int, int, int]) -> str:
    """Best-effort semantic name for a class color, '' when unsure.

    Exact-ish ESA WorldCover hits first, then a conservative hue heuristic
    (blue = water, green = vegetation, near-neutral gray = paved). Labels are
    user-editable in the panel, so a miss costs one rename, but stay
    conservative: a wrong guess reads worse than no guess.
    """
    r, g, b = rgb
    mx, mn = max(rgb), min(rgb)
    sat = mx - mn
    # Neutrals never match the named table: a mask's white background sits one
    # step from ESA's snow hex, and grays are paved/bare/void depending on the
    # map. Guess generically or not at all.
    if sat <= 24:
        if mx >= 225:
            return ""  # near-white: background OR buildings; detect_classes decides
        if mx <= 40:
            return ""  # near-black: roads on some maps, void on others
        return tr("paved")
    for known_rgb, label in _KNOWN_CLASS_COLORS:
        if abs(known_rgb[0] - r) + abs(known_rgb[1] - g) + abs(known_rgb[2] - b) <= _KNOWN_MATCH_L1:
            return tr(label)
    if b > r + 30 and b > g + 20:
        return tr("water")
    if g > r + 25 and g > b + 25:
        return tr("vegetation")
    return ""


def looks_like_background(
    rgb: tuple[int, int, int], fraction: float, n_colors: int, rank: int
) -> bool:
    """Heuristic for "this color is the map's background, not a class".

    Near-white is background on every Segment/mask template ("class in color,
    everything else white") - but only when it DOMINATES the map. A minority
    off-white in a multi-color map is usually a real class (site plans paint
    buildings off-white). On a plain 2-color map the dominant color is the
    background even when it isn't white (e.g. black mask ground).
    """
    r, g, b = rgb
    near_white = min(r, g, b) >= 225 and (max(r, g, b) - min(r, g, b)) <= 26
    if near_white and (rank == 0 or fraction >= 0.4):
        return True
    return n_colors == 2 and rank == 0 and fraction >= 0.5


def detect_classes(raster_path: str) -> list[dict]:
    """Detect the map's flat colors as ready-to-vectorize class entries.

    Returns ``[{"rgb": (r,g,b), "fraction": float, "label": str,
    "is_background": bool}, ...]`` sorted by coverage, at most one entry
    flagged as background. Empty list when the raster is unreadable.
    """
    palette = dominant_palette(raster_path)
    entries: list[dict] = []
    background_seen = False
    for rank, (rgb, fraction) in enumerate(palette):
        is_bg = not background_seen and looks_like_background(rgb, fraction, len(palette), rank)
        background_seen = background_seen or is_bg
        entries.append(
            {
                "rgb": rgb,
                "fraction": fraction,
                "label": tr("background") if is_bg else suggest_class_label(rgb),
                "is_background": is_bg,
            }
        )
    return entries


def dominant_palette(
    raster_path: str,
    max_colors: int = 10,
    quant: int = 24,
    merge_l1: int = 80,
    min_fraction: float = 0.02,
    sample_max: int = 1_000_000,
) -> list[tuple[tuple[int, int, int], float]]:
    """Detect the dominant flat colors in a generated map.

    A "2-color" map actually carries thousands of anti-aliased shades, so this
    quantizes, then merges near shades (within ``merge_l1`` summed-channel
    distance) into their most-common base color. Returns ``[((r,g,b), fraction),
    ...]`` sorted by coverage. ``merge_l1`` sits above the widest quantization
    gap (adjacent buckets are 72 apart) but below the tightest pair in the ESA
    WorldCover palette (water vs wetland is 90 apart) so a 10-class land cover
    keeps every class separate. Pure numpy + GDAL, decimated to stay fast on 4K
    rasters; safe to call on the main thread.
    """
    if np is None or not raster_path or not os.path.exists(raster_path):
        return []
    ds = gdal.Open(raster_path)
    if ds is None or ds.RasterCount < 3:
        return []
    width, height = ds.RasterXSize, ds.RasterYSize
    # The palette is scale-invariant, so read a decimated buffer (at most
    # ~sample_max px) to keep detection near-instant even on a 4K output.
    scale = max(1, int((width * height / float(sample_max)) ** 0.5))
    bw, bh = max(1, width // scale), max(1, height // scale)
    r = ds.GetRasterBand(1).ReadAsArray(buf_xsize=bw, buf_ysize=bh)
    g = ds.GetRasterBand(2).ReadAsArray(buf_xsize=bw, buf_ysize=bh)
    b = ds.GetRasterBand(3).ReadAsArray(buf_xsize=bw, buf_ysize=bh)
    ds = None
    if r is None or g is None or b is None:
        return []
    a = np.stack([r, g, b], axis=-1).reshape(-1, 3).astype(np.int32)
    q = (a // quant) * quant + quant // 2
    keys = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    total = int(keys.size) or 1
    kept: list[list] = []  # [ [(r,g,b), count], ... ], representative = most common shade
    for idx in order:
        key = int(vals[idx])
        rr, gg, bb = (key >> 16) & 255, (key >> 8) & 255, key & 255
        cnt = int(counts[idx])
        merged = False
        for entry in kept:
            kr, kg, kb = entry[0]
            if abs(kr - rr) + abs(kg - gg) + abs(kb - bb) <= merge_l1:
                entry[1] += cnt
                merged = True
                break
        if not merged:
            kept.append([(rr, gg, bb), cnt])
        if len(kept) >= max_colors and cnt < total * 0.01:
            break
    out = [(rgb, c / total) for rgb, c in kept if c / total >= min_fraction]
    out.sort(key=lambda t: -t[1])
    return out
