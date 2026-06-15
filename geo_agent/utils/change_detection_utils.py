"""
Pixel-level change detection math for the ``run_change_detection`` tool.

This module provides the two actively used change-detection tiers:

Tier 1 (spectral index deltas + weighted composite): Computes per-pixel change
scores by differencing normalized spectral indices (NDVI, NDWI, NBR, BSI)
between two dates, combines them into a single weighted composite change score,
and classifies that score into discrete severity levels.

Tier 2 (iMAD / IR-MAD): Iteratively Reweighted Multivariate Alteration Detection
— a statistically rigorous approach that uses canonical correlation analysis
across all bands simultaneously to produce a per-pixel chi-squared change
statistic, which is then normalized into a [0, 1] change score.

The score arrays produced here are written to Cloud Optimized GeoTIFFs by the
caller (``tools.run_change_detection``) for TiTiler rendering.
"""
import logging
import os
import tempfile

import numpy as np
import rasterio
from scipy.linalg import inv, sqrtm
from scipy.stats import chi2 as chi2_dist

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Change classification thresholds
# ---------------------------------------------------------------------------

# Composite change score thresholds (0-1 scale after normalization)
CHANGE_THRESHOLDS = {
    "no_change": 0.05,       # < 5% composite delta = no meaningful change
    "low_change": 0.15,      # 5-15% = low / possible change
    "moderate_change": 0.30, # 15-30% = moderate change
    # > 30% = high change
}

# Per-index weights for composite score
INDEX_WEIGHTS = {
    "NDVI": 0.35,  # Vegetation loss — strongest signal for deforestation
    "NDWI": 0.15,  # Water changes (construction near water, flooding)
    "NBR": 0.15,   # Burn / bare soil exposure
    "BSI": 0.35,   # Bare Soil Index — strongest signal for construction/clearing
}


def compute_index_delta(band1_date1: np.ndarray, band2_date1: np.ndarray,
                        band1_date2: np.ndarray, band2_date2: np.ndarray,
                        index_type: str) -> np.ndarray:
    """Compute the absolute difference of a normalized spectral index between two dates.

    Args:
        band1_date1: First band array for date 1.
        band2_date1: Second band array for date 1.
        band1_date2: First band array for date 2.
        band2_date2: Second band array for date 2.
        index_type: One of NDVI, NDWI, NBR, BSI.

    Returns:
        2-D float32 array of absolute index difference, range [0, 2].
    """
    eps = 1e-10

    if index_type == "NDVI":
        # (NIR - Red) / (NIR + Red)
        idx1 = (band2_date1.astype(float) - band1_date1.astype(float)) / (band2_date1 + band1_date1 + eps)
        idx2 = (band2_date2.astype(float) - band1_date2.astype(float)) / (band2_date2 + band1_date2 + eps)
    elif index_type == "NDWI":
        # (Green - NIR) / (Green + NIR)
        idx1 = (band1_date1.astype(float) - band2_date1.astype(float)) / (band1_date1 + band2_date1 + eps)
        idx2 = (band1_date2.astype(float) - band2_date2.astype(float)) / (band1_date2 + band2_date2 + eps)
    elif index_type == "NBR":
        # (NIR08 - SWIR2) / (NIR08 + SWIR2)
        idx1 = (band1_date1.astype(float) - band2_date1.astype(float)) / (band1_date1 + band2_date1 + eps)
        idx2 = (band1_date2.astype(float) - band2_date2.astype(float)) / (band1_date2 + band2_date2 + eps)
    elif index_type == "BSI":
        # Bare Soil Index is a 4-band index (Red, SWIR2, NIR, Blue) and cannot be
        # expressed through this 2-band interface. Use compute_bsi() for a single
        # date or compute_bsi_delta() for the between-date difference instead.
        raise ValueError("BSI uses compute_bsi() / compute_bsi_delta() directly, not compute_index_delta()")
    else:
        raise ValueError(f"Unknown index_type: {index_type}")

    delta = np.abs(idx2 - idx1)
    return delta.astype(np.float32)


def compute_bsi(red: np.ndarray, swir2: np.ndarray, nir: np.ndarray, blue: np.ndarray) -> np.ndarray:
    """Compute the Bare Soil Index for a single date.

    BSI = (Red + SWIR2 - NIR - Blue) / (Red + SWIR2 + NIR + Blue)
    Range: [-1, 1], higher values = more bare soil/impervious surface.

    Args:
        red: Red band (B04, 10m).
        swir2: SWIR2 band (B12, 20m — must be resampled to 10m).
        nir: NIR band (B08, 10m).
        blue: Blue band (B02, 10m).

    Returns:
        2-D float32 array of BSI values [-1, 1].
    """
    eps = 1e-10
    r = red.astype(float)
    s = swir2.astype(float)
    n = nir.astype(float)
    b = blue.astype(float)
    bsi = (r + s - n - b) / (r + s + n + b + eps)
    return bsi.astype(np.float32)


def compute_bsi_delta(red1: np.ndarray, swir2_1: np.ndarray, nir1: np.ndarray, blue1: np.ndarray,
                      red2: np.ndarray, swir2_2: np.ndarray, nir2: np.ndarray, blue2: np.ndarray) -> np.ndarray:
    """Compute absolute BSI difference between two dates.

    Returns:
        2-D float32 array of absolute BSI difference [0, 2].
    """
    bsi1 = compute_bsi(red1, swir2_1, nir1, blue1)
    bsi2 = compute_bsi(red2, swir2_2, nir2, blue2)
    return np.abs(bsi2 - bsi1).astype(np.float32)


def compute_composite_change_score(deltas: dict[str, np.ndarray]) -> np.ndarray:
    """Combine per-index deltas into a single weighted composite change score.

    Args:
        deltas: Mapping of index name → absolute delta array (all same shape).

    Returns:
        2-D float32 array normalized to [0, 1] where 1 = maximum change.
    """
    # Start with zeros matching the shape of any delta
    first = next(iter(deltas.values()))
    composite = np.zeros_like(first, dtype=np.float32)
    total_weight = 0.0

    for index_name, delta in deltas.items():
        weight = INDEX_WEIGHTS.get(index_name, 1.0 / len(deltas))
        composite += weight * delta
        total_weight += weight

    if total_weight > 0:
        composite /= total_weight

    # Normalize to [0, 1] — theoretical max delta is 2 (index range -1 to 1)
    # but in practice deltas > 1.0 are rare; clip at 1.0
    composite = np.clip(composite, 0.0, 1.0)

    return composite


def classify_change(composite: np.ndarray) -> np.ndarray:
    """Classify composite change score into discrete severity levels.

    Returns:
        uint8 array with values:
            1 = no change (green)
            2 = low change (yellow-green)
            3 = moderate change (orange)
            4 = high change (red)
    """
    classified = np.ones_like(composite, dtype=np.uint8)  # default: no change

    classified[composite >= CHANGE_THRESHOLDS["no_change"]] = 2      # low
    classified[composite >= CHANGE_THRESHOLDS["low_change"]] = 3     # moderate
    classified[composite >= CHANGE_THRESHOLDS["moderate_change"]] = 4 # high

    return classified


def compute_adaptive_thresholds(composite: np.ndarray) -> dict:
    """Compute percentile-based thresholds from the actual composite data.

    Instead of fixed cutoffs, thresholds auto-calibrate to the AOI:
        - No change: below 75th percentile
        - Low change: 75th to 90th percentile
        - Moderate change: 90th to 97th percentile
        - High change: above 97th percentile

    Falls back to fixed thresholds if the data is too uniform.

    Args:
        composite: 2-D float32 array of composite change scores [0, 1].

    Returns:
        Dict with threshold values for no_change, low_change, moderate_change.
    """
    valid = composite[~np.isnan(composite) & ~np.isinf(composite) & (composite > 0)]

    if len(valid) < 100:
        # Too few pixels — fall back to fixed thresholds
        return CHANGE_THRESHOLDS

    p75 = float(np.percentile(valid, 75))
    p90 = float(np.percentile(valid, 90))
    p97 = float(np.percentile(valid, 97))

    # Ensure minimum separation between thresholds
    if p90 - p75 < 0.01:
        return CHANGE_THRESHOLDS
    if p97 - p90 < 0.01:
        p97 = p90 + 0.01

    return {
        "no_change": p75,
        "low_change": p90,
        "moderate_change": p97,
    }


def compute_change_statistics(composite: np.ndarray, pixel_area_m2: float,
                              adaptive: bool = False) -> dict:
    """Compute area and percentage statistics from a composite change score.

    Args:
        composite: 2-D float32 array of composite change scores [0, 1].
        pixel_area_m2: Area of a single pixel in square meters.
        adaptive: If True, use percentile-based thresholds. If False, use fixed.

    Returns:
        Dict with per-class pixel counts, areas, and percentages.
    """
    valid = composite[~np.isnan(composite) & ~np.isinf(composite)]
    total_pixels = len(valid)

    if total_pixels == 0:
        return {
            "total_pixels": 0,
            "no_change_pct": 0, "no_change_area_m2": 0,
            "low_change_pct": 0, "low_change_area_m2": 0,
            "moderate_change_pct": 0, "moderate_change_area_m2": 0,
            "high_change_pct": 0, "high_change_area_m2": 0,
            "mean_change_score": 0, "max_change_score": 0,
        }

    thresholds = compute_adaptive_thresholds(composite) if adaptive else CHANGE_THRESHOLDS

    no_change = int(np.sum(valid < thresholds["no_change"]))
    low = int(np.sum((valid >= thresholds["no_change"]) & (valid < thresholds["low_change"])))
    moderate = int(np.sum((valid >= thresholds["low_change"]) & (valid < thresholds["moderate_change"])))
    high = int(np.sum(valid >= thresholds["moderate_change"]))

    return {
        "total_pixels": total_pixels,
        "no_change_pct": round(no_change / total_pixels * 100, 1),
        "no_change_area_m2": round(no_change * pixel_area_m2, 2),
        "low_change_pct": round(low / total_pixels * 100, 1),
        "low_change_area_m2": round(low * pixel_area_m2, 2),
        "moderate_change_pct": round(moderate / total_pixels * 100, 1),
        "moderate_change_area_m2": round(moderate * pixel_area_m2, 2),
        "high_change_pct": round(high / total_pixels * 100, 1),
        "high_change_area_m2": round(high * pixel_area_m2, 2),
        "mean_change_score": round(float(np.mean(valid)), 4),
        "max_change_score": round(float(np.max(valid)), 4),
    }


# ---------------------------------------------------------------------------
# Tier 2: iMAD — Iteratively Reweighted Multivariate Alteration Detection
# ---------------------------------------------------------------------------

def imad(
    image1: np.ndarray,
    image2: np.ndarray,
    max_iter: int = 30,
    tol: float = 1e-3,
) -> tuple[np.ndarray, np.ndarray]:
    """Run the iMAD algorithm on two co-registered multi-band images.

    Uses SVD-based canonical correlation analysis to find the linear
    combinations of bands that maximize correlation between the two images,
    then iteratively down-weights changed pixels to refine the no-change
    background.

    Reference:
        Nielsen, A.A. (2007). The Regularized Iteratively Reweighted MAD Method
        for Change Detection in Multi- and Hyperspectral Data.
        IEEE Transactions on Image Processing, 16(2), 463-478.

    Args:
        image1: Array of shape (bands, H, W) for time 1.
        image2: Array of shape (bands, H, W) for time 2.
        max_iter: Maximum number of iterations.
        tol: Convergence tolerance on canonical correlations.

    Returns:
        Tuple of (chi2_map, p_nochange) where:
            chi2_map: Per-pixel chi-squared statistic, shape (H, W).
            p_nochange: Per-pixel probability of no-change, shape (H, W).
    """
    bands, h, w = image1.shape
    n_pixels = h * w

    x = image1.reshape(bands, n_pixels).astype(np.float64)
    y = image2.reshape(bands, n_pixels).astype(np.float64)

    # Mask out nodata pixels (zeros in all bands)
    valid = (np.sum(np.abs(x), axis=0) > 0) & (np.sum(np.abs(y), axis=0) > 0)
    if valid.sum() < bands * 2:
        logger.warning("iMAD: Too few valid pixels (%d) for %d bands", valid.sum(), bands)
        return np.zeros((h, w)), np.ones((h, w))

    # Initial weights: uniform
    weights = np.ones(n_pixels, dtype=np.float64)
    weights[~valid] = 0.0
    rho_old = np.zeros(bands)

    for iteration in range(max_iter):
        w_sum = weights.sum()
        if w_sum == 0:
            break

        # Weighted means
        wx = (x * weights).sum(axis=1, keepdims=True) / w_sum
        wy = (y * weights).sum(axis=1, keepdims=True) / w_sum

        # Center the data
        xc = x - wx
        yc = y - wy

        # Weighted covariance matrices
        sw = np.sqrt(weights)
        xw = xc * sw
        yw = yc * sw

        sigma_xx = (xw @ xw.T) / w_sum
        sigma_yy = (yw @ yw.T) / w_sum
        sigma_xy = (xw @ yw.T) / w_sum

        # Regularize to avoid singular matrices
        reg = 1e-6 * np.eye(bands)
        sigma_xx += reg
        sigma_yy += reg

        # SVD-based CCA: whiten both sides, then SVD the cross-covariance
        try:
            sxx_inv_sqrt = np.real(inv(sqrtm(sigma_xx)))
            syy_inv_sqrt = np.real(inv(sqrtm(sigma_yy)))
        except np.linalg.LinAlgError:
            logger.warning("iMAD: Singular matrix at iteration %d", iteration)
            break

        T = sxx_inv_sqrt @ sigma_xy @ syy_inv_sqrt
        U, rho, Vt = np.linalg.svd(T)

        # Canonical vectors
        a = sxx_inv_sqrt @ U
        b = syy_inv_sqrt @ Vt.T

        # MAD variates
        mad = a.T @ xc - b.T @ yc  # (bands, pixels)
        var_mad = 2.0 * (1.0 - rho)
        var_mad = np.maximum(var_mad, 1e-10)

        # Chi-squared statistic per pixel
        chi2_pixels = np.sum((mad ** 2) / var_mad[:, None], axis=0)

        # Update weights: probability of no-change
        p_nc = 1.0 - chi2_dist.cdf(chi2_pixels, df=bands)
        weights = p_nc.copy()
        weights[~valid] = 0.0

        # Check convergence
        delta = np.max(np.abs(rho - rho_old))
        rho_old = rho.copy()

        logger.debug("iMAD iteration %d: max_rho_delta=%.6f, correlations=%s",
                      iteration, delta, np.round(rho, 4))

        if delta < tol:
            logger.info("iMAD converged at iteration %d (delta=%.6f)", iteration, delta)
            break

    chi2_map = chi2_pixels.reshape(h, w)
    p_nochange_map = p_nc.reshape(h, w)

    return chi2_map, p_nochange_map


def imad_change_score(
    image1: np.ndarray,
    image2: np.ndarray,
    max_iter: int = 30,
    tol: float = 1e-3,
) -> np.ndarray:
    """Run iMAD and return a normalized change score in [0, 1].

    Uses the chi-squared statistic (proportional to change magnitude) rather
    than p_nochange (which saturates near 0/1 too quickly). The chi2 values
    are normalized using the 95th percentile as the upper bound so that only
    the most extreme changes map to 1.0, matching the sensitivity of Tier 1.

    Args:
        image1: Array of shape (bands, H, W) for time 1.  Raw DN values are
                normalized internally to [0, 1] reflectance.
        image2: Array of shape (bands, H, W) for time 2.
        max_iter: Maximum number of iterations.
        tol: Convergence tolerance.

    Returns:
        2-D float32 array of change scores [0, 1], shape (H, W).
    """
    # Normalize raw DN values to approximate reflectance [0, 1]
    # Sentinel-2 L2A reflectance = DN / 10000
    # Skip if values are already in a small range (e.g. spectral indices in [-1, 1])
    img1 = image1.astype(np.float64)
    img2 = image2.astype(np.float64)
    if img1.max() > 10.0:
        img1 = img1 / 10000.0
    if img2.max() > 10.0:
        img2 = img2 / 10000.0

    chi2_map, _ = imad(img1, img2, max_iter=max_iter, tol=tol)

    # Normalize chi2 to [0, 1] using 95th percentile as upper bound.
    # This prevents a few extreme outliers from compressing the rest of
    # the range, and keeps the output comparable to Tier 1's sensitivity.
    valid = chi2_map[chi2_map > 0]
    if len(valid) == 0:
        return np.zeros_like(chi2_map, dtype=np.float32)

    upper = np.percentile(valid, 95)
    if upper <= 0:
        upper = 1.0

    change_score = chi2_map / upper
    change_score = np.clip(change_score, 0.0, 1.0).astype(np.float32)

    logger.info("iMAD score normalization: chi2 95th pct=%.2f, median=%.4f, "
                "score mean=%.4f, score >0.3 fraction=%.1f%%",
                upper, np.median(valid), np.mean(change_score),
                100.0 * np.mean(change_score > 0.3))

    return change_score
