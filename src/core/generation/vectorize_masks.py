"""Mask refinement with optional scipy acceleration and equivalent numpy fallbacks."""
from __future__ import annotations

from collections import deque

try:
    import numpy as np
except ImportError:
    np = None


def _refine_mask(
    mask,
    expand_value: int = 0,
    fill_holes: bool = False,
):
    """Dilate/erode then optionally fill interior holes. scipy fast-path,
    pure-numpy fallback. Same order as AI Segmentation's apply_mask_refinement.
    """
    result = np.asarray(mask, dtype=np.uint8).copy()
    if expand_value != 0:
        iterations = abs(int(expand_value))
        try:
            from scipy import ndimage
            structure = ndimage.generate_binary_structure(2, 1)
            if expand_value > 0:
                result = ndimage.binary_dilation(
                    result, structure=structure, iterations=iterations
                ).astype(np.uint8)
            else:
                result = ndimage.binary_erosion(
                    result, structure=structure, iterations=iterations
                ).astype(np.uint8)
        except ImportError:
            result = _numpy_morphology(result, iterations, expand=expand_value > 0)
    if fill_holes:
        try:
            from scipy import ndimage
            result = ndimage.binary_fill_holes(result).astype(np.uint8)
        except ImportError:
            result = _numpy_fill_holes(result)
    return result


def _numpy_morphology(mask, iterations: int, expand: bool):
    """Pure-numpy 4-connected dilation/erosion fallback when scipy missing."""
    result = mask.copy()
    for _ in range(iterations):
        updated = result.copy()
        if expand:
            updated[1:, :] |= result[:-1, :]
            updated[:-1, :] |= result[1:, :]
            updated[:, 1:] |= result[:, :-1]
            updated[:, :-1] |= result[:, 1:]
        else:
            updated[1:, :] &= result[:-1, :]
            updated[:-1, :] &= result[1:, :]
            updated[:, 1:] &= result[:, :-1]
            updated[:, :-1] &= result[:, 1:]
            updated[0, :] = updated[-1, :] = 0
            updated[:, 0] = updated[:, -1] = 0
        if np.array_equal(updated, result):
            break
        result = updated
    return result


def _numpy_fill_holes(mask):
    """Flood exterior background by horizontal spans, with no distance cutoff.

    Each span is visited once. A narrow winding channel remains exterior even
    when its path is longer than the image width or the former 2048-step cap.
    """
    height, width = mask.shape
    if not height or not width:
        return mask.copy()
    background = mask == 0
    queue = deque([(0, x) for x in range(width)] + [(height - 1, x) for x in range(width)])
    queue.extend((y, 0) for y in range(height))
    queue.extend((y, width - 1) for y in range(height))
    exterior = np.zeros(mask.shape, dtype=bool)
    while queue:
        y, x = queue.popleft()
        if not background[y, x] or exterior[y, x]:
            continue
        left = right = x
        while left > 0 and background[y, left - 1] and not exterior[y, left - 1]:
            left -= 1
        while right + 1 < width and background[y, right + 1] and not exterior[y, right + 1]:
            right += 1
        exterior[y, left:right + 1] = True
        for ny in (y - 1, y + 1):
            if 0 <= ny < height:
                segment = background[ny, left:right + 1] & ~exterior[ny, left:right + 1]
                starts = segment & ~np.r_[False, segment[:-1]]
                queue.extend((ny, left + int(offset)) for offset in np.flatnonzero(starts))
    return (np.asarray(mask, dtype=bool) | (background & ~exterior)).astype(np.uint8)
