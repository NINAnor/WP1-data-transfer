"""Per-species confidence thresholds from validated BirdNET detections.

Following the BirdNET guidelines (Wood & Kahl 2024), we model the probability
that a detection is correct as a function of its confidence score and take the
lowest score at which that probability reaches the target (90%):

    P(correct | score) = u * sigmoid(a + b * logit(score))

u = 1 is the usual logistic regression. Some species never get much above, say,
95% correct even at the highest scores; forcing u = 1 then flattens the curve and
makes it too optimistic at low scores, so a ceiling u < 1 is also fitted and kept
when it predicts held-out checks better (5-fold cross-validation).

Validated clips were sampled evenly across confidence bins. That does not bias
P(correct | score), which is conditional on the score. Checks scoring below
MIN_SCORE are ignored: BirdNET reports no detections there.

Uncertainty comes from refitting on bootstrap resamples of the checks: a 95% band
for the curve and a range for the threshold.
"""

import math

import numpy as np

MIN_SCORE = 0.10
GRID = np.round(np.arange(MIN_SCORE, 1.0, 0.01), 2)
EDGES = np.round(np.arange(MIN_SCORE, 1.0 + 1e-9, 0.05), 2)
EPS = 1e-6
RIDGE = 1e-2
U_RANGE = (0.5, 1.0)
FOLDS = 5
BOOTSTRAP = 200
SEED = 20261113


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -35, 35)))


def predict(params, score):
    a, b, u = params
    return u * sigmoid(a + b * logit(score))


def _fit_fixed_u(x, y, u, start=None, iterations=60):
    """(a, b, log-likelihood) for a fixed ceiling u, by Fisher scoring."""
    design = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2) if start is None else np.array(start, dtype=float)
    penalty = RIDGE * np.eye(2)
    for _ in range(iterations):
        s = sigmoid(design @ beta)
        p = np.clip(u * s, EPS, 1 - EPS)
        score_z = (1 - s) * (y - p) / (1 - p)
        info_w = p * (1 - s) ** 2 / (1 - p)
        gradient = design.T @ score_z - penalty @ beta
        info = (design * info_w[:, None]).T @ design + penalty
        try:
            step = np.linalg.solve(info, gradient)
        except np.linalg.LinAlgError:
            return None
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    p = np.clip(u * sigmoid(design @ beta), EPS, 1 - EPS)
    loglik = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))
    if not np.all(np.isfinite(beta)) or not math.isfinite(loglik):
        return None
    return float(beta[0]), float(beta[1]), loglik


def fit(score, correct, ceiling: bool):
    """(a, b, u) maximising the likelihood, or None if correctness does not rise
    with the score."""
    x = logit(score)
    y = np.asarray(correct, dtype=float)
    if len(y) == 0:
        return None
    if not ceiling:
        result = _fit_fixed_u(x, y, 1.0)
        best = None if result is None else (result[0], result[1], 1.0)
    else:
        # Golden-section search on the profile likelihood of u.
        lo, hi = U_RANGE
        ratio = (math.sqrt(5) - 1) / 2
        cache = {}

        def profile(u):
            if u not in cache:
                cache[u] = _fit_fixed_u(x, y, u)
            r = cache[u]
            return -math.inf if r is None else r[2]

        c, d = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
        for _ in range(30):
            if profile(c) > profile(d):
                hi = d
            else:
                lo = c
            c, d = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
        u = max(cache, key=lambda k: profile(k))
        if profile(1.0) >= profile(u):
            u = 1.0
        r = cache[u]
        best = None if r is None else (r[0], r[1], u)
    if best is None or best[1] <= 0:
        return None
    return best


def _log_loss(params, score, correct):
    p = np.clip(predict(params, score), EPS, 1 - EPS)
    y = np.asarray(correct, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def cross_validated_loss(score, correct, ceiling: bool, rng) -> float:
    folds = rng.permutation(len(score)) % FOLDS
    losses = []
    for k in range(FOLDS):
        train, test = folds != k, folds == k
        params = fit(score[train], correct[train], ceiling)
        if params is None:
            return math.inf
        losses.append(_log_loss(params, score[test], correct[test]) * test.sum())
    return sum(losses) / len(score)


def threshold_for(curve, n_above, level, min_above):
    """Lowest grid score where the curve reaches `level` with enough checks above."""
    ok = (curve >= level) & (n_above >= min_above)
    return float(GRID[np.argmax(ok)]) if ok.any() else None


def wilson(k, n, z=1.96):
    """95% Wilson interval for k successes out of n."""
    if n == 0:
        return None, None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def calibrate_species(
    score,
    correct,
    hist_scores,
    hist_counts,
    *,
    target: float,
    fallback: float,
    min_above: int,
    bootstrap: int = BOOTSTRAP,
):
    """Threshold, status, model and everything needed to draw the calibration."""
    rng = np.random.default_rng(SEED)
    score = np.asarray(score, dtype=float)
    correct = np.asarray(correct, dtype=bool)
    relevant = score >= MIN_SCORE
    score, correct = score[relevant], correct[relevant]
    hist_scores = np.asarray(hist_scores, dtype=float)
    hist_counts = np.asarray(hist_counts, dtype=float)

    n_above = np.array([(score >= t).sum() for t in GRID])
    detections_above = np.array([hist_counts[hist_scores >= t].sum() for t in GRID])

    loss_plain = cross_validated_loss(score, correct, False, rng)
    loss_ceiling = cross_validated_loss(score, correct, True, rng)
    ceiling = loss_ceiling < loss_plain
    params = fit(score, correct, ceiling)

    curve = lower = upper = np.full(len(GRID), np.nan)
    threshold = threshold_lo = threshold_hi = at_threshold = None
    status, model = "unreliable", "no_fit"
    if params is not None:
        model = "logistic_ceiling" if params[2] < 0.995 else "logistic"
        curve = predict(params, GRID)
        reaches_target = threshold_for(curve, n_above, target, min_above) is not None
        level = target if reaches_target else fallback
        threshold = threshold_for(curve, n_above, level, min_above)
        if threshold is not None:
            status = "calibrated" if level == target else "calibrated_low"
            at_threshold = float(predict(params, threshold))

        boot_curves, boot_thresholds = [], []
        for _ in range(bootstrap):
            idx = rng.integers(0, len(score), len(score))
            p = fit(score[idx], correct[idx], ceiling)
            if p is None:
                continue
            c = predict(p, GRID)
            boot_curves.append(c)
            t = threshold_for(c, n_above, level, min_above)
            boot_thresholds.append(np.inf if t is None else t)
        if boot_curves:
            lower, upper = np.percentile(np.array(boot_curves), [2.5, 97.5], axis=0)
        if threshold is not None and boot_thresholds:
            lo, hi = np.percentile(np.array(boot_thresholds), [2.5, 97.5], method="nearest")
            threshold_lo = round(float(lo), 2) if np.isfinite(lo) else None
            threshold_hi = round(float(hi), 2) if np.isfinite(hi) else None

    observed = []
    idx = np.clip(
        np.floor((score - EDGES[0]) / 0.05 + 1e-9).astype(int), 0, len(EDGES) - 2
    )
    for b in range(len(EDGES) - 1):
        keep = idx == b
        n, k = int(keep.sum()), int(correct[keep].sum())
        if n:
            lo, hi = wilson(k, n)
            observed.append(
                {
                    "confidence": round(float(EDGES[b] + 0.025), 3),
                    "n_validated": n,
                    "n_correct": k,
                    "p_lower": lo,
                    "p_upper": hi,
                }
            )

    return {
        "threshold": threshold,
        "threshold_lower": threshold_lo,
        "threshold_upper": threshold_hi,
        "p_at_threshold": at_threshold,
        "status": status,
        "model": model,
        "params": params,
        "cv_loss": {"logistic": loss_plain, "logistic_ceiling": loss_ceiling},
        "curve": [
            {
                "confidence": float(t),
                "p_correct": _num(curve[i]),
                "p_lower": _num(lower[i]),
                "p_upper": _num(upper[i]),
                "n_validated_above": int(n_above[i]),
                "n_detections_above": int(detections_above[i]),
            }
            for i, t in enumerate(GRID)
        ],
        "observed": observed,
    }


def _num(value):
    return None if value is None or np.isnan(value) else float(value)
