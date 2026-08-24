import numpy as np

from build_sentinelgraph import moving_block_sample


def test_moving_block_sample_preserves_local_adjacency():
    sample = moving_block_sample(4096, np.random.default_rng(42), block_size=128)
    assert len(sample) == 4096
    # Every sampled block is chronological internally; only block boundaries jump.
    assert np.mean(np.diff(sample) == 1) > 0.98
    assert sample.min() >= 0
    assert sample.max() < 4096
