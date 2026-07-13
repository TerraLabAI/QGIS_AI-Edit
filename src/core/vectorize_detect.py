"""Detect flat-tint (segmentation / land-cover style) generation outputs.

A result made of a few flat color zones is almost always a segmentation,
mask, or land-cover style output: the user's real next step is vectorizing
it, not keeping the colors. The result panel uses this to surface the
"Vectorize this result" CTA even when neither a template nor the prompt
announced the intent (manual land-cover prompts get no CTA otherwise).

The hard requirement is the opposite direction: an ordinary photographic edit
(an edited orthophoto) must NEVER trip this. A photo can still resolve to a
small palette after quantization (asphalt greys, muted tones), so coverage
alone is not enough. The decision therefore ANDs three signals: few real
colors, the top colors covering the vast majority of pixels, AND flat local
texture (posterized fills, not photographic noise). Only a result that is flat
on all three counts lights the CTA.

Pure numeric core (`_analyze`) is QGIS-free and unit-testable; the QImage
decode is a thin lazy shell. A 96px downsample keeps the pixel loops around
10k iterations, no numpy needed.
"""

from __future__ import annotations

# Longest side of the downsampled analysis frame. 96px keeps zone shares
# accurate to about a percent while staying trivially fast in pure Python.
_SAMPLE_SIDE = 96

# 16 levels per channel: wide enough that one flat tint plus its model noise
# and anti-aliased edges lands in one bucket, narrow enough that two land
# cover classes never merge.
_QUANT_SHIFT = 4

# A flat-tint output concentrates almost all pixels in a handful of buckets.
# When the prompt already reads like a segmentation / land-cover ask the bar
# drops a notch (models shade those outputs a little more than a pure mask).
_TOP_N = 8
_BASE_COVERAGE = 0.90
_HINTED_COVERAGE = 0.85

# A merged class below this share is edge noise, not a class. Deliberately
# tiny: a roads or water mask is 97-99% background with a 1-3% foreground,
# and that foreground is exactly what the user wants traced.
_MIN_CLASS_SHARE = 0.005

# Buckets whose MEAN colors sit within this per-channel distance are one
# visual tint split by a quantization boundary (a flat color at value ~127
# plus model noise straddles two 16-level buckets per channel, and mask reds
# drift across neighboring buckets); fold them together before judging
# coverage. Two bucket steps: distinct land-cover classes stay >40 apart.
_MERGE_DISTANCE = 32

# Merging is quadratic, so only the biggest buckets participate; everything
# below is noise that could never form a class anyway.
_MERGE_CANDIDATES = 64

# --- Photo rejection gates -------------------------------------------------
# A pixel is one of a flat output's "real colors" only if it holds at least
# this share of the frame. Counting by share (not raw distinct buckets) keeps
# the tally anti-alias robust: edge slivers between fills stay below it.
_SIGNIFICANT_SHARE = 0.004
# A flat-color output resolves to few real colors; a photo's textured regions
# spread across many. Generous enough for a busy multi-class land-cover map.
_MAX_SIGNIFICANT_COLORS = 32

# Posterization gate. Inside a flat fill neighboring pixels are near-identical;
# photographic texture varies pixel to pixel. The tolerance absorbs JPEG and
# anti-alias noise within a fill without admitting real texture.
_FLAT_TOLERANCE = 8
# Fraction of adjacent-pixel pairs that must be flat. Region boundaries are the
# only non-flat pairs in a true map, so a comfortably-below-1 bar still clears
# busy maps while a textured photo (near 0) never approaches it.
_FLAT_FRACTION_MIN = 0.55


def _flat_fraction(
    pixels: list[tuple[int, int, int]], width: int, height: int
) -> float:
    """Share of horizontal/vertical neighbor pairs that are near-identical.

    High for posterized fills (only region boundaries differ), near zero for
    photographic texture. Cheap: one pass over the downsampled frame.
    """
    flat = 0
    pairs = 0
    tol = _FLAT_TOLERANCE
    for y in range(height):
        base = y * width
        for x in range(width):
            r, g, b = pixels[base + x]
            if x + 1 < width:
                r2, g2, b2 = pixels[base + x + 1]
                pairs += 1
                if abs(r - r2) <= tol and abs(g - g2) <= tol and abs(b - b2) <= tol:
                    flat += 1
            if y + 1 < height:
                r2, g2, b2 = pixels[base + x + width]
                pairs += 1
                if abs(r - r2) <= tol and abs(g - g2) <= tol and abs(b - b2) <= tol:
                    flat += 1
    return flat / pairs if pairs else 0.0


def _merge_buckets(ranked: list[list[int]]) -> list[list[float]]:
    """Fold near-identical buckets (quantization boundary splits) into one
    class, biggest buckets first. Returns [count, sum_r, sum_g, sum_b] rows."""
    merged: list[list[float]] = []
    for count, sum_r, sum_g, sum_b in ranked[:_MERGE_CANDIDATES]:
        mean_r, mean_g, mean_b = sum_r / count, sum_g / count, sum_b / count
        target = None
        for cls in merged:
            if (
                abs(cls[1] / cls[0] - mean_r) <= _MERGE_DISTANCE
                and abs(cls[2] / cls[0] - mean_g) <= _MERGE_DISTANCE
                and abs(cls[3] / cls[0] - mean_b) <= _MERGE_DISTANCE
            ):
                target = cls
                break
        if target is None:
            merged.append([count, sum_r, sum_g, sum_b])
        else:
            target[0] += count
            target[1] += sum_r
            target[2] += sum_g
            target[3] += sum_b
    merged.sort(key=lambda e: e[0], reverse=True)
    return merged


def _analyze(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    seg_hint: bool = False,
) -> list[tuple[str, float]] | None:
    """QGIS-free decision core. ``pixels`` is a row-major list of (r, g, b)
    triples, ``width * height`` long. Returns the flat-color classes (see
    detect_flat_colors), or None when the frame is not a flat-color output."""
    total = width * height
    if total == 0 or len(pixels) < total:
        return None

    # Bucket every pixel into a coarse 16-level-per-channel color cube.
    buckets: dict[int, list[int]] = {}
    for r, g, b in pixels:
        key = ((r >> _QUANT_SHIFT) << 8) | ((g >> _QUANT_SHIFT) << 4) | (b >> _QUANT_SHIFT)
        entry = buckets.get(key)
        if entry is None:
            buckets[key] = [1, r, g, b]
        else:
            entry[0] += 1
            entry[1] += r
            entry[2] += g
            entry[3] += b

    # Gate 1 - few real colors. A photo's textured regions spread across many
    # buckets; only significant ones count, so edge slivers never inflate it.
    significant = sum(
        1 for e in buckets.values() if e[0] / total >= _SIGNIFICANT_SHARE
    )
    if significant > _MAX_SIGNIFICANT_COLORS:
        return None

    # Gate 2 - posterized, not photographic. This is the signal that separates
    # a flat map from a photo that merely happens to have a small palette.
    if _flat_fraction(pixels, width, height) < _FLAT_FRACTION_MIN:
        return None

    # Gate 3 - the top colors cover the vast majority of the frame.
    merged = _merge_buckets(
        sorted(buckets.values(), key=lambda e: e[0], reverse=True)
    )
    coverage = sum(e[0] for e in merged[:_TOP_N]) / total
    threshold = _HINTED_COVERAGE if seg_hint else _BASE_COVERAGE
    if coverage < threshold:
        return None

    classes: list[tuple[str, float]] = []
    for count, sum_r, sum_g, sum_b in merged[:_TOP_N]:
        share = count / total
        if share < _MIN_CLASS_SHARE:
            break
        classes.append(
            (
                f"#{round(sum_r / count):02X}{round(sum_g / count):02X}{round(sum_b / count):02X}",
                round(share, 4),
            )
        )
    # Fewer than two real zones means there is nothing to trace: a pure
    # single-color wash never qualifies, but a 99% background with a small
    # foreground (a roads or water mask) does.
    if len(classes) < 2:
        return None
    return classes


def detect_flat_colors(
    image_bytes: bytes, seg_hint: bool = False
) -> list[tuple[str, float]] | None:
    """Return the dominant flat colors of a generated image, or None.

    A non-None return means "this output is a small set of flat color zones,
    suggest vectorizing it": a list of (hex, share) pairs sorted by coverage,
    at most _TOP_N entries, each covering at least _MIN_CLASS_SHARE. The hex
    is the MEAN color of the bucket (a real image color, usable to pre-fill
    the vectorize panel), not the bucket's quantized center.

    An ordinary photographic result returns None even when its palette is
    small (see the module docstring for the three-signal test).

    seg_hint relaxes the coverage threshold when the prompt already read like
    a segmentation / land-cover request (see detect_seg_context).
    """
    if not image_bytes:
        return None
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QImage

    img = QImage.fromData(image_bytes)
    if img.isNull():
        return None
    img = img.scaled(
        _SAMPLE_SIDE,
        _SAMPLE_SIDE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    ).convertToFormat(QImage.Format.Format_RGB32)
    width, height = img.width(), img.height()
    if width * height == 0:
        return None

    pixels: list[tuple[int, int, int]] = []
    for y in range(height):
        for x in range(width):
            rgb = img.pixel(x, y)
            pixels.append(((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF))

    return _analyze(pixels, width, height, seg_hint=seg_hint)


def pick_foreground_color(classes: list[tuple[str, float]]) -> str:
    """The class to pre-fill in the vectorize panel: the largest one, unless
    it dominates the frame (then it is the background and the second class is
    the interesting one, e.g. red buildings on a white mask)."""
    if len(classes) > 1 and classes[0][1] > 0.5:
        return classes[1][0]
    return classes[0][0]
