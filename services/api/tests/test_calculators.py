"""Reference cases for engineering calculators (hand-checked values)."""

import math

import pytest

from autoforge.calc.calculators import CalcError, run


def v(r, k):
    return r["outputs"][k]["value"]


def test_units():
    assert math.isclose(v(run("unit_convert", {"value": 1, "from_unit": "in", "to_unit": "mm"}), "result"), 25.4)
    assert math.isclose(v(run("unit_convert", {"value": 100, "from_unit": "degC", "to_unit": "degF"}), "result"), 212)
    assert math.isclose(v(run("unit_convert", {"value": 1, "from_unit": "kWh", "to_unit": "J"}), "result"), 3.6e6)
    with pytest.raises(CalcError):
        run("unit_convert", {"value": 1, "from_unit": "kg", "to_unit": "mm"})
    with pytest.raises(CalcError):
        run("unit_convert", {"value": -500, "from_unit": "degC", "to_unit": "K"})


def test_shaft_power_and_torsion():
    r = run("shaft_power", {"torque_nm": 300, "speed_rpm": 3000})
    assert math.isclose(v(r, "power"), 300 * 3000 * 2 * math.pi / 60 / 1000)  # 94.25 kW
    r2 = run("shaft_power", {"power_kw": v(r, "power"), "speed_rpm": 3000})
    assert math.isclose(v(r2, "torque"), 300)
    with pytest.raises(CalcError):
        run("shaft_power", {"torque_nm": 1})
    t = run("shaft_torsion", {"torque_nm": 500, "diameter_mm": 30})
    assert math.isclose(v(t, "max_shear_stress"), 16 * 500e3 / (math.pi * 30**3), rel_tol=1e-12)  # 94.3 MPa
    with pytest.raises(CalcError):
        run("shaft_torsion", {"torque_nm": 1, "diameter_mm": 10, "bore_mm": 12})


def test_gears():
    r = run("gear_train", {"teeth": [17, 51, 20, 60], "input_speed_rpm": 3000, "input_torque_nm": 100, "efficiency_per_mesh": 0.98})
    assert math.isclose(v(r, "overall_ratio"), 9.0)
    assert math.isclose(v(r, "output_speed"), 3000 / 9)
    assert math.isclose(v(r, "output_torque"), 900 * 0.98**2)
    with pytest.raises(CalcError):
        run("gear_train", {"teeth": [17, 51, 20]})


def test_spring():
    r = run("helical_spring", {"wire_d_mm": 4, "mean_d_mm": 32, "active_coils": 10, "force_n": 100})
    k = 79300 * 4**4 / (8 * 32**3 * 10)
    assert math.isclose(v(r, "rate"), k)
    assert math.isclose(v(r, "deflection"), 100 / k)
    assert r["warnings"] == []
    C = 8
    assert math.isclose(v(r, "wahl_factor"), (4 * C - 1) / (4 * C - 4) + 0.615 / C)


def test_beam_matches_closed_form_and_fea_benchmark():
    r = run("beam", {"case": "cantilever_end_load", "length_mm": 200, "load": 1000, "youngs_modulus_mpa": 210000,
                     "section": {"type": "rectangle", "b_mm": 20, "h_mm": 20}})
    assert math.isclose(v(r, "max_bending_stress"), 150.0)
    assert math.isclose(v(r, "max_deflection"), 1000 * 200**3 / (3 * 210000 * 20**4 / 12))  # 0.952 mm
    assert r["warnings"] == []
    short = run("beam", {"case": "simply_supported_udl", "length_mm": 100, "load": 1, "youngs_modulus_mpa": 70000,
                         "section": {"type": "rectangle", "b_mm": 20, "h_mm": 20}})
    assert short["warnings"]  # span/depth 5 -> shear warning


def test_thermal_resistance():
    r = run("thermal_resistance", {"layers": [{"type": "slab", "thickness_mm": 100, "k_w_mk": 50, "area_mm2": 100},
                                              {"type": "convection", "h_w_m2k": 1000, "area_mm2": 100}],
                                   "t_hot_c": 100, "t_cold_c": 20})
    # same configuration as the CalculiX convection benchmark: q = 0.02667 W/mm^2 * 100 mm^2
    assert math.isclose(v(r, "heat_flow"), 80 / 3000 * 100, rel_tol=1e-12)  # 2.667 W
    assert math.isclose(v(r, "interface_temperatures")[0], 46.6667, rel_tol=1e-5)


def test_battery_pack():
    r = run("battery_pack", {"cells_series": 96, "cells_parallel": 4, "cell_nominal_v": 3.6, "cell_capacity_ah": 5.0,
                             "cell_max_continuous_a": 10, "cell_mass_g": 70})
    assert math.isclose(v(r, "pack_nominal_voltage"), 345.6)
    assert math.isclose(v(r, "pack_energy"), 6.912)
    assert any("60 V" in w for w in r["warnings"])
    with pytest.raises(CalcError):
        run("battery_pack", {"cells_series": 1, "cells_parallel": 1, "cell_nominal_v": 12, "cell_capacity_ah": 1})


def test_iso_fits_against_table_values():
    # 25 H7/g6: hole 25.000/25.021, shaft 24.980/24.993 -> clearance 0.007..0.041
    r = run("iso_fit", {"nominal_mm": 25, "hole": "H7", "shaft": "g6"})
    assert v(r, "fit_type") == "clearance"
    assert [round(x, 3) for x in v(r, "hole_limits")] == [25.0, 25.021]
    assert [round(x, 3) for x in v(r, "shaft_limits")] == [24.98, 24.993]
    assert round(v(r, "min_clearance"), 3) == 0.007 and round(v(r, "max_clearance"), 3) == 0.041
    # 40 H7/p6: interference fit (p6 for 30-50: +26/+42)
    p = run("iso_fit", {"nominal_mm": 40, "hole": "H7", "shaft": "p6"})
    assert v(p, "fit_type") == "interference" and round(v(p, "max_clearance"), 3) == -0.001
    assert run("iso_fit", {"nominal_mm": 25, "hole": "H7", "shaft": "k6"})["outputs"]["fit_type"]["value"] == "transition"
    with pytest.raises(CalcError):
        run("iso_fit", {"nominal_mm": 25, "hole": "G7", "shaft": "h6"})


def test_tolerance_stack():
    r = run("tolerance_stack", {"contributors": [
        {"name": "housing", "nominal": 50, "plus": 0.1, "minus": 0.1, "direction": 1},
        {"name": "bearing", "nominal": 15, "plus": 0, "minus": 0.12, "direction": -1},
        {"name": "spacer", "nominal": 34, "plus": 0.05, "minus": 0.05, "direction": -1}], "spec_min": 0.6, "spec_max": 1.4})
    assert math.isclose(v(r, "mean"), 50 - (15 - 0.06) - 34)
    lo, hi = v(r, "worst_case")
    assert math.isclose(hi - lo, 2 * (0.1 + 0.06 + 0.05))
    assert v(r, "worst_case_ok") is True
