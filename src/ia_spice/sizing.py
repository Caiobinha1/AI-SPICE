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


# ==============================================================================
# Multi-Domain Analytical Sizing (Op-Amps, MOSFETs, Filters, RF Matching, Power)
# ==============================================================================

@dataclass
class VoltageDividerResult:
    """Analytical result for a precision E-series voltage divider or feedback network."""
    r_top: float
    r_bot: float
    target_ratio: float
    actual_ratio: float
    v_in: float
    v_out_actual: float
    error_pct: float
    thevenin_r_ohms: float
    quiescent_current_a: float


def size_voltage_divider(
    v_in: float,
    v_out_target: float,
    r_total_target: float = 20e3,
    series: str = "E24",
) -> VoltageDividerResult:
    """
    Find the optimal commercial E12/E24/E96 resistor pair (R_top, R_bot) that minimizes
    output voltage error for V_out = V_in * R_bot / (R_top + R_bot) near `r_total_target`.
    Also used for LDO / DC-DC converter feedback networks where V_in is V_out_reg and V_out_target is V_ref.
    """
    if v_out_target <= 0 or v_out_target >= v_in:
        raise ValueError(f"v_out_target ({v_out_target}) must be strictly between 0 and v_in ({v_in}).")
    target_ratio = v_out_target / v_in
    r_bot_ideal = r_total_target * target_ratio
    r_bot_base, _ = snap_to_series(r_bot_ideal, series=series)

    mantissas = SERIES_MAP[series.upper()]
    exp_bot = math.floor(math.log10(r_bot_base))
    bot_candidates = [m * (10.0 ** e) for e in (exp_bot - 1, exp_bot, exp_bot + 1) for m in mantissas]

    best_pair = (0.0, 0.0)
    best_err = float("inf")
    for rb in bot_candidates:
        if rb < 0.15 * r_bot_ideal or rb > 6.0 * r_bot_ideal:
            continue
        rt_ideal = rb * (1.0 - target_ratio) / target_ratio
        rt, _ = snap_to_series(rt_ideal, series=series)
        ratio = rb / (rt + rb)
        err = abs(ratio - target_ratio) / target_ratio
        if err < best_err:
            best_err = err
            best_pair = (rt, rb)

    r_top, r_bot = best_pair
    actual_ratio = r_bot / (r_top + r_bot)
    v_out_actual = v_in * actual_ratio
    err_pct = ((v_out_actual - v_out_target) / v_out_target) * 100.0
    r_th = (r_top * r_bot) / (r_top + r_bot)
    i_q = v_in / (r_top + r_bot)
    return VoltageDividerResult(
        r_top=r_top,
        r_bot=r_bot,
        target_ratio=target_ratio,
        actual_ratio=actual_ratio,
        v_in=v_in,
        v_out_actual=v_out_actual,
        error_pct=err_pct,
        thevenin_r_ohms=r_th,
        quiescent_current_a=i_q,
    )


@dataclass
class OpAmpStageResult:
    """Analytical sizing and small-signal bandwidth result for an Op-Amp stage."""
    topology: str
    r_in_or_g: float
    r_f: float
    target_gain_vv: float
    actual_gain_vv: float
    gain_db: float
    gain_error_pct: float
    noise_gain_vv: float
    f_3db_hz: float
    z_in_ohms: float


def size_opamp_stage(
    target_gain_vv: float,
    topology: str = "non_inverting",
    r_input_target: float = 10e3,
    gbw_hz: float = 10e6,
    series: str = "E24",
) -> OpAmpStageResult:
    """
    Size an Inverting (`Av = -Rf / Rin`) or Non-Inverting (`Av = 1 + Rf / Rg`) operational
    amplifier stage in commercial E12/E24/E96 values and compute its closed-loop -3dB bandwidth.
    """
    topo = topology.strip().lower().replace("-", "_")
    r_g, _ = snap_to_series(r_input_target, series=series)

    if topo == "inverting":
        mag = abs(target_gain_vv)
        r_f_ideal = r_g * mag
        r_f, _ = snap_to_series(r_f_ideal, series=series)
        actual_av = -(r_f / r_g)
        noise_gain = 1.0 + (r_f / r_g)
        z_in = r_g
        target_signed = -mag
    else:
        if abs(target_gain_vv) < 1.0:
            raise ValueError("Non-inverting op-amp gain must be >= 1.0 V/V.")
        mag = abs(target_gain_vv)
        if math.isclose(mag, 1.0, rel_tol=1e-4):
            r_f = 0.0
            actual_av = 1.0
            noise_gain = 1.0
        else:
            r_f_ideal = r_g * (mag - 1.0)
            r_f, _ = snap_to_series(r_f_ideal, series=series)
            actual_av = 1.0 + (r_f / r_g)
            noise_gain = actual_av
        z_in = 1e12  # High-Z non-inverting input
        target_signed = mag

    err_pct = ((abs(actual_av) - abs(target_signed)) / abs(target_signed)) * 100.0
    gain_db = 20.0 * math.log10(abs(actual_av)) if actual_av != 0 else -float("inf")
    f_3db = gbw_hz / noise_gain
    return OpAmpStageResult(
        topology=topo,
        r_in_or_g=r_g,
        r_f=r_f,
        target_gain_vv=target_signed,
        actual_gain_vv=actual_av,
        gain_db=gain_db,
        gain_error_pct=err_pct,
        noise_gain_vv=noise_gain,
        f_3db_hz=f_3db,
        z_in_ohms=z_in,
    )


@dataclass
class MOSFETOperatingPointResult:
    """Analytical DC and small-signal operating point for a Common-Source or Source-Follower MOSFET stage."""
    v_g: float
    v_s: float
    v_d: float
    v_gs: float
    v_ds: float
    v_ov: float
    i_d: float
    g_m: float
    z_in_stage: float
    voltage_gain_loaded: float
    region: str  # "SATURATION", "TRIODE_RISK", or "CUTOFF"


def size_mosfet_stage(
    v_dd: float,
    r_g1: float,
    r_g2: float,
    r_d: float,
    r_s: float,
    k_p: float = 2e-3,
    v_th: float = 1.5,
    r_s_ac_unbypassed: Optional[float] = None,
    r_load_ac: Optional[float] = None,
    topology: str = "CS",
) -> MOSFETOperatingPointResult:
    """
    Solve the exact quadratic saturation equation I_D = 0.5 * k_p * (V_G - I_D * R_S - V_th)^2
    for an NMOS stage and compute small-signal g_m and loaded voltage gain A_v.
    """
    v_g = v_dd * (r_g2 / (r_g1 + r_g2))
    z_in = (r_g1 * r_g2) / (r_g1 + r_g2)

    if v_g <= v_th:
        return MOSFETOperatingPointResult(
            v_g=v_g, v_s=0.0, v_d=v_dd, v_gs=v_g, v_ds=v_dd, v_ov=0.0,
            i_d=0.0, g_m=0.0, z_in_stage=z_in, voltage_gain_loaded=0.0, region="CUTOFF"
        )

    v_eff = v_g - v_th
    if r_s <= 0:
        i_d = 0.5 * k_p * (v_eff ** 2)
    else:
        # 0.5 * k_p * R_S^2 * I_D^2 - (k_p * R_S * v_eff + 1) * I_D + 0.5 * k_p * v_eff^2 = 0
        a = 0.5 * k_p * (r_s ** 2)
        b = -(k_p * r_s * v_eff + 1.0)
        c = 0.5 * k_p * (v_eff ** 2)
        disc = max(0.0, b * b - 4.0 * a * c)
        # Smaller root guarantees V_GS > V_th
        i_d = (-b - math.sqrt(disc)) / (2.0 * a)

    v_s = i_d * r_s
    v_d = v_dd - i_d * r_d
    v_gs = v_g - v_s
    v_ds = v_d - v_s
    v_ov = max(0.0, v_gs - v_th)
    g_m = k_p * v_ov

    r_s_ac = r_s if r_s_ac_unbypassed is None else r_s_ac_unbypassed
    if topology.upper() in ("CD", "SF"):
        r_s_eff = (r_s * r_load_ac) / (r_s + r_load_ac) if r_load_ac else r_s
        av = (g_m * r_s_eff) / (1.0 + g_m * r_s_eff)
    else:
        r_d_eff = (r_d * r_load_ac) / (r_d + r_load_ac) if (r_load_ac and r_d > 0) else r_d
        av = -(g_m * r_d_eff) / (1.0 + g_m * r_s_ac)

    region = "SATURATION" if v_ds >= v_ov else "TRIODE_RISK"
    return MOSFETOperatingPointResult(
        v_g=v_g,
        v_s=v_s,
        v_d=v_d,
        v_gs=v_gs,
        v_ds=v_ds,
        v_ov=v_ov,
        i_d=i_d,
        g_m=g_m,
        z_in_stage=z_in,
        voltage_gain_loaded=av,
        region=region,
    )


@dataclass
class FilterSizingResult:
    """Analytical component sizing result for passive RC/RLC and active Sallen-Key filters."""
    filter_type: str
    f_target_hz: float
    f_actual_hz: float
    freq_error_pct: float
    q_target: float
    q_actual: float
    bandwidth_hz: float
    components: Dict[str, float]


def size_passive_filter(
    f_cutoff_hz: float,
    filter_type: str = "rc_lowpass",
    r_characteristic: float = 1000.0,
    q_factor: float = 5.0,
    series: str = "E24",
) -> FilterSizingResult:
    """
    Size 1st-order RC (`rc_lowpass`, `rc_highpass`) or 2nd-order RLC (`rlc_series_bandpass`,
    `rlc_parallel_bandpass`) filters using commercial E12/E24/E96 components.
    """
    ftype = filter_type.strip().lower()
    r_snap, _ = snap_to_series(r_characteristic, series=series)
    omega = 2.0 * math.pi * f_cutoff_hz

    if ftype in ("rc_lowpass", "rc_highpass", "rc"):
        c_ideal = 1.0 / (omega * r_snap)
        c_snap, _ = snap_to_series(c_ideal, series=series)
        f_act = 1.0 / (2.0 * math.pi * r_snap * c_snap)
        err = ((f_act - f_cutoff_hz) / f_cutoff_hz) * 100.0
        return FilterSizingResult(
            filter_type=ftype,
            f_target_hz=f_cutoff_hz,
            f_actual_hz=f_act,
            freq_error_pct=err,
            q_target=0.5,
            q_actual=0.5,
            bandwidth_hz=f_act,
            components={"R": r_snap, "C": c_snap},
        )

    # 2nd-order RLC Band-Pass
    if ftype == "rlc_parallel_bandpass":
        c_ideal = q_factor / (omega * r_snap)
        c_snap, _ = snap_to_series(c_ideal, series=series)
        l_ideal = 1.0 / ((omega ** 2) * c_snap)
        l_snap, _ = snap_to_series(l_ideal, series=series)
        f_act = 1.0 / (2.0 * math.pi * math.sqrt(l_snap * c_snap))
        q_act = r_snap * math.sqrt(c_snap / l_snap)
    else:
        l_ideal = (q_factor * r_snap) / omega
        l_snap, _ = snap_to_series(l_ideal, series=series)
        c_ideal = 1.0 / ((omega ** 2) * l_snap)
        c_snap, _ = snap_to_series(c_ideal, series=series)
        f_act = 1.0 / (2.0 * math.pi * math.sqrt(l_snap * c_snap))
        q_act = (1.0 / r_snap) * math.sqrt(l_snap / c_snap)

    err = ((f_act - f_cutoff_hz) / f_cutoff_hz) * 100.0
    bw = f_act / q_act if q_act > 0 else f_act
    return FilterSizingResult(
        filter_type=ftype,
        f_target_hz=f_cutoff_hz,
        f_actual_hz=f_act,
        freq_error_pct=err,
        q_target=q_factor,
        q_actual=q_act,
        bandwidth_hz=bw,
        components={"R": r_snap, "L": l_snap, "C": c_snap},
    )


def size_sallen_key_filter(
    f_cutoff_hz: float,
    q_factor: float = 0.7071,  # Butterworth = 0.7071, Bessel = 0.5774
    c_base: float = 10e-9,
    series: str = "E24",
) -> FilterSizingResult:
    """
    Size a 2nd-order unity-gain Sallen-Key Active Low-Pass Filter with commercial E12/E24/E96 values.
    For unity gain with equal resistors (R1 = R2 = R), C1 = 2 * Q * C_eq and C2 = C_eq / (2 * Q).
    """
    c2_snap, _ = snap_to_series(c_base, series=series)
    c1_ideal = c2_snap * (4.0 * (q_factor ** 2))
    c1_snap, _ = snap_to_series(c1_ideal, series=series)

    omega = 2.0 * math.pi * f_cutoff_hz
    r_ideal = 1.0 / (omega * math.sqrt(c1_snap * c2_snap))
    r1_snap, _ = snap_to_series(r_ideal, series=series)
    r2_snap = r1_snap

    f_act = 1.0 / (2.0 * math.pi * math.sqrt(r1_snap * r2_snap * c1_snap * c2_snap))
    q_act = math.sqrt(r1_snap * r2_snap * c1_snap * c2_snap) / (c2_snap * (r1_snap + r2_snap))
    err = ((f_act - f_cutoff_hz) / f_cutoff_hz) * 100.0
    return FilterSizingResult(
        filter_type="sallen_key_lowpass",
        f_target_hz=f_cutoff_hz,
        f_actual_hz=f_act,
        freq_error_pct=err,
        q_target=q_factor,
        q_actual=q_act,
        bandwidth_hz=f_act,
        components={"R1": r1_snap, "R2": r2_snap, "C1": c1_snap, "C2": c2_snap},
    )


@dataclass
class LMatchingResult:
    """Analytical RF L-network impedance matching synthesis result."""
    r_source: float
    r_load: float
    f_0_hz: float
    q_network: float
    x_series_ohms: float
    x_shunt_ohms: float
    l_henries: float
    c_farads: float
    topology_note: str


def size_l_matching_network(
    r_source: float,
    r_load: float,
    f_0_hz: float,
    dc_feed_lowpass: bool = True,
    series: str = "E24",
) -> LMatchingResult:
    """
    Synthesize a Narrowband RF L-Section Impedance Matching Network between `r_source` and `r_load`
    at `f_0_hz` using commercial E12/E24/E96 L and C values.
    """
    if math.isclose(r_source, r_load, rel_tol=1e-3):
        raise ValueError("r_source and r_load are already matched.")
    r_high = max(r_source, r_load)
    r_low = min(r_source, r_load)
    q_s = math.sqrt((r_high / r_low) - 1.0)
    x_s = q_s * r_low
    x_p = r_high / q_s
    omega = 2.0 * math.pi * f_0_hz

    if dc_feed_lowpass:
        # Series L on low-Z side, Shunt C across high-Z side
        l_ideal = x_s / omega
        c_ideal = 1.0 / (omega * x_p)
        note = "Low-pass L-match (Series L on low-Z port, Shunt C across high-Z port)"
    else:
        # Series C on low-Z side, Shunt L across high-Z side
        c_ideal = 1.0 / (omega * x_s)
        l_ideal = x_p / omega
        note = "High-pass L-match (Series C on low-Z port, Shunt L across high-Z port)"

    l_snap, _ = snap_to_series(l_ideal, series=series)
    c_snap, _ = snap_to_series(c_ideal, series=series)
    return LMatchingResult(
        r_source=r_source,
        r_load=r_load,
        f_0_hz=f_0_hz,
        q_network=q_s,
        x_series_ohms=x_s,
        x_shunt_ohms=x_p,
        l_henries=l_snap,
        c_farads=c_snap,
        topology_note=note,
    )

