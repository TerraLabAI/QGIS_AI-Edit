"""Detect flat-tint (segmentation / land-cover style) generation outputs.

A result made of a few flat color zones is almost always a segmentation,
mask, or land-cover style output: the user's real next step is vectorizing
it, not keeping the colors. The result panel uses this to surface the
"Vectorize this result" CTA even when neither a template nor the prompt
announced the intent (manual land-cover prompts are ~18% of generations and
previously got no CTA at all).

Pure function over the downloaded image bytes; runs inside the generation
worker (a 96px downsample keeps the pixel loop around 10k iterations, no
numpy needed).
"""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QImage

# Longest side of the downsampled analysis frame. 96px keeps zone shares
# accurate to about a percent while staying trivially fast in pure Python.
_SAMPLE_SIDE = 96

# 16 levels per channel: wide enough that one flat tint plus its model noise
# and anti-aliased edges lands in one bucket, narrow enough that two land
# cover classes never merge.
_QUANT_SHIFT = 4

# A flat-tint output concentrates almost all pixels in a handful of buckets.
# Thresholds calibrated on real production outputs (2026-07): mask / land
# cover results score 0.99-1.00 after merging, ordinary photo results peak
# around 0.81, so the 0.85-0.90 band separates them with margin either way.
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


def detect_flat_colors(
    image_bytes: bytes, seg_hint: bool = False
) -> list[tuple[str, float]] | None:
    """Return the dominant flat colors of a generated image, or None.

    A non-None return means "this output is a small set of flat color zones,
    suggest vectorizing it": a list of (hex, share) pairs sorted by coverage,
    at most _TOP_N entries, each covering at least _MIN_CLASS_SHARE. The hex
    is the MEAN color of the bucket (a real image color, usable to pre-fill
    the vectorize panel), not the bucket's quantized center.

    seg_hint relaxes the coverage threshold when the prompt already read like
    a segmentation / land-cover request (see detect_seg_context).
    """
    if not image_bytes:
        return None
    img = QImage.fromData(image_bytes)
    if img.isNull():
        return None
    img = img.scaled(
        _SAMPLE_SIDE,
        _SAMPLE_SIDE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    ).convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    total = w * h
    if total == 0:
        return None

    # bucket key -> [count, sum_r, sum_g, sum_b]
    buckets: dict[int, list[int]] = {}
    for y in range(h):
        for x in range(w):
            rgb = img.pixel(x, y)
            r = (rgb >> 16) & 0xFF
            g = (rgb >> 8) & 0xFF
            b = rgb & 0xFF
            key = ((r >> _QUANT_SHIFT) << 8) | ((g >> _QUANT_SHIFT) << 4) | (b >> _QUANT_SHIFT)
            entry = buckets.get(key)
            if entry is None:
                buckets[key] = [1, r, g, b]
            else:
                entry[0] += 1
                entry[1] += r
                entry[2] += g
                entry[3] += b

    ranked = sorted(buckets.values(), key=lambda e: e[0], reverse=True)

    # Fold near-identical buckets back into one class (boundary splits).
    merged: list[list[float]] = []  # [count, sum_r, sum_g, sum_b]
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


def pick_foreground_color(classes: list[tuple[str, float]]) -> str:
    """The class to pre-fill in the vectorize panel: the largest one, unless
    it dominates the frame (then it is the background and the second class is
    the interesting one, e.g. red buildings on a white mask)."""
    if len(classes) > 1 and classes[0][1] > 0.5:
        return classes[1][0]
    return classes[0][0]
