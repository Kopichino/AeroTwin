"""Tests for the sensor catalogue and module attribution matrix (Doc 08 section 8.5)."""

from __future__ import annotations

import pytest

from at_core.domain.enums import EngineModule, Subset
from at_core.domain.sensors import (
    ATTRIBUTION_MATRIX,
    MODULE_CRITICALITY,
    N_SENSORS,
    SENSOR_BY_KEY,
    SENSOR_BY_SYMBOL,
    SENSOR_SPECS,
    TRACKED_MODULES,
    attribute_to_modules,
    dominant_module,
    informative_sensors,
)


def test_catalogue_has_all_21_sensors() -> None:
    assert len(SENSOR_SPECS) == N_SENSORS
    assert [spec.index for spec in SENSOR_SPECS] == list(range(1, 22))


def test_sensor_keys_are_unique_and_well_formed() -> None:
    keys = [spec.key for spec in SENSOR_SPECS]
    assert len(set(keys)) == N_SENSORS
    assert SENSOR_BY_KEY["s3"].symbol == "T30"
    assert SENSOR_BY_SYMBOL["T30"].key == "s3"


def test_known_physical_mappings() -> None:
    assert SENSOR_BY_KEY["s3"].primary_module is EngineModule.HPC
    assert SENSOR_BY_KEY["s8"].symbol == "Nf"  # drives the 3D fan animation
    assert SENSOR_BY_KEY["s20"].primary_module is EngineModule.HPT


@pytest.mark.parametrize("key", list(ATTRIBUTION_MATRIX.keys()))
def test_attribution_rows_sum_to_one(key: str) -> None:
    total = sum(ATTRIBUTION_MATRIX[key].values())
    assert total == pytest.approx(1.0), f"row {key} sums to {total}"


def test_criticality_weights_sum_to_one() -> None:
    assert sum(MODULE_CRITICALITY.values()) == pytest.approx(1.0)


def test_criticality_covers_every_tracked_module() -> None:
    assert set(MODULE_CRITICALITY) == set(TRACKED_MODULES)


def test_single_regime_subsets_drop_constant_sensors() -> None:
    fd001 = informative_sensors(Subset.FD001)
    assert len(fd001) == 14
    assert "s1" not in fd001
    assert "s3" in fd001


def test_multi_regime_subsets_keep_all_sensors() -> None:
    assert len(informative_sensors(Subset.FD002)) == 21
    assert len(informative_sensors(Subset.FD004)) == 21


def test_attribution_concentrates_hpc_signals_on_hpc() -> None:
    """T30, P30, Ps30 rising together is the textbook HPC degradation signature."""
    result = attribute_to_modules({"s3": 3.0, "s7": 2.5, "s11": 2.8})
    assert max(result, key=lambda module: result[module]) is EngineModule.HPC


def test_dominant_module_identifies_hpt_cooling_drift() -> None:
    assert dominant_module({"s20": 4.0}) is EngineModule.HPT


def test_dominant_module_of_empty_input_is_none() -> None:
    assert dominant_module({}) is None


def test_unknown_sensor_keys_are_ignored() -> None:
    assert attribute_to_modules({"not_a_sensor": 5.0}) == {}


def test_sensor_without_explicit_row_falls_back_to_primary_module() -> None:
    """s18 has no attribution row; it must still attribute to CONTROL."""
    result = attribute_to_modules({"s18": 2.0})
    assert result == {EngineModule.CONTROL: 2.0}


def test_attribution_scales_linearly_with_magnitude() -> None:
    single = attribute_to_modules({"s3": 1.0})
    double = attribute_to_modules({"s3": 2.0})
    for module, value in single.items():
        assert double[module] == pytest.approx(value * 2.0)
