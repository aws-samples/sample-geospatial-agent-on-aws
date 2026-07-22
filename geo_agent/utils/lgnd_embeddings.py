"""LGND Clay v1.5 pre-computed embeddings for country/state-wide change scans.

Powers the region-wide change scan (`scan_region_change`): queries pre-computed
Clay v1.5 embeddings from the public LGND dataset (hosted on Source Cooperative /
AWS Open Data) using DuckDB for fast spatial+temporal filtering, then computes
cosine similarity between monthly embeddings to produce a per-cell change score.
No GPU or SageMaker infrastructure is involved.

All region scans are executed via a Lambda fan-out (`lgnd-partition-query`),
one invocation per geohash partition. DuckDB runs inside the Lambda image,
not in the agent container.

Data source: s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings/
Format: GeoParquet, hive-partitioned by model_version/collection/chip_size/dims/geohash/year/month
Schema: chips_id, cell_id, embedding (list<float32>), bbox, geometry, datetime
License: CC BY 4.0
"""

from __future__ import annotations

import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

# LGND dataset configuration
LGND_BASE_URL = "s3://us-west-2.opendata.source.coop/clay/lgnd-embeddings"
MONTHLY_PATH = f"{LGND_BASE_URL}/monthly-aggregated"

# Default configuration
DEFAULT_MODEL_VERSION = "v1.5"
DEFAULT_COLLECTION = "sentinel-2-l2a"
DEFAULT_CHIP_SIZE = "1280m"
DEFAULT_DIMS = "256"

# Similarity thresholds for change scoring (calibrated for 256-dim embeddings).
#
# Calibrated against the observed Colorado distribution (159K cells): median
# same-place cosine similarity is ~0.86, so SIM_HIGH must sit near the stable
# bulk (~0.90) to keep unchanged terrain near score 0, and SIM_LOW must be low
# (~0.50) so the max score is reserved for the genuine tail (~bottom 5-8%)
# rather than ~23% of the state saturating at 1.0.
SIM_HIGH = 0.90   # above this = no change (score 0)
SIM_LOW = 0.50    # below this = max change (score 1)
SIM_RANGE = SIM_HIGH - SIM_LOW

# Artifact guard: cosine similarity below this almost always means one period's
# monthly aggregate was cloud/snow/nodata (degenerate embedding), not real land
# change. Negative similarities were observed down to -0.59. Drop these cells so
# they don't pollute the top hotspots.
ARTIFACT_SIM_FLOOR = 0.30

# Peak growing season months by hemisphere (most stable for change detection)
# Order matters: first month in list is preferred.
# NOTE: June (6) is intentionally EXCLUDED from the northern list — the LGND
# monthly archive has sparse June coverage for many geohashes (e.g. Colorado
# 2019 has July data but no June), so a June request must be redirected to July.
PEAK_SEASON_NORTH = [7, 8]      # July preferred, then August (June excluded — see above)
PEAK_SEASON_SOUTH = [1, 2]      # January preferred, then February
PEAK_SEASON_TROPICAL = [3, 9, 4, 10]  # Shoulder months (avoid wet/dry extremes)

# Land mask: loaded lazily on first use
_land_tree = None
_land_geoms = None


def _load_land_mask():
    """Load Natural Earth 110m land polygons for ocean filtering."""
    global _land_tree, _land_geoms
    if _land_tree is not None:
        return _land_tree

    import json
    from shapely.geometry import shape
    from shapely import STRtree

    # Find the data file relative to this module
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'ne_110m_land.geojson')
    if not os.path.exists(data_path):
        data_path = os.path.join('data', 'ne_110m_land.geojson')
    if not os.path.exists(data_path):
        data_path = os.path.join('geo_agent', 'data', 'ne_110m_land.geojson')

    if not os.path.exists(data_path):
        logger.warning("Land mask file not found, skipping ocean filter")
        return None

    with open(data_path) as f:
        geojson = json.load(f)

    polygons = []
    for feature in geojson['features']:
        geom = shape(feature['geometry'])
        if geom.is_valid:
            polygons.append(geom)

    _land_geoms = polygons
    _land_tree = STRtree(polygons)
    logger.info("Land mask loaded: %d polygons", len(polygons))
    return _land_tree


def _is_on_land(lon: float, lat: float) -> bool:
    """Check if a point is on land using the Natural Earth mask.

    Uses an envelope query (STRtree.query without a predicate) to find
    candidate polygons, then explicitly tests polygon.contains(point).
    This is robust across Shapely versions and avoids the predicate-
    direction pitfall (point.contains(polygon) is always False).
    """
    from shapely.geometry import Point

    tree = _load_land_mask()
    if tree is None:
        return True  # If no mask, assume on land

    pt = Point(lon, lat)
    # STRtree.query(pt) returns indices of tree geometries whose envelope
    # intersects the point; verify with an explicit containment test.
    for i in tree.query(pt):
        if _land_geoms[i].contains(pt):
            return True
    return False


def _get_peak_month(lat: float, user_month: int) -> int:
    """Determine the best peak-season month for a given latitude.

    If the user-requested month is already in peak season, use it.
    Otherwise, return the FIRST month in the peak season list (preferred).
    """
    if lat > 23.5:
        peak = PEAK_SEASON_NORTH
    elif lat < -23.5:
        peak = PEAK_SEASON_SOUTH
    else:
        peak = PEAK_SEASON_TROPICAL

    if user_month in peak:
        return user_month

    # Return the preferred month (first in list)
    return peak[0]


def _get_geohashes_for_bbox(west: float, south: float, east: float, north: float,
                            precision: int = 2) -> list[str]:
    """Compute geohash prefixes that cover a bounding box.

    The LGND dataset is partitioned by 2-character geohash prefixes.
    We need to find which geohash partitions intersect our AOI.

    Args:
        west, south, east, north: Bounding box in EPSG:4326.
        precision: Geohash precision (2 = ~600km cells).

    Returns:
        List of geohash strings that cover the bbox.
    """
    try:
        import pygeohash as pgh
    except ImportError:
        # Fallback: compute geohashes manually for precision 2
        # Each precision-2 geohash covers roughly 5° lat × 5° lon
        return _compute_geohashes_manual(west, south, east, north, precision)

    geohashes = set()
    # Sample points across the bbox to find covering geohashes
    lat_step = max((north - south) / 10, 0.5)
    lon_step = max((east - west) / 10, 0.5)

    lat = south
    while lat <= north:
        lon = west
        while lon <= east:
            gh = pgh.encode(lat, lon, precision=precision)
            geohashes.add(gh)
            lon += lon_step
        lat += lat_step

    # Also add corners and center
    for lat, lon in [(south, west), (south, east), (north, west), (north, east),
                     ((south + north) / 2, (west + east) / 2)]:
        gh = pgh.encode(lat, lon, precision=precision)
        geohashes.add(gh)

    return sorted(geohashes)


def _compute_geohashes_manual(west: float, south: float, east: float, north: float,
                              precision: int = 2) -> list[str]:
    """Manual geohash computation without pygeohash dependency.

    Uses the standard geohash encoding algorithm for precision 2.
    """
    BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

    def encode_one(lat: float, lon: float, prec: int) -> str:
        lat_range = [-90.0, 90.0]
        lon_range = [-180.0, 180.0]
        bits = 0
        bit_count = 0
        is_lon = True
        result = []

        while len(result) < prec:
            if is_lon:
                mid = (lon_range[0] + lon_range[1]) / 2
                if lon >= mid:
                    bits = bits * 2 + 1
                    lon_range[0] = mid
                else:
                    bits = bits * 2
                    lon_range[1] = mid
            else:
                mid = (lat_range[0] + lat_range[1]) / 2
                if lat >= mid:
                    bits = bits * 2 + 1
                    lat_range[0] = mid
                else:
                    bits = bits * 2
                    lat_range[1] = mid

            is_lon = not is_lon
            bit_count += 1

            if bit_count == 5:
                result.append(BASE32[bits])
                bits = 0
                bit_count = 0

        return "".join(result)

    geohashes = set()
    # Sample grid across bbox
    lat_step = max((north - south) / 5, 1.0)
    lon_step = max((east - west) / 5, 1.0)

    lat = south
    while lat <= north + lat_step:
        lon = west
        while lon <= east + lon_step:
            gh = encode_one(min(lat, 89.9), max(min(lon, 179.9), -180.0), precision)
            geohashes.add(gh)
            lon += lon_step
        lat += lat_step

    return sorted(geohashes)


# ---------------------------------------------------------------------------
# Country/Region-scale scanning
# ---------------------------------------------------------------------------

# Well-known country bounding boxes (EPSG:4326)
COUNTRY_BBOXES = {
    "colombia": (-79.0, -4.2, -66.9, 12.5),
    "brazil": (-73.98, -33.75, -34.79, 5.27),
    "peru": (-81.33, -18.35, -68.65, -0.04),
    "bolivia": (-69.64, -22.90, -57.45, -9.68),
    "ecuador": (-81.08, -5.01, -75.19, 1.68),
    "venezuela": (-73.35, 0.65, -59.80, 12.20),
    "costa rica": (-85.95, 8.03, -82.55, 11.22),
    "panama": (-83.05, 7.20, -77.17, 9.65),
    "guatemala": (-92.23, 13.74, -88.22, 17.82),
    "honduras": (-89.35, 12.98, -83.15, 16.52),
    "nicaragua": (-87.69, 10.71, -82.73, 15.03),
    "mexico": (-118.40, 14.53, -86.70, 32.72),
    "united states": (-125.0, 24.5, -66.9, 49.4),
    "usa": (-125.0, 24.5, -66.9, 49.4),
    "colorado": (-109.06, 36.99, -102.04, 41.00),
    "california": (-124.41, 32.53, -114.13, 42.01),
    "texas": (-106.65, 25.84, -93.51, 36.50),
    "florida": (-87.63, 24.52, -80.03, 31.00),
    "oregon": (-124.57, 41.99, -116.46, 46.29),
    "washington": (-124.73, 45.54, -116.92, 49.00),
    "amazon basin": (-73.98, -15.0, -50.0, 2.0),
    "rondonia": (-66.0, -13.7, -59.8, -7.9),
    "mato grosso": (-62.0, -18.0, -50.0, -7.3),
    "para": (-58.9, -9.8, -46.1, 2.5),
    "canada": (-141.0, 41.7, -52.6, 83.1),
    "argentina": (-73.58, -55.06, -53.59, -21.78),
    "chile": (-75.64, -55.98, -66.96, -17.50),
    "paraguay": (-62.65, -27.59, -54.26, -19.29),
    "uruguay": (-58.44, -35.03, -53.07, -30.08),
    "indonesia": (95.01, -11.01, 141.02, 6.08),
    "malaysia": (99.64, 0.85, 119.27, 7.36),
    "thailand": (97.34, 5.61, 105.64, 20.46),
    "vietnam": (102.14, 8.56, 109.47, 23.39),
    "myanmar": (92.19, 9.78, 101.17, 28.54),
    "india": (68.18, 6.75, 97.40, 35.50),
    "china": (73.50, 18.16, 134.77, 53.56),
    "australia": (113.16, -43.63, 153.64, -10.68),
    "democratic republic of congo": (12.18, -13.46, 31.31, 5.39),
    "drc": (12.18, -13.46, 31.31, 5.39),
    "congo": (12.18, -13.46, 31.31, 5.39),
    "cameroon": (8.49, 1.65, 16.19, 13.08),
    "nigeria": (2.69, 4.27, 14.68, 13.89),
    "ghana": (-3.26, 4.74, 1.20, 11.17),
    "ivory coast": (-8.60, 4.36, -2.49, 10.74),
    "kenya": (33.91, -4.72, 41.91, 5.02),
    "tanzania": (29.33, -11.75, 40.44, -0.99),
    "mozambique": (30.22, -26.87, 40.84, -10.47),
    "madagascar": (43.22, -25.60, 50.48, -11.95),
    "spain": (-9.39, 36.00, 3.35, 43.79),
    "france": (-5.14, 41.33, 9.56, 51.09),
    "germany": (5.87, 47.27, 15.04, 55.06),
    "united kingdom": (-8.18, 49.96, 1.77, 58.64),
    "uk": (-8.18, 49.96, 1.77, 58.64),
    "italy": (6.63, 36.62, 18.52, 47.09),
    "portugal": (-9.53, 36.84, -6.19, 42.15),
    "japan": (129.41, 31.03, 145.54, 45.55),
    "south korea": (125.89, 33.11, 129.58, 38.61),
    "philippines": (116.93, 4.64, 126.60, 18.65),
}


LAMBDA_FUNCTION_NAME = "lgnd-partition-query"


def _build_coherence_check(cells, min_neighbors, center_fn):
    """Return a predicate is_coherent(cell) -> bool.

    A genuine change hotspot is part of a contiguous block of changed cells;
    isolated single cells are usually cloud / edge / co-registration noise. This
    requires a cell to have >= min_neighbors changed neighbors (8-connected on the
    ~1.28km embedding grid). Uses an O(1) grid-hash lookup so it scales to large
    scans. Returns an always-True predicate when min_neighbors <= 0 (filter off).
    """
    import math
    if not min_neighbors or min_neighbors <= 0:
        return lambda c: True
    # Infer grid cell size (degrees) from a sample cell bbox.
    dx = dy = None
    for c in cells:
        b = c.get("bbox")
        if isinstance(b, dict):
            w = abs(b.get("xmax", 0) - b.get("xmin", 0))
            h = abs(b.get("ymax", 0) - b.get("ymin", 0))
            if w > 0 and h > 0:
                dx, dy = w, h
                break
    if not dx or not dy:
        return lambda c: True

    # Key on the cell's lower-left CORNER (a grid line), snapped with floor. Using
    # the center would land on half-cell multiples and hit banker's-rounding
    # ambiguity that misaligns neighbors; floor on the corner is stable and makes
    # adjacent cells differ by exactly 1 in each axis regardless of grid origin.
    def _key(b):
        return (int(math.floor(b.get("xmin", 0) / dx + 1e-9)),
                int(math.floor(b.get("ymin", 0) / dy + 1e-9)))

    occupied = {}
    for c in cells:
        b = c.get("bbox")
        if isinstance(b, dict):
            k = _key(b)
            occupied[k] = occupied.get(k, 0) + 1

    def _check(c):
        b = c.get("bbox")
        if not isinstance(b, dict):
            return True
        kx, ky = _key(b)
        n = 0
        for ax in (-1, 0, 1):
            for ay in (-1, 0, 1):
                if ax == 0 and ay == 0:
                    continue
                n += occupied.get((kx + ax, ky + ay), 0)
        return n >= min_neighbors

    return _check


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lon/lat points."""
    import math
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _scan_with_lambda_fanout(
    geohashes: list[str],
    bbox: tuple[float, float, float, float],
    year1: int, month1: int,
    year2: int, month2: int,
    top_n: int,
    min_change_score: float,
    start_time: float,
    min_separation_km: float = 0.0,
    min_neighbors: int = 0,
) -> dict:
    """Fan out partition queries to Lambda functions for parallel execution.

    Each Lambda handles one geohash partition: queries both periods,
    computes cosine similarity, returns cells above threshold.
    """
    import boto3
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    west, south, east, north = bbox
    # The lgnd-partition-query Lambda is deployed in the LGND data region
    # (us-west-2) so its parquet reads are local. Configurable via env.
    lambda_region = os.environ.get('LGND_LAMBDA_REGION', 'us-west-2')
    lambda_client = boto3.client('lambda', region_name=lambda_region)

    print(f"🚀 Scanning {len(geohashes)} partitions via Lambda fan-out (parallel)...")

    def invoke_partition(gh):
        import json as _json
        payload = {
            'geohash': gh,
            'year1': year1, 'month1': month1,
            'year2': year2, 'month2': month2,
            'bbox': {'west': west, 'south': south, 'east': east, 'north': north},
            'min_change_score': min_change_score,
        }
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Payload=_json.dumps(payload).encode(),
        )
        return _json.loads(response['Payload'].read())

    # Invoke all partitions in parallel
    all_changed_cells = []
    total_matched = 0
    total_d1 = 0
    total_d2 = 0
    errors = []

    with ThreadPoolExecutor(max_workers=min(len(geohashes), 8)) as executor:
        futures = {executor.submit(invoke_partition, gh): gh for gh in geohashes}

        completed = 0
        for future in as_completed(futures):
            completed += 1
            gh = futures[future]
            try:
                result = future.result()
                if result.get('errorMessage'):
                    errors.append(f"{gh}: {result['errorMessage'][:100]}")
                else:
                    total_d1 += result.get('total_d1', 0)
                    total_d2 += result.get('total_d2', 0)
                    total_matched += result.get('matched', 0)
                    all_changed_cells.extend(result.get('changed_cells', []))
                    if result.get('error'):
                        errors.append(f"{gh}: {result['error']}")
            except Exception as e:
                errors.append(f"{gh}: {type(e).__name__}: {str(e)[:100]}")

            print(f"🔍 {completed}/{len(geohashes)} partitions done "
                  f"({total_matched:,} matched, {len(all_changed_cells):,} changed)...")

    query_time = time.time() - start_time

    if not all_changed_cells and not total_matched:
        error_msg = f"No data found. Errors: {'; '.join(errors)}" if errors else "No matching cells"
        return {"error": error_msg, "query_time_s": round(query_time, 1)}

    # Sort deterministically. change_score saturates at 1.0 for many cells, so a
    # plain change_score sort leaves ties in non-deterministic Lambda-completion
    # order (hotspots shuffle between runs). Break ties by:
    #   1. change_score  (desc) — primary severity
    #   2. similarity    (asc)  — lower cosine similarity = more semantically
    #                             changed, a finer discriminator once score hits 1.0
    #   3. center lat, lon (asc) — fully stable final tie-break
    def _cell_center(c):
        b = c.get("bbox") or {}
        if isinstance(b, dict):
            return ((b.get("ymin", 0) + b.get("ymax", 0)) / 2.0,
                    (b.get("xmin", 0) + b.get("xmax", 0)) / 2.0)
        return (0.0, 0.0)

    def _sort_key(c):
        lat, lon = _cell_center(c)
        return (-round(c.get("change_score", 0), 4),
                round(c.get("similarity", 1.0), 6),
                round(lat, 5), round(lon, 5))

    all_changed_cells.sort(key=_sort_key)

    # Coherence filter over the full changed-cell set (isolated cells are noise).
    _is_coherent = _build_coherence_check(all_changed_cells, min_neighbors, _cell_center)

    # Land-mask candidates. When thinning or coherence is active we must scan a
    # generous pool (both drop candidates), otherwise the top-N*3 slice is enough.
    use_full_pool = (min_separation_km and min_separation_km > 0) or (min_neighbors and min_neighbors > 0)
    pool = all_changed_cells if use_full_pool else all_changed_cells[:top_n * 3]
    land_cells = []
    ocean_removed = 0
    incoherent_removed = 0
    for r in pool:
        cell_bbox = r.get("bbox")
        if isinstance(cell_bbox, dict):
            cx = (cell_bbox.get("xmin", 0) + cell_bbox.get("xmax", 0)) / 2
            cy = (cell_bbox.get("ymin", 0) + cell_bbox.get("ymax", 0)) / 2
            if not _is_on_land(cx, cy):
                ocean_removed += 1
                continue
        if not _is_coherent(r):
            incoherent_removed += 1
            continue
        land_cells.append(r)
        # Fast path: stop early when not thinning and we have enough land cells.
        if not (min_separation_km and min_separation_km > 0) and len(land_cells) >= top_n:
            break

    if ocean_removed > 0:
        print(f"🌊 Filtered {ocean_removed} ocean/water cells")
    if incoherent_removed > 0:
        print(f"🔎 Filtered {incoherent_removed} isolated (low-coherence) cells")

    # Optional spatial thinning: keep hotspots at least `min_separation_km` apart
    # so the top-N are geographically DISTINCT events instead of a cluster of
    # adjacent 1.28km cells (all saturating at change_score 1.0). Greedy over the
    # deterministically-sorted list, so the strongest cell in each area wins.
    if min_separation_km and min_separation_km > 0:
        thinned, kept_centers = [], []
        for r in land_cells:
            lat, lon = _cell_center(r)
            if all(_haversine_km(lat, lon, klat, klon) >= min_separation_km
                   for klat, klon in kept_centers):
                thinned.append(r)
                kept_centers.append((lat, lon))
            if len(thinned) >= top_n:
                break
        hotspots = thinned[:top_n]
        print(f"📍 Spatial thinning: {len(hotspots)} distinct hotspots ≥{min_separation_km}km apart")
    else:
        hotspots = land_cells[:top_n]

    # Build hotspot list with coordinates
    hotspot_list = []
    for i, h in enumerate(hotspots):
        entry = {
            "rank": i + 1,
            "change_score": round(h.get("change_score", 0), 4),
            "similarity": round(h.get("similarity", 0), 4),
        }
        if h.get("bbox"):
            bbox_data = h["bbox"]
            if isinstance(bbox_data, dict):
                xmin = bbox_data.get("xmin", 0)
                ymin = bbox_data.get("ymin", 0)
                xmax = bbox_data.get("xmax", 0)
                ymax = bbox_data.get("ymax", 0)
                entry["center_lat"] = round((ymin + ymax) / 2, 4)
                entry["center_lon"] = round((xmin + xmax) / 2, 4)
                entry["bbox"] = {
                    "west": round(xmin, 5),
                    "south": round(ymin, 5),
                    "east": round(xmax, 5),
                    "north": round(ymax, 5),
                }
        hotspot_list.append(entry)

    # Build the full "all changed cells" density layer. Land-mask each cell so
    # coastal scans don't show ocean noise, and tag the top-N hotspots (by
    # cell_id) so the frontend can highlight them distinctly. Capped to bound
    # payload size on very large regions (highest-scoring cells kept first).
    MAX_DENSITY_CELLS = 20000
    top_ids = {h.get("cell_id"): i + 1 for i, h in enumerate(hotspots)}
    all_cells_out = []
    for c in all_changed_cells[:MAX_DENSITY_CELLS]:
        bb = c.get("bbox")
        if not isinstance(bb, dict):
            continue
        cx = (bb.get("xmin", 0) + bb.get("xmax", 0)) / 2
        cy = (bb.get("ymin", 0) + bb.get("ymax", 0)) / 2
        if not _is_on_land(cx, cy):
            continue
        all_cells_out.append({
            "change_score": round(c.get("change_score", 0), 4),
            "rank": top_ids.get(c.get("cell_id")),
            "bbox": {
                "west": round(bb.get("xmin", 0), 4),
                "south": round(bb.get("ymin", 0), 4),
                "east": round(bb.get("xmax", 0), 4),
                "north": round(bb.get("ymax", 0), 4),
            },
        })

    # Compute statistics
    all_scores = [c.get("change_score", 0) for c in all_changed_cells]
    total_time = time.time() - start_time

    month_names = ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"]

    result = {
        "summary": {
            "total_cells_scanned": total_matched,
            "cells_with_change": len(all_changed_cells),
            "ocean_cells_filtered": ocean_removed,
            "cells_no_change": total_matched - len(all_changed_cells),
            "change_percentage": round(100.0 * len(all_changed_cells) / max(total_matched, 1), 1),
            "mean_change_score": round(float(np.mean(all_scores)), 4) if all_scores else 0,
            "max_change_score": round(float(np.max(all_scores)), 4) if all_scores else 0,
            "area_scanned_km2": round(total_matched * 1.28 * 1.28, 1),
            "period1": f"{year1}-{month1:02d} ({month_names[month1]})",
            "period2": f"{year2}-{month2:02d} ({month_names[month2]})",
            "month_used": month1,
            "month_name": month_names[month1],
            "query_time_s": round(query_time, 1),
            "total_time_s": round(total_time, 1),
            "execution_mode": "lambda_fanout",
            "partitions_queried": len(geohashes),
        },
        "hotspots": hotspot_list,
        "_all_cells": all_cells_out,
        "drill_in_recommendation": (
            f"When drilling into hotspots, use imagery from {month_names[month1]} "
            f"(month {month1:02d}) for both dates to match the scan period and avoid "
            f"seasonal false positives. Set get_rasters date to {year1}-{month1:02d}-15 "
            f"and {year2}-{month2:02d}-15."
        ),
    }

    logger.info("LGND Lambda scan complete: %d cells, %d changed, %.1fs",
                total_matched, len(all_changed_cells), total_time)

    return result


def scan_region_change(
    bbox: tuple[float, float, float, float],
    year1: int,
    month1: int,
    year2: int,
    month2: int,
    top_n: int = 20,
    min_change_score: float = 0.15,
    min_separation_km: float = 0.0,
    min_neighbors: int = 0,
) -> dict:
    """Scan a large region for change hotspots using LGND embeddings.

    Automatically adjusts months to peak growing season for the region's
    latitude to avoid seasonal false positives. Filters out ocean cells.

    Args:
        bbox: (west, south, east, north) in EPSG:4326.
        year1: Earlier year.
        month1: Earlier month (1-12) — will be adjusted to peak season.
        year2: Later year.
        month2: Later month (1-12) — will be adjusted to peak season.
        top_n: Number of top hotspots to return.
        min_change_score: Minimum change score to consider (0-1).

    Returns:
        Dict with summary, hotspots, and metadata including actual months used.
    """
    import time

    start_time = time.time()
    west, south, east, north = bbox

    # Determine peak season based on center latitude
    center_lat = (south + north) / 2
    actual_month1 = _get_peak_month(center_lat, month1)
    actual_month2 = _get_peak_month(center_lat, month2)

    # Ensure both periods use the SAME month for fair comparison
    if actual_month1 != actual_month2:
        # Use the month closest to both requests
        actual_month1 = actual_month2 = _get_peak_month(center_lat, (month1 + month2) // 2)

    if actual_month1 != month1 or actual_month2 != month2:
        logger.info("LGND scan: Adjusted months to peak season: %d-%02d→%d-%02d (requested %02d→%02d)",
                    year1, actual_month1, year2, actual_month2, month1, month2)
        print(f"📅 Adjusted to peak season month {actual_month1:02d} for fair comparison "
              f"(avoids seasonal false positives)")

    geohashes = _get_geohashes_for_bbox(west, south, east, north)

    if not geohashes:
        return {"error": "No geohashes found for the given region"}

    logger.info("LGND region scan: bbox=(%.2f,%.2f,%.2f,%.2f), %d-%02d→%d-%02d, %d geohashes",
                west, south, east, north, year1, actual_month1, year2, actual_month2, len(geohashes))

    # All region scans run through the Lambda fan-out (one invocation per geohash
    # partition). DuckDB runs inside the Lambda image, not the agent container, so
    # the agent stays lean and there is a single code path for every region size.
    return _scan_with_lambda_fanout(
        geohashes, bbox, year1, actual_month1, year2, actual_month2,
        top_n, min_change_score, start_time,
        min_separation_km=min_separation_km,
        min_neighbors=min_neighbors,
    )
