"""Per-species confidence thresholds from validated BirdNET detections.

Validated clips were sampled evenly across confidence bins, so the share of
correct clips above a threshold over-weights high scores compared with real
detections, which are mostly low-scoring. Instead we:

1. fit P(correct | score) = sigmoid(a + b * logit(score)) on the validations,
   which does not depend on how many clips were checked at each score;
2. in each 0.05-wide score bin, pull that curve towards the observed share of
   correct clips, weighted n / (n + SHRINKAGE): well-validated bins follow the
   data (e.g. accuracy levelling off below 1 at high scores), sparse bins keep
   the smooth curve;
3. weight the curve by the scores of *all* detections of the species to get the
   expected precision of everything kept above a threshold t;
4. pick the lowest t whose expected precision reaches the target; if it is never
   reached, the lowest t within TOLERANCE of the best precision (if that is at
   least the fallback level).

When the fit is not usable (no increase of correctness with score, or no
convergence), the plain share of correct validations above t is used instead.
Validations scoring below MIN_SCORE are ignored: BirdNET reports no detections
there, so they say nothing about the detections being counted.
"""

import itertools
import math

import numpy as np

MIN_SCORE = 0.10
GRID = np.round(np.arange(MIN_SCORE, 1.0, 0.01), 2)
OBSERVED_BIN = 0.05
EDGES = np.round(np.arange(0.10, 1.0 + 1e-9, OBSERVED_BIN), 2)
SHRINKAGE = 20
TOLERANCE = 0.02
EPS = 1e-4


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1 / (1 + np.exp(-z))


def fit_logistic(score, correct, ridge: float = 1e-2, iterations: int = 50):
    """(intercept, slope) of correct ~ logit(score), or None if unusable.

    Newton-Raphson with a small ridge penalty so that perfectly separated data
    (e.g. every validation correct) still gives finite coefficients.
    """
    x = logit(score)
    y = np.asarray(correct, dtype=float)
    if len(y) == 0:
        return None
    design = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    penalty = ridge * np.eye(2)
    for _ in range(iterations):
        p = sigmoid(design @ beta)
        w = p * (1 - p)
        gradient = design.T @ (y - p) - penalty @ beta
        hessian = (design * w[:, None]).T @ design + penalty
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return None
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    else:
        return None
    intercept, slope = float(beta[0]), float(beta[1])
    if not (math.isfinite(intercept) and math.isfinite(slope)) or slope <= 0:
        return None
    return intercept, slope


def bin_index(score):
    """Index of the 0.05-wide score bin (scores of 1.0 join the last bin)."""
    idx = np.floor((np.asarray(score, dtype=float) - EDGES[0]) / OBSERVED_BIN + 1e-9)
    return np.clip(idx.astype(int), 0, len(EDGES) - 2)


def bin_adjustment(score, correct, intercept, slope, shrinkage=SHRINKAGE):
    """Per-bin shift of the logistic curve towards the observed accuracy."""
    score = np.asarray(score, dtype=float)
    correct = np.asarray(correct, dtype=float)
    p = sigmoid(intercept + slope * logit(score))
    idx = bin_index(score)
    delta = np.zeros(len(EDGES) - 1)
    for b in range(len(delta)):
        keep = idx == b
        n = keep.sum()
        if n:
            delta[b] = n / (n + shrinkage) * (correct[keep].mean() - p[keep].mean())
    return delta


def adjusted_probability(score, intercept, slope, delta):
    """Logistic curve plus the bin shifts, interpolated between bin centres."""
    centres = (EDGES[:-1] + EDGES[1:]) / 2
    shift = np.interp(np.asarray(score, dtype=float), centres, delta)
    return np.clip(sigmoid(intercept + slope * logit(score)) + shift, 0.0, 1.0)


def expected_precision(p_correct, bin_centres, bin_counts, grid=GRID):
    """Expected share of correct detections among all detections scoring >= t."""
    bin_centres = np.asarray(bin_centres, dtype=float)
    counts = np.asarray(bin_counts, dtype=float)
    out = np.full(len(grid), np.nan)
    for i, t in enumerate(grid):
        keep = bin_centres >= t
        n = counts[keep].sum()
        if n > 0:
            out[i] = float((counts[keep] * p_correct[keep]).sum() / n)
    return out


def naive_precision(score, correct, grid=GRID):
    """Share of correct validations among validations scoring >= t."""
    score = np.asarray(score, dtype=float)
    correct = np.asarray(correct, dtype=float)
    out = np.full(len(grid), np.nan)
    for i, t in enumerate(grid):
        keep = score >= t
        if keep.any():
            out[i] = float(correct[keep].mean())
    return out


def choose_threshold(
    precision, n_validated_above, target, fallback, min_above, grid=GRID
):
    """(threshold, precision, status) from a precision curve over the grid."""
    ok = ~np.isnan(precision) & (np.asarray(n_validated_above) >= min_above)
    strict = np.flatnonzero(ok & (precision >= target))
    if len(strict):
        i = strict[0]
        return float(grid[i]), float(precision[i]), "calibrated"
    loose = np.flatnonzero(ok & (precision >= fallback))
    if len(loose):
        # The lowest threshold within TOLERANCE of the best precision: raising the
        # threshold further would discard detections for almost no gain.
        best = precision[loose].max()
        i = loose[np.argmax(precision[loose] >= best - TOLERANCE)]
        return float(grid[i]), float(precision[i]), "calibrated_low"
    return None, None, "unreliable"


def calibrate_species(
    score,
    correct,
    hist_scores,
    hist_counts,
    *,
    target: float,
    fallback: float,
    min_above: int,
):
    """Threshold, status, method and the curves needed to draw the calibration."""
    score = np.asarray(score, dtype=float)
    correct = np.asarray(correct, dtype=bool)
    relevant = score >= MIN_SCORE
    score, correct = score[relevant], correct[relevant]
    hist_scores = np.asarray(hist_scores, dtype=float)
    hist_counts = np.asarray(hist_counts, dtype=float)
    if hist_counts.sum() == 0:
        # No detections to weight by: fall back to the validations' own scores.
        hist_scores, hist_counts = score, np.ones_like(score)

    n_above = np.array([(score >= t).sum() for t in GRID])
    naive = naive_precision(score, correct)
    fit = fit_logistic(score, correct)
    if fit is not None:
        intercept, slope = fit
        delta = bin_adjustment(score, correct, intercept, slope)
        precision = expected_precision(
            adjusted_probability(hist_scores, intercept, slope, delta),
            hist_scores,
            hist_counts,
        )
        p_logistic = sigmoid(intercept + slope * logit(GRID))
        p_fit = adjusted_probability(GRID, intercept, slope, delta)
        method = "logistic"
    else:
        intercept = slope = None
        precision = naive
        p_logistic = p_fit = np.full(len(GRID), np.nan)
        method = "empirical"

    threshold, at_threshold, status = choose_threshold(
        precision, n_above, target, fallback, min_above
    )
    detections_above = np.array([hist_counts[hist_scores >= t].sum() for t in GRID])

    observed = []
    idx = bin_index(score)
    for b, (lo, hi) in enumerate(itertools.pairwise(EDGES)):
        keep = idx == b
        if keep.any():
            observed.append(
                {
                    "confidence": round(float((lo + hi) / 2), 3),
                    "n_validated": int(keep.sum()),
                    "n_correct": int(correct[keep].sum()),
                }
            )

    curve = [
        {
            "confidence": float(t),
            "p_correct": _num(p_fit[i]),
            "p_logistic": _num(p_logistic[i]),
            "precision_above": _num(precision[i]),
            "naive_precision_above": _num(naive[i]),
            "n_validated_above": int(n_above[i]),
            "n_detections_above": int(detections_above[i]),
        }
        for i, t in enumerate(GRID)
    ]
    return {
        "threshold": threshold,
        "precision_at_threshold": at_threshold,
        "status": status,
        "method": method,
        "intercept": intercept,
        "slope": slope,
        "curve": curve,
        "observed": observed,
    }


def _num(value):
    return None if value is None or np.isnan(value) else float(value)
