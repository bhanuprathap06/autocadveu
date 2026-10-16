"""Engineering calculators with explicit formulas, assumptions and validation.

Every calculator returns::

    {key, inputs, outputs: {name: {value, unit}}, intermediate, formula, assumptions,
     warnings, references, status}

Inputs are validated (ranges, physical consistency); invalid input raises
CalcError with an actionable message. These are hand-calculation aids: none of
them implies regulatory, homologation or safety certification.
"""

from __future__ import annotations

import math
from typing import Any, Callable

SAFETY_NOTE = ("Hand-calculation aid. Results depend on the stated assumptions and are not a substitute for detailed analysis, "
               "testing or independent engineering verification of safety-critical components.")


class CalcError(ValueError):
    pass


def _pos(name: str, v: Any) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError) as exc:
        raise CalcError(f"{name} must be a number") from exc
    if not math.isfinite(v) or v <= 0:
        raise CalcError(f"{name} must be greater than zero")
    return v


def _num(name: str, v: Any) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError) as exc:
        raise CalcError(f"{name} must be a number") from exc
    if not math.isfinite(v):
        raise CalcError(f"{name} must be finite")
    return v


def _out(key, inputs, outputs, formula, assumptions, intermediate=None, warnings=None, refs=None, status="implemented_and_verified"):
    return {"key": key, "inputs": inputs, "outputs": outputs, "intermediate": intermediate or {}, "formula": formula,
            "assumptions": assumptions + [SAFETY_NOTE], "warnings": warnings or [], "references": refs or [], "status": status}


# --------------------------------------------------------------------------- units
UNITS: dict[str, dict[str, float]] = {
    "length": {"mm": 1e-3, "cm": 1e-2, "m": 1.0, "km": 1e3, "in": 0.0254, "ft": 0.3048},
    "mass": {"g": 1e-3, "kg": 1.0, "t": 1e3, "lb": 0.45359237},
    "force": {"N": 1.0, "kN": 1e3, "lbf": 4.4482216152605, "kgf": 9.80665},
    "torque": {"N*m": 1.0, "N*mm": 1e-3, "lbf*ft": 1.3558179483314, "lbf*in": 0.1129848290276},
    "pressure": {"Pa": 1.0, "kPa": 1e3, "MPa": 1e6, "GPa": 1e9, "bar": 1e5, "psi": 6894.757293168},
    "power": {"W": 1.0, "kW": 1e3, "hp": 745.69987158227, "PS": 735.49875},
    "speed": {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704},
    "angular_speed": {"rad/s": 1.0, "rpm": 2 * math.pi / 60, "deg/s": math.pi / 180},
    "energy": {"J": 1.0, "kJ": 1e3, "Wh": 3600.0, "kWh": 3.6e6},
    "volume": {"mm3": 1e-9, "cm3": 1e-6, "L": 1e-3, "m3": 1.0, "in3": 1.6387064e-5},
    "density": {"kg/m3": 1.0, "g/cm3": 1000.0, "lb/in3": 27679.9047},
}


def unit_convert(value: Any, from_unit: str, to_unit: str, quantity: str | None = None) -> dict[str, Any]:
    v = _num("value", value)
    if from_unit in ("degC", "degF", "K") or to_unit in ("degC", "degF", "K"):
        to_k = {"degC": lambda x: x + 273.15, "degF": lambda x: (x - 32) * 5 / 9 + 273.15, "K": lambda x: x}
        from_k = {"degC": lambda x: x - 273.15, "degF": lambda x: (x - 273.15) * 9 / 5 + 32, "K": lambda x: x}
        if from_unit not in to_k or to_unit not in from_k:
            raise CalcError("Temperature conversions are between degC, degF and K only")
        k = to_k[from_unit](v)
        if k < 0:
            raise CalcError("Temperature below absolute zero")
        r = from_k[to_unit](k)
        return _out("unit_convert", {"value": v, "from": from_unit, "to": to_unit}, {"result": {"value": r, "unit": to_unit}},
                    "Affine temperature conversion via kelvin", ["Exact conversion factors"])
    groups = [q for q, t in UNITS.items() if from_unit in t and to_unit in t and (quantity is None or q == quantity)]
    if not groups:
        raise CalcError(f"Cannot convert {from_unit} to {to_unit}: incompatible or unknown units")
    t = UNITS[groups[0]]
    r = v * t[from_unit] / t[to_unit]
    return _out("unit_convert", {"value": v, "from": from_unit, "to": to_unit, "quantity": groups[0]},
                {"result": {"value": r, "unit": to_unit}}, f"x[{to_unit}] = x[{from_unit}] * {t[from_unit]:.12g} / {t[to_unit]:.12g}",
                ["Exact (defined) conversion factors"], refs=["NIST SP 811"])


# --------------------------------------------------------------------------- powertrain
def shaft_power(torque_nm: Any = None, speed_rpm: Any = None, power_kw: Any = None) -> dict[str, Any]:
    given = {k: v for k, v in {"torque_nm": torque_nm, "speed_rpm": speed_rpm, "power_kw": power_kw}.items() if v is not None}
    if len(given) != 2:
        raise CalcError("Provide exactly two of torque_nm, speed_rpm, power_kw")
    if torque_nm is not None:
        torque_nm = _pos("torque_nm", torque_nm)
    if speed_rpm is not None:
        speed_rpm = _pos("speed_rpm", speed_rpm)
    if power_kw is not None:
        power_kw = _pos("power_kw", power_kw)
    if power_kw is None:
        w = speed_rpm * 2 * math.pi / 60
        power_kw = torque_nm * w / 1000
    elif torque_nm is None:
        w = speed_rpm * 2 * math.pi / 60
        torque_nm = power_kw * 1000 / w
    else:
        w = power_kw * 1000 / torque_nm
        speed_rpm = w * 60 / (2 * math.pi)
    return _out("shaft_power", given, {"torque": {"value": torque_nm, "unit": "N*m"}, "speed": {"value": speed_rpm, "unit": "rpm"},
                                       "power": {"value": power_kw, "unit": "kW"}},
                "P = T * omega, omega = 2*pi*n/60", ["Steady state, no losses"], {"omega_rad_s": w})


def shaft_torsion(torque_nm: Any, diameter_mm: Any, bore_mm: Any = 0, length_mm: Any = None, shear_modulus_mpa: Any = None
                  ) -> dict[str, Any]:
    T = _pos("torque_nm", torque_nm) * 1000  # N*mm
    D = _pos("diameter_mm", diameter_mm)
    d = _num("bore_mm", bore_mm or 0)
    if d < 0 or d >= D:
        raise CalcError("bore_mm must be >= 0 and smaller than diameter_mm")
    J = math.pi * (D**4 - d**4) / 32
    tau = T * (D / 2) / J
    out = {"max_shear_stress": {"value": tau, "unit": "MPa"}, "polar_moment": {"value": J, "unit": "mm^4"}}
    inter = {"T_Nmm": T}
    if length_mm is not None and shear_modulus_mpa is not None:
        L, G = _pos("length_mm", length_mm), _pos("shear_modulus_mpa", shear_modulus_mpa)
        out["twist_angle"] = {"value": math.degrees(T * L / (G * J)), "unit": "deg"}
    return _out("shaft_torsion", {"torque_nm": torque_nm, "diameter_mm": D, "bore_mm": d}, out,
                "tau = T*r/J, J = pi*(D^4-d^4)/32, theta = T*L/(G*J)",
                ["Circular (hollow) section, linear elastic, pure torsion", "No stress concentrations (keyways, shoulders) included"],
                inter, refs=["Roark's Formulas for Stress and Strain, torsion of circular shafts"])


def gear_train(teeth: list[Any], input_speed_rpm: Any = None, input_torque_nm: Any = None, efficiency_per_mesh: Any = 1.0
               ) -> dict[str, Any]:
    if not isinstance(teeth, list) or len(teeth) < 2 or len(teeth) % 2:
        raise CalcError("Provide teeth as pairs [driver1, driven1, driver2, driven2, ...]")
    z = [int(_pos("teeth", t)) for t in teeth]
    if any(t < 6 for t in z):
        raise CalcError("Tooth counts below 6 are not practical for involute gears")
    eta = _pos("efficiency_per_mesh", efficiency_per_mesh)
    if eta > 1:
        raise CalcError("efficiency_per_mesh must be <= 1")
    ratio = 1.0
    stages = []
    for i in range(0, len(z), 2):
        r = z[i + 1] / z[i]
        stages.append({"driver": z[i], "driven": z[i + 1], "ratio": r})
        ratio *= r
    n_mesh = len(stages)
    eta_tot = eta**n_mesh
    out: dict[str, Any] = {"overall_ratio": {"value": ratio, "unit": "-"}, "overall_efficiency": {"value": eta_tot, "unit": "-"}}
    if input_speed_rpm is not None:
        out["output_speed"] = {"value": _pos("input_speed_rpm", input_speed_rpm) / ratio, "unit": "rpm"}
    if input_torque_nm is not None:
        out["output_torque"] = {"value": _pos("input_torque_nm", input_torque_nm) * ratio * eta_tot, "unit": "N*m"}
    warnings = [] if eta < 1 else ["Efficiency assumed 100%: real meshes lose roughly 1-3% each (estimate, not verified)"]
    return _out("gear_train", {"teeth": z, "efficiency_per_mesh": eta}, out,
                "i = prod(z_driven/z_driver); n_out = n_in/i; T_out = T_in*i*eta^stages",
                ["Simple (non-epicyclic) train", "Rigid gears, no slip", "Efficiency per mesh constant"],
                {"stages": stages}, warnings)


# --------------------------------------------------------------------------- springs
def helical_spring(wire_d_mm: Any, mean_d_mm: Any, active_coils: Any, shear_modulus_mpa: Any = 79300, force_n: Any = None
                   ) -> dict[str, Any]:
    d, D, n, G = _pos("wire_d_mm", wire_d_mm), _pos("mean_d_mm", mean_d_mm), _pos("active_coils", active_coils), _pos("shear_modulus_mpa", shear_modulus_mpa)
    C = D / d
    warnings = []
    if C < 4 or C > 12:
        warnings.append(f"Spring index C = {C:.2f} is outside the usual 4-12 range (manufacturability / Wahl accuracy)")
    k = G * d**4 / (8 * D**3 * n)
    Kw = (4 * C - 1) / (4 * C - 4) + 0.615 / C
    out = {"rate": {"value": k, "unit": "N/mm"}, "spring_index": {"value": C, "unit": "-"}, "wahl_factor": {"value": Kw, "unit": "-"}}
    if force_n is not None:
        F = _pos("force_n", force_n)
        out["deflection"] = {"value": F / k, "unit": "mm"}
        out["shear_stress_corrected"] = {"value": Kw * 8 * F * D / (math.pi * d**3), "unit": "MPa"}
    return _out("helical_spring", {"wire_d_mm": d, "mean_d_mm": D, "active_coils": n, "shear_modulus_mpa": G}, out,
                "k = G*d^4/(8*D^3*n); tau = Kw*8*F*D/(pi*d^3); Kw = (4C-1)/(4C-4)+0.615/C",
                ["Round-wire helical compression spring, linear range", "Static load; fatigue and buckling not checked",
                 "Default G = 79.3 GPa (music wire, typical)"], warnings=warnings,
                refs=["Shigley's Mechanical Engineering Design, ch. 10"])


# --------------------------------------------------------------------------- beams
def beam(case: str, length_mm: Any, load: Any, youngs_modulus_mpa: Any, section: dict[str, Any]) -> dict[str, Any]:
    L, E = _pos("length_mm", length_mm), _pos("youngs_modulus_mpa", youngs_modulus_mpa)
    kind = section.get("type")
    if kind == "rectangle":
        b, h = _pos("b_mm", section.get("b_mm")), _pos("h_mm", section.get("h_mm"))
        I, c, A = b * h**3 / 12, h / 2, b * h
    elif kind == "circle":
        dd = _pos("d_mm", section.get("d_mm"))
        I, c, A = math.pi * dd**4 / 64, dd / 2, math.pi * dd**2 / 4
    elif kind == "tube":
        Do, Di = _pos("od_mm", section.get("od_mm")), _num("id_mm", section.get("id_mm"))
        if not 0 <= Di < Do:
            raise CalcError("id_mm must be >= 0 and smaller than od_mm")
        I, c, A = math.pi * (Do**4 - Di**4) / 64, Do / 2, math.pi * (Do**2 - Di**2) / 4
    else:
        raise CalcError("section.type must be rectangle, circle or tube")
    if case == "cantilever_end_load":
        P = _pos("load (N)", load)
        M, dmax = P * L, P * L**3 / (3 * E * I)
        formula = "M = P*L, delta = P*L^3/(3*E*I)"
    elif case == "cantilever_udl":
        w = _pos("load (N/mm)", load)
        M, dmax = w * L**2 / 2, w * L**4 / (8 * E * I)
        formula = "M = w*L^2/2, delta = w*L^4/(8*E*I)"
    elif case == "simply_supported_center_load":
        P = _pos("load (N)", load)
        M, dmax = P * L / 4, P * L**3 / (48 * E * I)
        formula = "M = P*L/4, delta = P*L^3/(48*E*I)"
    elif case == "simply_supported_udl":
        w = _pos("load (N/mm)", load)
        M, dmax = w * L**2 / 8, 5 * w * L**4 / (384 * E * I)
        formula = "M = w*L^2/8, delta = 5*w*L^4/(384*E*I)"
    else:
        raise CalcError("Unknown beam case")
    sigma = M * c / I
    warnings = []
    depth = 2 * c
    if L / depth < 10:
        warnings.append(f"Span/depth = {L / depth:.1f} < 10: shear deformation is significant; Euler-Bernoulli underestimates "
                        "deflection")
    return _out("beam", {"case": case, "length_mm": L, "load": load, "youngs_modulus_mpa": E, "section": section},
                {"max_bending_stress": {"value": sigma, "unit": "MPa"}, "max_deflection": {"value": dmax, "unit": "mm"},
                 "max_moment": {"value": M, "unit": "N*mm"}},
                formula + "; sigma = M*c/I",
                ["Euler-Bernoulli beam, small deflections, linear elastic, prismatic", "Ideal supports, no local effects",
                 "Self-weight not included"], {"I_mm4": I, "c_mm": c, "area_mm2": A}, warnings,
                refs=["Roark's Formulas for Stress and Strain, table 8.1"])


# --------------------------------------------------------------------------- thermal
def thermal_resistance(layers: list[dict[str, Any]], t_hot_c: Any = None, t_cold_c: Any = None) -> dict[str, Any]:
    if not layers:
        raise CalcError("Provide at least one layer")
    rs = []
    for i, l in enumerate(layers):
        t = l.get("type")
        if t == "slab":
            R = _pos("thickness_mm", l.get("thickness_mm")) * 1e-3 / (_pos("k_w_mk", l.get("k_w_mk")) * _pos("area_mm2", l.get("area_mm2")) * 1e-6)
        elif t == "cylinder":
            ri, ro = _pos("r_in_mm", l.get("r_in_mm")), _pos("r_out_mm", l.get("r_out_mm"))
            if ro <= ri:
                raise CalcError("r_out_mm must exceed r_in_mm")
            R = math.log(ro / ri) / (2 * math.pi * _pos("k_w_mk", l.get("k_w_mk")) * _pos("length_mm", l.get("length_mm")) * 1e-3)
        elif t == "convection":
            R = 1 / (_pos("h_w_m2k", l.get("h_w_m2k")) * _pos("area_mm2", l.get("area_mm2")) * 1e-6)
        elif t == "contact":
            R = _pos("r_contact_m2k_w", l.get("r_contact_m2k_w")) / (_pos("area_mm2", l.get("area_mm2")) * 1e-6)
        else:
            raise CalcError(f"Layer {i}: type must be slab, cylinder, convection or contact")
        rs.append({"layer": i, "type": t, "R_k_w": R})
    Rt = sum(r["R_k_w"] for r in rs)
    out: dict[str, Any] = {"total_resistance": {"value": Rt, "unit": "K/W"}}
    if t_hot_c is not None and t_cold_c is not None:
        dT = _num("t_hot_c", t_hot_c) - _num("t_cold_c", t_cold_c)
        q = dT / Rt
        out["heat_flow"] = {"value": q, "unit": "W"}
        temps, T = [], float(t_hot_c)
        for r in rs:
            T -= q * r["R_k_w"]
            temps.append(T)
        out["interface_temperatures"] = {"value": temps, "unit": "degC"}
    return _out("thermal_resistance", {"layers": layers}, out, "R_slab = L/(kA); R_cyl = ln(ro/ri)/(2*pi*k*L); R_conv = 1/(hA); "
                "series: R = sum(R_i); Q = dT/R", ["1D steady conduction, series layers", "Constant properties"],
                {"layers": rs}, refs=["Incropera, Fundamentals of Heat and Mass Transfer, ch. 3"])


# --------------------------------------------------------------------------- battery
def battery_pack(cells_series: Any, cells_parallel: Any, cell_nominal_v: Any, cell_capacity_ah: Any,
                 cell_max_continuous_a: Any = None, cell_mass_g: Any = None, cell_resistance_mohm: Any = None) -> dict[str, Any]:
    s, p = int(_pos("cells_series", cells_series)), int(_pos("cells_parallel", cells_parallel))
    v, c = _pos("cell_nominal_v", cell_nominal_v), _pos("cell_capacity_ah", cell_capacity_ah)
    if v > 5:
        raise CalcError("cell_nominal_v above 5 V is not a single electrochemical cell")
    out: dict[str, Any] = {"pack_nominal_voltage": {"value": s * v, "unit": "V"}, "pack_capacity": {"value": p * c, "unit": "Ah"},
                           "pack_energy": {"value": s * p * v * c / 1000, "unit": "kWh"}, "cell_count": {"value": s * p, "unit": "-"}}
    warnings = ["Nominal energy only: usable energy depends on depth of discharge, temperature and ageing"]
    if cell_max_continuous_a is not None:
        I = _pos("cell_max_continuous_a", cell_max_continuous_a) * p
        out["pack_max_continuous_current"] = {"value": I, "unit": "A"}
        out["pack_max_continuous_power"] = {"value": I * s * v / 1000, "unit": "kW"}
    if cell_mass_g is not None:
        m = _pos("cell_mass_g", cell_mass_g) * s * p / 1000
        out["cell_mass_total"] = {"value": m, "unit": "kg"}
        out["cell_level_specific_energy"] = {"value": s * p * v * c / m, "unit": "Wh/kg"}
        warnings.append("Mass covers cells only; enclosure, busbars, cooling and BMS are excluded")
    if cell_resistance_mohm is not None:
        out["pack_dc_resistance"] = {"value": _pos("cell_resistance_mohm", cell_resistance_mohm) * s / p, "unit": "mOhm"}
    if s * v > 60:
        warnings.append("Pack exceeds 60 V DC: high-voltage safety requirements apply (e.g. ECE R100 / ISO 6469); "
                        "requires qualified review")
    return _out("battery_pack", {"series": s, "parallel": p, "cell_nominal_v": v, "cell_capacity_ah": c}, out,
                "V = s*V_cell; Ah = p*Ah_cell; E = s*p*V_cell*Ah_cell; R = s*R_cell/p",
                ["Identical, balanced cells", "Interconnect resistance excluded"], warnings=warnings)


# --------------------------------------------------------------------------- ISO 286 fits
IT_RANGES = [3, 6, 10, 18, 30, 50, 80, 120, 180, 250, 315, 400, 500]
IT = {  # um, ISO 286-1 standard tolerance grades
    5: [4, 5, 6, 8, 9, 11, 13, 15, 18, 20, 23, 25, 27],
    6: [6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40],
    7: [10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63],
    8: [14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97],
    9: [25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155],
    10: [40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250],
    11: [60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400],
}
SHAFT_FD = {  # fundamental deviation (um): es for f,g,h; ei for k,m,n,p (k: grades 4..7)
    "f": [-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68],
    "g": [-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20],
    "h": [0] * 13,
    "k": [0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5],
    "m": [2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23],
    "n": [4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40],
    "p": [6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68],
}


def _range_idx(size: float) -> int:
    if size <= 0 or size > 500:
        raise CalcError("ISO 286 tables here cover nominal sizes 0 < D <= 500 mm")
    return next(i for i, upper in enumerate(IT_RANGES) if size <= upper)


def _parse(code: str, hole: bool) -> tuple[str, int]:
    import re

    m = re.fullmatch(r"([A-Za-z])(\d{1,2})", code.strip())
    if not m:
        raise CalcError(f"Invalid tolerance class {code!r} (e.g. H7, g6)")
    letter, grade = m.group(1), int(m.group(2))
    if hole and letter != "H":
        raise CalcError("Only hole-basis fits (H holes) are supported")
    if not hole and letter not in SHAFT_FD:
        raise CalcError(f"Shaft letter {letter!r} not supported; supported: {', '.join(SHAFT_FD)}")
    if grade not in IT:
        raise CalcError(f"Grade IT{grade} not in table (supported IT5-IT11)")
    return letter, grade


def iso_fit(nominal_mm: Any, hole: str = "H7", shaft: str = "g6") -> dict[str, Any]:
    D = _pos("nominal_mm", nominal_mm)
    i = _range_idx(D)
    _, gh = _parse(hole, True)
    sl, gs = _parse(shaft, False)
    ITh, ITs = IT[gh][i], IT[gs][i]
    EI, ES = 0, ITh
    warnings = []
    if sl in ("f", "g", "h"):
        es = SHAFT_FD[sl][i]
        ei = es - ITs
    else:
        if sl == "k" and gs > 7:
            raise CalcError("k fundamental deviation in this table applies to grades IT4-IT7")
        ei = SHAFT_FD[sl][i]
        es = ei + ITs
    max_clear, min_clear = ES - ei, EI - es
    if min_clear >= 0:
        kind = "clearance"
    elif max_clear <= 0:
        kind = "interference"
    else:
        kind = "transition"
    rng = f"{([0] + IT_RANGES)[i]}-{IT_RANGES[i]} mm"
    return _out("iso_fit", {"nominal_mm": D, "hole": hole, "shaft": shaft},
                {"fit_type": {"value": kind, "unit": "-"},
                 "hole_limits": {"value": [D + EI / 1000, D + ES / 1000], "unit": "mm"},
                 "shaft_limits": {"value": [D + ei / 1000, D + es / 1000], "unit": "mm"},
                 "max_clearance": {"value": max_clear / 1000, "unit": "mm"},
                 "min_clearance": {"value": min_clear / 1000, "unit": "mm"}},
                "Hole: EI = 0, ES = IT(hole); shaft deviations per ISO 286-2 fundamental deviation; negative clearance = interference",
                [f"Size range {rng}", "Hole-basis system", "Deviations at 20 degC"],
                {"IT_hole_um": ITh, "IT_shaft_um": ITs, "EI_um": EI, "ES_um": ES, "ei_um": ei, "es_um": es}, warnings,
                refs=["ISO 286-1:2010 Table 1", "ISO 286-2:2010"], status="implemented_with_limitations")


def tolerance_stack(contributors: list[dict[str, Any]], spec_min: Any = None, spec_max: Any = None) -> dict[str, Any]:
    """1D linear stack. Each contributor: {name, nominal, plus, minus, direction (+1/-1)}."""
    if not contributors:
        raise CalcError("Provide at least one contributor")
    nom, wc, rss2 = 0.0, 0.0, 0.0
    rows = []
    for c in contributors:
        d = int(c.get("direction", 1))
        if d not in (1, -1):
            raise CalcError("direction must be +1 or -1")
        n = _num("nominal", c.get("nominal"))
        plus, minus = abs(_num("plus", c.get("plus", 0))), abs(_num("minus", c.get("minus", 0)))
        mean_shift = (plus - minus) / 2
        half = (plus + minus) / 2
        nom += d * (n + mean_shift)
        wc += half
        rss2 += half**2
        rows.append({"name": c.get("name", ""), "direction": d, "mean": n + mean_shift, "half_tolerance": half})
    rss = math.sqrt(rss2)
    out = {"mean": {"value": nom, "unit": "mm"},
           "worst_case": {"value": [nom - wc, nom + wc], "unit": "mm"},
           "rss": {"value": [nom - rss, nom + rss], "unit": "mm"}}
    warnings = []
    if spec_min is not None or spec_max is not None:
        lo = _num("spec_min", spec_min) if spec_min is not None else -math.inf
        hi = _num("spec_max", spec_max) if spec_max is not None else math.inf
        out["worst_case_ok"] = {"value": (nom - wc) >= lo and (nom + wc) <= hi, "unit": "-"}
        out["rss_ok"] = {"value": (nom - rss) >= lo and (nom + rss) <= hi, "unit": "-"}
        if out["rss_ok"]["value"] and not out["worst_case_ok"]["value"]:
            warnings.append("Passes statistically (RSS) but not worst case: requires process capability evidence")
    return _out("tolerance_stack", {"contributors": contributors}, out,
                "mean = sum(d_i * mid_i); worst case = mean +/- sum(t_i); RSS = mean +/- sqrt(sum(t_i^2))",
                ["1D linear stack", "RSS assumes independent, centred, normally distributed contributors (each half tolerance = 3 sigma)"],
                {"contributors": rows}, warnings)


def mass_from_volume(volume_mm3: Any, density_kg_m3: Any) -> dict[str, Any]:
    V, rho = _pos("volume_mm3", volume_mm3), _pos("density_kg_m3", density_kg_m3)
    return _out("mass_from_volume", {"volume_mm3": V, "density_kg_m3": rho}, {"mass": {"value": V * 1e-9 * rho, "unit": "kg"}},
                "m = V * rho", ["Homogeneous material", "Volume from exact B-rep when taken from a CAD model"])


CALCULATORS: dict[str, dict[str, Any]] = {
    "unit_convert": {"fn": unit_convert, "name": "Unit conversion", "domain": "general"},
    "shaft_power": {"fn": shaft_power, "name": "Shaft torque / speed / power", "domain": "powertrain"},
    "shaft_torsion": {"fn": shaft_torsion, "name": "Shaft torsional stress and twist", "domain": "powertrain"},
    "gear_train": {"fn": gear_train, "name": "Gear train ratio, speed and torque", "domain": "powertrain"},
    "helical_spring": {"fn": helical_spring, "name": "Helical compression spring", "domain": "chassis_suspension"},
    "beam": {"fn": beam, "name": "Beam bending stress and deflection", "domain": "mechanical"},
    "thermal_resistance": {"fn": thermal_resistance, "name": "Thermal resistance network", "domain": "combustion_cooling"},
    "battery_pack": {"fn": battery_pack, "name": "Battery pack series/parallel", "domain": "ev_battery"},
    "iso_fit": {"fn": iso_fit, "name": "ISO 286 limits and fits", "domain": "manufacturing"},
    "tolerance_stack": {"fn": tolerance_stack, "name": "1D tolerance stack (worst case / RSS)", "domain": "manufacturing"},
    "mass_from_volume": {"fn": mass_from_volume, "name": "Mass from volume and density", "domain": "general"},
}


def run(key: str, inputs: dict[str, Any]) -> dict[str, Any]:
    c = CALCULATORS.get(key)
    if c is None:
        raise CalcError(f"Unknown calculator {key!r}")
    fn: Callable[..., dict[str, Any]] = c["fn"]
    try:
        return fn(**inputs)
    except TypeError as exc:
        raise CalcError(f"Invalid inputs: {exc}") from exc
