"""
Analytical Sizing, Standard EIA Series Snapping (E12/E24/E96), and Inter-Stage Contract Verification.

Why this module exists:
-----------------------
Most LLM circuit design attempts fail because the model guesses component values directly in a netlist
without first solving DC operating points, small-signal impedances, tank resonance, and Barkhausen
startup margins. Furthermore, connecting Stage N to Stage N+1 often causes severe load-pulling or gain
collapse if input/output impedances are not checked beforehand.

This module enforces equation-grounded engineering calculations BEFORE running SPICE.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Standard EIA decade mantissas
E12_MANTISSAS = [
    1.0, 1.2, 1.5, 1.8, 2.2, 2.7,
    3.3, 3.9, 4.7, 5.6, 6.8, 8.2,
]

E24_MANTISSAS = [
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
]

E96_MANTISSAS = [
    1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30,
    1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74,
    1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32,
    2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09,
    3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12,
    4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49,
    5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32,
    7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76,
]

SERIES_MAP: Dict[str, List[float]] = {
    "E12": E12_MANTISSAS,
    "E24": E24_MANTISSAS,
    "E96": E96_MANTISSAS,
}

SPICE_SUFFIXES = {
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "meg": 1e6,
    "g": 1e9,
    "t": 1e12,
}


def snap_to_series(value: float, series: str = "E24") -> Tuple[float, float]:
    """
    Snap an ideal component value (Ohms, Farads, Henries) to the nearest commercial EIA series value.

    Parameters
    ----------
    value : float
        Positive ideal target value.
    series : str
        One of 'E12', 'E24', or 'E96'.

    Returns
    -------
    Tuple[float, float]
        (snapped_value, relative_error_percent)
    """
    if value <= 0:
        raise ValueError(f"Component value must be positive, got {value}")
    series_key = series.upper()
    if series_key not in SERIES_MAP:
        raise ValueError(f"Unsupported series '{series}'. Choose from {list(SERIES_MAP.keys())}")

    mantissas = SERIES_MAP[series_key]
    exponent = math.floor(math.log10(value))
    normalized = value / (10.0 ** exponent)

    # Search candidates across current decade and adjacent decade boundary (e.g. 9.8 -> 10.0)
    candidates = [m * (10.0 ** exponent) for m in mantissas] + [
        mantissas[0] * (10.0 ** (exponent + 1)),
        mantissas[-1] * (10.0 ** (exponent - 1)),
    ]
    best = min(candidates, key=lambda c: abs(c - value))
    # Round to avoid floating point representation noise
    sig_figs = 3 if series_key == "E96" else 2
    best_exp = math.floor(math.log10(best))
    best_clean = round(best / (10.0 ** best_exp), sig_figs - 1) * (10.0 ** best_exp)
    # Re-clean with formatting precision
    best_clean = float(f"{best_clean:.6g}")
    rel_err_pct = ((best_clean - value) / value) * 100.0
    return best_clean, rel_err_pct


def format_spice_value(value: float) -> str:
    """
    Convert a float value into compact SPICE engineering notation (e.g., 4700 -> '4.7k', 3.3e-10 -> '330p').
    Note: Uses 'Meg' for 1e6 as required by SPICE syntax (where 'm' and 'M' both mean 1e-3).
    """
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    v = abs(value)

    scales = [
        (1e9, "G"),
        (1e6, "Meg"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "u"),
        (1e-9, "n"),
        (1e-12, "p"),
        (1e-15, "f"),
    ]
    for factor, suffix in scales:
        if v >= factor * 0.999999:
            scaled = v / factor
            if scaled >= 1000.0 and suffix != "G":
                continue
            formatted = f"{scaled:.4g}"
            return f"{sign}{formatted}{suffix}"
    return f"{value:.4g}"


def parse_spice_value(token: str) -> float:
    """
    Parse a SPICE numeric string (e.g. '4.7u', '330p', '5Meg', '3.9k', '100') into a Python float.
    """
    s = token.strip()
    match = re.match(r"^([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*([a-zA-Z]*)$", s, re.IGNORECASE)
    if not match:
        raise ValueError(f"Cannot parse SPICE value from '{token}'")
    num_str, unit_str = match.group(1), match.group(2).lower()
    base = float(num_str)
    if not unit_str:
        return base
    if unit_str.startswith("meg"):
        return base * 1e6
    prefix = unit_str[0]
    if prefix in SPICE_SUFFIXES:
        return base * SPICE_SUFFIXES[prefix]
    raise ValueError(f"Unrecognized SPICE unit suffix '{unit_str}' in '{token}'")


# ==============================================================================
# Analytical Circuit Sizing Models
# ==============================================================================

@dataclass
class BJTOperatingPointResult:
    """Analytical DC and small-signal operating point for a voltage-divider biased BJT stage."""
    v_th: float
    r_th: float
    i_e: float
    i_c: float
    i_b: float
    v_b: float
    v_e: float
    v_c: float
    v_ce: float
    g_m: float
    r_e_intrinsic: float
    r_pi: float
    z_in_base: float
    z_in_stage: float
    voltage_gain_loaded: float
    p_dc_total: float
    region: str  # "ACTIVE", "SATURATION_RISK", or "CUTOFF"


def size_bjt_OperatingPoint(
    v_cc: float,
    r1: float,
    r2: float,
    r_e_dc: float,
    r_c: float = 0.0,
    r_e_rf_unbypassed: Optional[float] = None,
    r_load_ac: Optional[float] = None,
    beta: float = 100.0,
    v_be: float = 0.70,
    v_t: float = 0.026,
    topology: str = "CE",  # "CE" (Common Emitter) or "CC" (Emitter Follower)
) -> BJTOperatingPointResult:
    """
    Compute exact analytical DC operating point and small-signal AC parameters for a BJT stage
    (Common-Emitter with full/partial degeneration, or Common-Collector Emitter Follower).
    """
    v_th = v_cc * (r2 / (r1 + r2))
    r_th = (r1 * r2) / (r1 + r2)

    if v_th <= v_be:
        return BJTOperatingPointResult(
            v_th=v_th, r_th=r_th, i_e=0.0, i_c=0.0, i_b=0.0,
            v_b=v_th, v_e=0.0, v_c=v_cc, v_ce=v_cc,
            g_m=0.0, r_e_intrinsic=float("inf"), r_pi=float("inf"),
            z_in_base=float("inf"), z_in_stage=r_th, voltage_gain_loaded=0.0,
            p_dc_total=(v_cc ** 2) / (r1 + r2), region="CUTOFF"
        )

    i_e = (v_th - v_be) / (r_e_dc + r_th / (beta + 1.0))
    i_b = i_e / (beta + 1.0)
    i_c = i_e - i_b

    v_e = i_e * r_e_dc
    v_b = v_e + v_be
    v_c = v_cc - i_c * r_c
    v_ce = v_c - v_e

    g_m = i_c / v_t
    r_e_int = v_t / i_c
    r_pi = beta / g_m

    r_e_ac = r_e_dc if r_e_rf_unbypassed is None else r_e_rf_unbypassed

    if topology.upper() == "CC":
        r_e_eff = (r_e_dc * r_load_ac) / (r_e_dc + r_load_ac) if r_load_ac else r_e_dc
        z_in_base = (beta + 1.0) * (r_e_int + r_e_eff)
        av = r_e_eff / (r_e_int + r_e_eff)
    else:
        z_in_base = (beta + 1.0) * (r_e_int + r_e_ac)
        r_c_eff = (r_c * r_load_ac) / (r_c + r_load_ac) if (r_load_ac and r_c > 0) else r_c
        av = -r_c_eff / (r_e_int + r_e_ac)

    z_in_stage = 1.0 / (1.0 / r_th + 1.0 / z_in_base)
    i_div = (v_cc - v_b) / r1
    p_dc = v_cc * (i_c + i_div)

    region = "ACTIVE" if v_ce >= 1.0 else "SATURATION_RISK"

    return BJTOperatingPointResult(
        v_th=v_th,
        r_th=r_th,
        i_e=i_e,
        i_c=i_c,
        i_b=i_b,
        v_b=v_b,
        v_e=v_e,
        v_c=v_c,
        v_ce=v_ce,
        g_m=g_m,
        r_e_intrinsic=r_e_int,
        r_pi=r_pi,
        z_in_base=z_in_base,
        z_in_stage=z_in_stage,
        voltage_gain_loaded=av,
        p_dc_total=p_dc,
        region=region,
    )


def evaluate_varactor_cv(
    v_reverse: float,
    c_j0: float = 61.40e-12,
    v_j: float = 14.51,
    m: float = 6.780,
    c_p: float = 8.90e-12,
) -> float:
    """
    Evaluate reverse capacitance C_var(V_R) = C_J0 / (1 + V_R / V_J)^M + C_P.
    Defaults correspond to the hyperabrupt Skyworks SMV1237-001LF model.
    """
    if v_reverse < -0.5 * v_j:
        raise ValueError(f"Reverse voltage {v_reverse}V is deep into forward conduction.")
    return c_j0 / ((1.0 + v_reverse / v_j) ** m) + c_p


@dataclass
class ColpittsTankResult:
    """Analytical resonance and feedback parameters of a Colpitts LC + Varactor tank."""
    c_12: float
    c_branch: float
    c_eq: float
    f_lc_hz: float
    feedback_n: float
    varactor_participation_pct: float


def size_colpitts_tank(
    l1: float,
    c1: float,
    c2: float,
    c_var: float = 0.0,
    c_cv: Optional[float] = None,
) -> ColpittsTankResult:
    """
    Calculate the passive equivalent tank capacitance C_eq, resonant frequency f_LC,
    capacitive feedback ratio n = C1 / (C1 + C2), and varactor participation factor.
    """
    c_12 = (c1 * c2) / (c1 + c2)
    if c_var > 0:
        if c_cv is not None and c_cv > 0:
            c_branch = (c_cv * c_var) / (c_cv + c_var)
        else:
            c_branch = c_var
    else:
        c_branch = 0.0

    c_eq = c_12 + c_branch
    f_lc = 1.0 / (2.0 * math.pi * math.sqrt(l1 * c_eq))
    n = c1 / (c1 + c2)
    part_pct = (c_branch / c_eq) * 100.0 if c_eq > 0 else 0.0

    return ColpittsTankResult(
        c_12=c_12,
        c_branch=c_branch,
        c_eq=c_eq,
        f_lc_hz=f_lc,
        feedback_n=n,
        varactor_participation_pct=part_pct,
    )


@dataclass
class BarkhausenResult:
    """Analytical Barkhausen startup margin and negative resistance check for a Colpitts oscillator."""
    omega_0: float
    x_l: float
    q_inductor: float
    r_p_tank: float
    g_m_min: float
    g_m_actual: float
    loop_gain_margin: float
    r_negative_ohms: float
    will_start: bool


def evaluate_barkhausen_colpitts(
    l1: float,
    r_ser_inductor: float,
    c1: float,
    c2: float,
    g_m: float,
    f_osc_hz: float,
) -> BarkhausenResult:
    """
    Evaluate Barkhausen startup criteria and negative input resistance for a BJT Colpitts oscillator:
      - Tank parallel loss resistance: R_p = (omega_0 * L1)^2 / R_ser
      - Minimum transconductance: g_m,min = (C1 + C2)^2 / (R_p * C1 * C2)
      - Synthesized negative resistance at base: R_neg = - g_m / (omega_0^2 * C1 * C2)
    """
    omega_0 = 2.0 * math.pi * f_osc_hz
    x_l = omega_0 * l1
    q_l = x_l / r_ser_inductor
    r_p = (x_l ** 2) / r_ser_inductor
    n = c1 / (c1 + c2)
    g_m_min = 1.0 / (r_p * n * (1.0 - n))
    margin = g_m / g_m_min if g_m_min > 0 else 0.0
    r_neg = -g_m / ((omega_0 ** 2) * c1 * c2)
    # Practical RF engineering rule requires margin >= 3.0 to ensure fast autonomous startup
    will_start = margin >= 3.0

    return BarkhausenResult(
        omega_0=omega_0,
        x_l=x_l,
        q_inductor=q_l,
        r_p_tank=r_p,
        g_m_min=g_m_min,
        g_m_actual=g_m,
        loop_gain_margin=margin,
        r_negative_ohms=r_neg,
        will_start=will_start,
    )


# ==============================================================================
# Inter-Stage Contract & Loading Verification
# ==============================================================================

@dataclass
class StageContract:
    """Formal electrical interface contract for a single functional block in a multi-stage circuit."""
    name: str
    z_in_ohms: float
    z_out_ohms: float
    unloaded_gain_vv: float
    max_output_current_peak_a: float = float("inf")
    coupling_reactance_ohms: float = 0.0  # e.g., |X_CEX| between this stage and the next


@dataclass
class CascadeLoadingReport:
    """Result of checking impedance loading between Stage N (driver) and Stage N+1 (load)."""
    driver_name: str
    load_name: str
    attenuation_factor: float
    effective_gain_vv: float
    gain_drop_pct: float
    peak_current_drawn_a: float
    current_utilization_pct: float
    passed: bool
    warnings: List[str] = field(default_factory=list)


def check_cascade_loading(
    driver: StageContract,
    load: StageContract,
    signal_peak_v: float = 1.0,
    max_allowed_current_fraction: float = 0.15,
) -> CascadeLoadingReport:
    """
    Verify whether connecting `load` (Stage N+1) to `driver` (Stage N) causes excessive voltage
    divider attenuation or exceeds the driver's safe RF current limit (preventing oscillator load-pulling).
    """
    total_load_z = math.hypot(load.z_in_ohms, driver.coupling_reactance_ohms) if driver.coupling_reactance_ohms > 0 else load.z_in_ohms
    atten = load.z_in_ohms / (driver.z_out_ohms + total_load_z)
    eff_gain = driver.unloaded_gain_vv * atten
    gain_drop_pct = (1.0 - atten) * 100.0

    i_drawn = signal_peak_v / (driver.z_out_ohms + total_load_z)
    curr_util_pct = (i_drawn / driver.max_output_current_peak_a) * 100.0 if math.isfinite(driver.max_output_current_peak_a) else 0.0

    warnings: List[str] = []
    passed = True

    if curr_util_pct > max_allowed_current_fraction * 100.0:
        passed = False
        warnings.append(
            f"Load pulling risk: Stage '{load.name}' draws {i_drawn*1e3:.2f} mA peak "
            f"({curr_util_pct:.1f}% of '{driver.name}' max {driver.max_output_current_peak_a*1e3:.2f} mA; "
            f"safe limit is {max_allowed_current_fraction*100:.0f}%). Increase coupling reactance (weak coupling) or add buffer."
        )

    if driver.coupling_reactance_ohms == 0 and load.z_in_ohms < 2.0 * driver.z_out_ohms:
        warnings.append(
            f"Impedance mismatch: Load Z_in ({load.z_in_ohms:.1f} Ohm) < 2x Driver Z_out ({driver.z_out_ohms:.1f} Ohm), "
            f"causing {gain_drop_pct:.1f}% voltage drop. Consider an Emitter Follower (CC) buffer."
        )

    return CascadeLoadingReport(
        driver_name=driver.name,
        load_name=load.name,
        attenuation_factor=atten,
        effective_gain_vv=eff_gain,
        gain_drop_pct=gain_drop_pct,
        peak_current_drawn_a=i_drawn,
        current_utilization_pct=curr_util_pct,
        passed=passed,
        warnings=warnings,
    )
