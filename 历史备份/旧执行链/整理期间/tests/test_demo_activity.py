"""Media evidence rejects low-density, phase-separated and reset activity."""
import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('demo_activity_metrics',Path(__file__).resolve().parents[1]/'demo/mixed_activity.py')
metrics=importlib.util.module_from_spec(spec);spec.loader.exec_module(metrics)
mixed=metrics


def test_density_needs_many_concurrent_initiators():
    samples = [
        {
            "at": i / 5,
            "delegation": i * 2,
            "p2p": i * 2,
            "activeLines": 5,
            "initiators": [str((i + j) % 36) for j in range(5)],
        }
        for i in range(151)
    ]
    assert metrics.density(samples)["passed"]
    one = [{**s, "initiators": ["only-one"]} for s in samples]
    assert not metrics.density(one)["passed"]
    quiet = [
        {**s, "delegation": i // 20, "p2p": i // 20} for i, s in enumerate(samples)
    ]
    assert not metrics.density(quiet)["passed"]


def test_equal_totals_do_not_hide_separated_phases():
    separated = [
        {"at": 0, "delegation": 0, "p2p": 0},
        {"at": 10, "delegation": 10, "p2p": 0},
        {"at": 20, "delegation": 10, "p2p": 10},
    ]
    proof = mixed.summarize(separated)
    assert proof["delegationShare"] == 0.5 and not proof["passed"]
    assert proof["mixedShare"] == 0
    concurrent = [{"at": i, "delegation": i * 2, "p2p": i * 2} for i in range(21)]
    assert mixed.summarize(concurrent)["passed"]


def test_counter_reset_is_not_accepted():
    with pytest.raises(ValueError, match="reset"):
        mixed.summarize(
            [
                {"at": 0, "delegation": 3, "p2p": 1},
                {"at": 10, "delegation": 0, "p2p": 4},
            ]
        )
