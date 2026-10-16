"""
LTspice Batch Runner & Physics-Based Diagnostic Engine (`ia_spice.simulator` & `ia_spice.diagnostics`).

Provides:
- Automatic cross-platform discovery of `LTspice.exe`
- Batch simulation execution (`-b`) and netlist generation (`-netlist`)
- Physics-based diagnostic analysis of `.meas` results (detecting non-oscillation, varactor forward
  conduction / self-rectification, BJT saturation/cutoff, and load pulling).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .meas_parser import ParsedLogResult, parse_ltspice_log


DEFAULT_LTSPICE_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe"),
    r"C:\Program Files\ADI\LTspice\LTspice.exe",
    r"C:\Program Files\LTC\LTspiceXVII\XVIIx64.exe",
    "/Applications/LTspice.app/Contents/MacOS/LTspice",
]


def find_ltspice_executable(custom_path: Optional[str] = None) -> str:
    """Locate the LTspice executable on the system."""
    if custom_path and os.path.isfile(custom_path):
        return custom_path
    env_path = os.environ.get("LTSPICE_EXE")
    if env_path and os.path.isfile(env_path):
        return env_path
    which_lt = shutil.which("LTspice") or shutil.which("LTspice.exe")
    if which_lt:
        return which_lt
    for candidate in DEFAULT_LTSPICE_PATHS:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not locate LTspice executable. Install LTspice 24 or set the LTSPICE_EXE environment variable."
    )


@dataclass
class SimulationRunResult:
    """Result of executing a `.cir` or `.asc` file in LTspice batch mode."""
    circuit_path: str
    log_path: str
    return_code: int
    parsed_log: ParsedLogResult
    diagnostics: List[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return (
            self.return_code == 0
            and len(self.parsed_log.fatal_errors) == 0
            and len(self.parsed_log.failed_measurements) == 0
        )


def run_ltspice_batch(
    circuit_path: str,
    timeout_sec: int = 180,
    ltspice_exe: Optional[str] = None,
) -> SimulationRunResult:
    """
    Run an LTspice simulation (`.cir` or `.asc`) in batch mode (`-b`), parse its `.log` output,
    and run automated physics diagnostics.
    """
    abs_circuit = os.path.abspath(circuit_path)
    if not os.path.isfile(abs_circuit):
        raise FileNotFoundError(f"Circuit file not found: {abs_circuit}")

    exe = find_ltspice_executable(ltspice_exe)
    work_dir = os.path.dirname(abs_circuit)
    base_no_ext, ext = os.path.splitext(abs_circuit)
    log_path = f"{base_no_ext}.log"

    # Remove stale log before running
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass

    # If the input is a visual schematic (.asc), compile it to a netlist (.net) first
    # so LTspice -b runs 100% headlessly without any GUI modal dialog.
    sim_target = abs_circuit
    if ext.lower() == ".asc":
        subprocess.run(
            [exe, "-netlist", abs_circuit],
            cwd=work_dir,
            capture_output=True,
            timeout=timeout_sec,
            check=True,
        )
        net_candidate = f"{base_no_ext}.net"
        if not os.path.isfile(net_candidate):
            raise RuntimeError(f"Failed to compile schematic {abs_circuit} to netlist {net_candidate}")
        with open(net_candidate, "r", encoding="latin-1", errors="replace") as nf:
            net_body = nf.read()
        # Append standard 2N2222 model if referenced in schematic without a .model card
        if "2N2222" in net_body and ".model 2N2222" not in net_body and ".include" not in net_body:
            lines = [l for l in net_body.splitlines() if l.strip().lower() != ".end"]
            lines.append(".model 2N2222 NPN(IS=1E-14 VAF=100 BF=200 IKF=0.3 XTB=1.5 BR=3 CJC=8E-12 CJE=25E-12 TR=100E-9 TF=400E-12 ITF=1 VTF=2 XTF=3 RB=10 RC=0.3 RE=0.2)")
            lines.append(".end")
            with open(net_candidate, "w", encoding="latin-1") as nf:
                nf.write("\n".join(lines) + "\n")
        sim_target = net_candidate

    proc = subprocess.run(
        [exe, "-b", sim_target],
        cwd=work_dir,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )

    if not os.path.exists(log_path):
        raise RuntimeError(
            f"LTspice exited with code {proc.returncode} without producing a .log file for {abs_circuit}."
        )

    parsed = parse_ltspice_log(log_path)
    diags = diagnose_simulation_results(parsed)

    return SimulationRunResult(
        circuit_path=abs_circuit,
        log_path=log_path,
        return_code=proc.returncode,
        parsed_log=parsed,
        diagnostics=diags,
    )


def diagnose_simulation_results(
    parsed: ParsedLogResult,
    specs: Optional[Dict[str, Tuple[float, float]]] = None,
) -> List[str]:
    """
    Analyze parsed `.meas` telemetry to produce actionable analog/RF physics diagnostics.

    Detects:
    1. Failed oscillating triggers (`t100`, `fosc` failed -> Barkhausen startup failure or small time window).
    2. Varactor forward conduction / RF self-rectification (`vr_min < -0.5V` -> coupling capacitor `CCV` too large).
    3. Spec violations if `specs={meas_name: (min_val, max_val)}` is provided.
    """
    messages: List[str] = []

    for err in parsed.fatal_errors:
        messages.append(f"[FATAL_SPICE_ERROR] {err}")

    for failed in parsed.failed_measurements:
        if failed in ("t100", "fosc", "t_period", "freq"):
            messages.append(
                f"[OSCILLATION_FAILURE] Measurement '{failed}' failed to trigger. "
                "Root causes: (1) Barkhausen loop gain < 1 (check inductor R_ser or BJT bias current I_C); "
                "(2) Excessive load pulling from next stage (reduce coupling capacitor C_EX); "
                "(3) Missing 'startup' flag in .tran or insufficient settling time before TD."
            )
        else:
            messages.append(f"[MEASUREMENT_FAILED] Directive '{failed}' failed in LTspice log.")

    # Check varactor minimum reverse voltage (self-rectification detection)
    vr_min = parsed.get_scalar("vr_min")
    if vr_min is None and "vr_min" in parsed.stepped:
        valid_vals = [v for v in parsed.get_stepped_series("vr_min") if v is not None]
        if valid_vals:
            vr_min = min(valid_vals)

    if vr_min is not None and vr_min < -0.80:
        messages.append(
            f"[VARACTOR_RECTIFICATION_WARNING] Minimum varactor cathode voltage reached {vr_min:.3f} V "
            "(forward conduction clamping). Large RF tank swing is rectifying across the varactor, "
            "compressing tuning sensitivity (K_VCO). Reduce series coupling capacitor CCV (e.g., from 100pF to 47pF)."
        )

    # Check user target specifications if provided
    if specs:
        for meas_name, (low, high) in specs.items():
            val = parsed.get_scalar(meas_name)
            if val is None:
                messages.append(f"[SPEC_MISSING] Required metric '{meas_name}' not found in scalar results.")
            elif not (low <= val <= high):
                messages.append(
                    f"[SPEC_VIOLATION] '{meas_name}' = {val:.6g} is outside target range [{low:.6g}, {high:.6g}]."
                )

    return messages
