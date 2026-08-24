import numpy as np

from riskpilot.monitoring import drift_status, population_stability_index


def test_psi_detects_shift_and_maps_boundary():
    rng = np.random.default_rng(4)
    reference = rng.normal(0, 1, 5000)
    stable = population_stability_index(reference, rng.normal(0, 1, 5000))
    shifted = population_stability_index(reference, rng.normal(2, 1, 5000))
    assert stable < shifted
    assert drift_status(0.05) == "stable"
    assert drift_status(0.15) == "watch"
    assert drift_status(0.30) == "pause"


def test_psi_handles_binary_features_instead_of_returning_zero():
    reference = np.array([0] * 900 + [1] * 100)
    current = np.array([0] * 500 + [1] * 500)
    assert population_stability_index(reference, current) > 0.25
