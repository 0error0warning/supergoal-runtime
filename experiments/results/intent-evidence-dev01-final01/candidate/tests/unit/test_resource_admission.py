import pytest

from experiments.public_benchmarks.resource_pool import fits, reservation


def test_review_and_heavy_tasks_reserve_real_peak_resources():
    heavy = {"resources": {"cpus": 4, "memory_mb": 8192}}
    assert reservation(heavy, "sg_v2") == {"cpus": 5, "memory_mb": 16384}
    assert reservation(heavy, "native") == {"cpus": 4, "memory_mb": 8192}
    assert not fits([{"cpus": 2, "memory_mb": 4096}], reservation(heavy, "sg_v2"))
    assert fits([{"cpus": 1, "memory_mb": 4096}], reservation(heavy, "sg_v2"))


def test_memory_and_worker_caps_are_independent_of_cpu():
    assert not fits([{"cpus": 1, "memory_mb": 48000}], {"cpus": 1, "memory_mb": 2048})
    assert not fits([{"cpus": 0.5, "memory_mb": 1024}] * 6, {"cpus": 1, "memory_mb": 1024})
    with pytest.raises(ValueError):
        fits([], {"cpus": 0, "memory_mb": 1024})
