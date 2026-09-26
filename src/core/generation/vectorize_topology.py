













from __future__ import annotations

import numpy as np
from osgeo import gdal
from qgis.core import QgsGeometry, QgsLineString, QgsPolygon


def _ring_keys(points, inv, stride: int) -> list[int]:


    out: list[int] = []
    prev = None
    for pt in points:
        px = inv[0] + inv[1] * pt[0] + inv[2] * pt[1]
        py = inv[3] + inv[4] * pt[0] + inv[5] * pt[1]
        ix, iy = int(round(px)), int(round(py))
        if prev is None:
            out.append(iy * stride + ix)
        else:
            pix, piy = prev
            dx, dy = ix - pix, iy - piy
            if dx and not dy:
                step = 1 if dx > 0 else -1
                out.extend(iy * stride + x for x in range(pix + step, ix + step, step))
            elif dy and not dx:
                step = 1 if dy > 0 else -1
                out.extend(y * stride + ix for y in range(piy + step, iy + step, step))
            elif dx or dy:
                out.append(iy * stride + ix)
        prev = (ix, iy)
    if len(out) > 1 and out[-1] == out[0]:
        out.pop()
    return out


def _douglas_peucker(pts: np.ndarray, tol: float) -> np.ndarray:

    n = len(pts)
    if n < 3 or tol <= 0:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[b] - pts[a]
        rel = pts[a + 1:b] - pts[a]
        length = float(np.hypot(seg[0], seg[1]))
        if length == 0.0:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / length
        i = int(np.argmax(dist))
        if dist[i] > tol:
            mid = a + 1 + i
            keep[mid] = True
            stack.append((a, mid))
            stack.append((mid, b))
    return pts[keep]


def _chaikin(pts: np.ndarray, iterations: int, closed: bool) -> np.ndarray:


    for _ in range(iterations):
        if closed:
            p, q = pts, np.roll(pts, -1, axis=0)
        else:
            if len(pts) < 3:
                return pts
            p, q = pts[:-1], pts[1:]
        cut = np.empty((2 * len(p), 2))
        cut[0::2] = 0.75 * p + 0.25 * q
        cut[1::2] = 0.25 * p + 0.75 * q
        pts = cut if closed else np.vstack([pts[:1], cut[1:-1], pts[-1:]])
    return pts


class _ArcSimplifier:
    def __init__(self, stride: int, tol_px: float, smooth_iterations: int):
        self._stride = stride
        self._tol = tol_px
        self._smooth = smooth_iterations
        self._cache: dict[tuple, np.ndarray] = {}

    def _xy(self, keys) -> np.ndarray:
        arr = np.asarray(keys, dtype=np.int64)
        return np.column_stack([arr % self._stride, arr // self._stride]).astype(np.float64)

    def open_arc(self, arc: list[int]) -> np.ndarray:
        fwd = tuple(arc)
        rev = fwd[::-1]
        key, flipped = (fwd, False) if fwd <= rev else (rev, True)
        done = self._cache.get(key)
        if done is None:
            pts = _douglas_peucker(self._xy(key), self._tol)
            done = _chaikin(pts, self._smooth, closed=False) if self._smooth else pts
            self._cache[key] = done
        return done[::-1] if flipped else done

    def closed_ring(self, ring: list[int]) -> np.ndarray:



        start = ring.index(min(ring))
        rot = ring[start:] + ring[:start]
        fwd = tuple(rot)
        rev = (rot[0],) + tuple(rot[:0:-1])
        key, flipped = (fwd, False) if fwd <= rev else (rev, True)
        done = self._cache.get(key)
        if done is None:
            pts = self._xy(key)
            if self._tol > 0 and len(pts) > 3:
                far = int(np.argmax(np.hypot(*(pts - pts[0]).T)))
                if far > 0:
                    first = _douglas_peucker(pts[: far + 1], self._tol)
                    second = _douglas_peucker(np.vstack([pts[far:], pts[:1]]), self._tol)
                    simplified = np.vstack([first[:-1], second[:-1]])
                    if len(simplified) >= 3:
                        pts = simplified
            done = _chaikin(pts, self._smooth, closed=True) if self._smooth else pts
            self._cache[key] = done
        if flipped:
            done = np.vstack([done[:1], done[:0:-1]])
        return done


def simplify_shared(
    ogr_geoms: list, gt, tol_px: float, smooth: bool, is_cancelled=None, size=None
) -> list[QgsGeometry] | None:







    inv = gdal.InvGeoTransform(gt)
    if inv is None:
        return [QgsGeometry.fromWkt(g.ExportToWkt()) for g in ogr_geoms]
    polys: list[list[list[int]]] = []
    max_x = 0
    all_rings = []
    for g in ogr_geoms:
        rings = []
        for i in range(g.GetGeometryCount()):
            pts = g.GetGeometryRef(i).GetPoints() or []
            rings.append(pts)
            for pt in pts:
                px = inv[0] + inv[1] * pt[0] + inv[2] * pt[1]
                max_x = max(max_x, int(round(px)))
        all_rings.append(rings)
    stride = max_x + 3
    for rings in all_rings:
        polys.append([_ring_keys(pts, inv, stride) for pts in rings])



    edges = set()
    for rings in polys:
        for ring in rings:
            n = len(ring)
            for i in range(n):
                a, b = ring[i], ring[(i + 1) % n]
                edges.add((a, b) if a < b else (b, a))
    degree: dict[int, int] = {}
    for a, b in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    del edges

    if size is not None:
        width, height = size
        for rings in polys:
            for ring in rings:
                for k in ring:
                    iy, ix = divmod(k, stride)
                    if ix in (0, width) or iy in (0, height):
                        degree[k] = 0
    arcs = _ArcSimplifier(stride, tol_px, 5 if smooth else 0)
    out: list[QgsGeometry] = []
    for count, rings in enumerate(polys):
        if count % 256 == 0 and is_cancelled is not None and is_cancelled():
            return None
        built = []
        for ring_idx, ring in enumerate(rings):
            if len(ring) < 3:
                if ring_idx == 0:
                    break
                continue
            nodes = [i for i, k in enumerate(ring) if degree.get(k, 0) != 2]
            if not nodes:
                pts = arcs.closed_ring(ring)
            else:
                n = len(ring)
                pieces = []
                for a, b in zip(nodes, nodes[1:] + [nodes[0] + n]):
                    arc = [ring[i % n] for i in range(a, b + 1)]
                    pieces.append(arcs.open_arc(arc)[:-1])
                pts = np.vstack(pieces)
            if len(pts) < 3:


                if ring_idx == 0:
                    break
                continue
            xs = gt[0] + pts[:, 0] * gt[1] + pts[:, 1] * gt[2]
            ys = gt[3] + pts[:, 0] * gt[4] + pts[:, 1] * gt[5]
            xs = np.append(xs, xs[0])
            ys = np.append(ys, ys[0])
            built.append(QgsLineString(xs.tolist(), ys.tolist()))
        if not built:
            out.append(QgsGeometry())
            continue
        poly = QgsPolygon()
        poly.setExteriorRing(built[0])
        for hole in built[1:]:
            poly.addInteriorRing(hole)
        out.append(QgsGeometry(poly))
    return out
