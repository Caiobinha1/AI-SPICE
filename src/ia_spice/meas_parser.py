"""
Deterministic LTspice `.log` and `.meas` Parser (`ia_spice.meas_parser`).

Why this module exists:
-----------------------
LTspice outputs `.meas` results in 4 distinct formats inside the `.log` file depending on
simulation mode (`.tran`, `.ac`, `.op`, and `.step`):
1. Scalar transient/DC:
   `fosc: 100/t100=4.99574e+06` or `t100=2.00171e-05 FROM 0.001 TO 0.002`
2. Scalar AC (dB, phase):
   `c_var: -im(...)=(-213.688dB,0°)`
3. Stepped parameter/temperature tables (`.step param` / `.step temp`):
   ```
   .step vc=2
   .step vc=2.5
   ...
   Measurement: fosc
     step  100/t100
        1  4.9888e+06
        2  4.98938e+06
   ```
4. Failed measurements:
   `Measurement "fosc" FAIL'ed`

This module parses all 4 formats deterministically into structured Python objects, automatically
converting dB values to linear magnitude (`10^(dB/20)`) when requested.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union


@dataclass
class ComplexDBValue:
    """Represents an LTspice AC measurement reported as `(dB, phase_deg)`."""
    db: float
    phase_deg: float

    @property
    def linear_magnitude(self) -> float:
        """Convert dB (`20*log10(|X|)`) to linear magnitude `|X| = 10^(dB/20)`."""
        return 10.0 ** (self.db / 20.0)


@dataclass
class ParsedLogResult:
    """Structured representation of an LTspice `.log` file."""
    log_path: str
    step_variable: Optional[str] = None
    step_values: List[float] = field(default_factory=list)
    scalars: Dict[str, Union[float, ComplexDBValue]] = field(default_factory=dict)
    stepped: Dict[str, List[Union[float, ComplexDBValue, None]]] = field(default_factory=dict)
    failed_measurements: List[str] = field(default_factory=list)
    fatal_errors: List[str] = field(default_factory=list)
    elapsed_seconds: Optional[float] = None

    def get_scalar(self, name: str, linearize_db: bool = True) -> Optional[float]:
        """Retrieve a scalar measurement by case-insensitive name."""
        key = name.lower()
        val = self.scalars.get(key)
        if val is None:
            return None
        if isinstance(val, ComplexDBValue):
            return val.linear_magnitude if linearize_db else val.db
        return float(val)

    def get_stepped_series(self, name: str, linearize_db: bool = True) -> List[Optional[float]]:
        """Retrieve a stepped measurement series by case-insensitive name."""
        key = name.lower()
        series = self.stepped.get(key, [])
        out: List[Optional[float]] = []
        for v in series:
            if v is None:
                out.append(None)
            elif isinstance(v, ComplexDBValue):
                out.append(v.linear_magnitude if linearize_db else v.db)
            else:
                out.append(float(v))
        return out


_DB_TOKEN_RE = re.compile(
    r"^\(\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)dB\s*,\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)[^\)]*\)",
    re.IGNORECASE,
)

_SCALAR_LINE_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(?::\s*[^=]+)?=\s*(\([^\)]+\)|[+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)(?:\s+at\s+([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?))?",
    re.IGNORECASE,
)

_STEP_DECL_RE = re.compile(
    r"^\.step\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)",
    re.IGNORECASE,
)

_FAIL_RE = re.compile(r'Measurement\s+"([^"]+)"\s+FAIL\'ed', re.IGNORECASE)


def _parse_value_token(token: str) -> Union[float, ComplexDBValue, None]:
    token = token.strip()
    if token.upper().startswith("FAIL"):
        return None
    db_match = _DB_TOKEN_RE.match(token)
    if db_match:
        return ComplexDBValue(db=float(db_match.group(1)), phase_deg=float(db_match.group(2)))
    try:
        return float(token)
    except ValueError:
        return None


def parse_ltspice_log(log_path: str) -> ParsedLogResult:
    """
    Parse an LTspice `.log` file and extract all scalar and stepped `.meas` results,
    `.step` sweep values, failed measurements, and fatal SPICE errors.
    """
    if not os.path.exists(log_path):
        raise FileNotFoundError(f"LTspice log file not found: {log_path}")

    with open(log_path, "r", encoding="latin-1", errors="replace") as f:
        raw_text = f.read()

    result = ParsedLogResult(log_path=log_path)
    lines = [line.strip() for line in raw_text.splitlines()]

    # Metadata & footer keys that look like `name = value` but are not user `.meas` directives
    footer_keys = {
        "tnom", "temp", "method", "totiter", "traniter", "tranpoints",
        "accept", "rejected", "matrix", "fillins", "solver",
    }

    current_table_meas: Optional[str] = None
    skip_header_line = False

    for line in lines:
        if not line:
            current_table_meas = None
            continue

        # Check fatal error lines
        if line.lower().startswith("fatal error:"):
            result.fatal_errors.append(line)
            continue

        # Check elapsed time
        if line.lower().startswith("total elapsed time:"):
            m_time = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*seconds", line, re.IGNORECASE)
            if m_time:
                result.elapsed_seconds = float(m_time.group(1))
            continue

        # Check failed measurements
        m_fail = _FAIL_RE.search(line)
        if m_fail:
            meas_name = m_fail.group(1).lower()
            if meas_name not in result.failed_measurements:
                result.failed_measurements.append(meas_name)
            continue

        # Check `.step var=val` declarations
        m_step = _STEP_DECL_RE.match(line)
        if m_step:
            var_name = m_step.group(1)
            val = float(m_step.group(2))
            result.step_variable = var_name
            result.step_values.append(val)
            continue

        # Check `Measurement: <name>` header for stepped tables
        if line.lower().startswith("measurement:"):
            current_table_meas = line.split(":", 1)[1].strip().lower()
            result.stepped.setdefault(current_table_meas, [])
            skip_header_line = True
            continue

        if current_table_meas is not None:
            if skip_header_line and line.lower().startswith("step"):
                skip_header_line = False
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                parsed_val = _parse_value_token(parts[1])
                result.stepped[current_table_meas].append(parsed_val)
                if parsed_val is None and current_table_meas not in result.failed_measurements:
                    result.failed_measurements.append(current_table_meas)
                continue
            else:
                current_table_meas = None

        # Check scalar measurement line
        m_scalar = _SCALAR_LINE_RE.match(line)
        if m_scalar:
            name_key = m_scalar.group(1).lower()
            if name_key in footer_keys:
                continue
            # If the line ends with `AT <freq_or_time>` (from a `.meas ... WHEN ...` directive),
            # the measured X-axis value is in group(3); otherwise use group(2).
            if m_scalar.group(3) is not None and not m_scalar.group(2).strip().startswith("("):
                val_obj = _parse_value_token(m_scalar.group(3))
            else:
                val_obj = _parse_value_token(m_scalar.group(2))
            if val_obj is not None:
                result.scalars[name_key] = val_obj

    return result
