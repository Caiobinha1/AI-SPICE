# Contributing to IA-SPICE

Thank you for your interest in contributing to **IA-SPICE**! Our goal is to maintain a rigorous, equation-grounded, and formally verified framework for AI-assisted analog and RF circuit design.

## Good Project Behavior & Engineering Standards

Every new feature, circuit sizing model, or schematic symbol added to IA-SPICE must follow these three rules:

1. **Analytical Grounding First**:
   - Any new circuit topology helper in `src/ia_spice/sizing.py` must include closed-form equations with clear docstrings and physical units (Ohms, Farads, Henries, Watts, Volts, Amperes).
2. **16-Pixel Grid & Formal Netlist Verification**:
   - Any new LTspice symbol added to `SYMBOL_PIN_OFFSETS` in `src/ia_spice/asc_compiler.py` must be calibrated against `LTspice.exe -netlist` so that every pin falls on an exact multiple of 16 pixels and produces zero unconnected pins (`NC_ = 0`).
3. **Automated Test Coverage**:
   - Every pull request must include unit and/or live LTspice batch verification tests in `tests/` and pass the full suite:
     ```bash
     python -m unittest discover -s tests -v
     ```
