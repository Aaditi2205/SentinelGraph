"""Distribution drift metrics with an explicit automation boundary."""

from __future__ import annotations

import numpy as np


def population_stability_index(reference, current, bins: int = 10) -> float:
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    if not len(reference) or not len(current):
        return 0.0
    categories = np.unique(np.concatenate([reference, current]))
    if len(categories) <= bins:
        ref = np.array([(reference == value).mean() for value in categories])
        cur = np.array([(current == value).mean() for value in categories])
        ref, cur = np.clip(ref, 1e-6, None), np.clip(cur, 1e-6, None)
        return float(np.sum((cur - ref) * np.log(cur / ref)))
    edges = np.unique(np.quantile(reference, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    ref = np.histogram(reference, edges)[0] / len(reference)
    cur = np.histogram(current, edges)[0] / len(current)
    ref, cur = np.clip(ref, 1e-6, None), np.clip(cur, 1e-6, None)
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def drift_status(max_psi: float) -> str:
    if max_psi >= 0.25:
        return "pause"
    if max_psi >= 0.10:
        return "watch"
    return "stable"
