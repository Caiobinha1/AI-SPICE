# Contributing to AI-SPICE

Contributions to **AI-SPICE** are welcome! Whether you are adding analytical sizing models for new electronic domains, expanding the 16-pixel grid `.asc` symbol library, or submitting verified circuit benchmarks to `showcase/`, please follow the guidelines below.

---

## 1. Development Setup

```bash
git clone https://github.com/Caiobinha1/AI-SPICE.git
cd AI-SPICE
pip install -e ".[dev,pdf]"
pytest
```

## 2. Adding New Circuit Symbols to `asc_compiler.py`

1. Calibrate the exact pin coordinates `(dx, dy)` in `R0` orientation from LTspice's native `.asy` file (`%LOCALAPPDATA%\LTspice\lib\sym\`).
2. Register the pin map in `SYMBOL_PIN_OFFSETS` inside `src/ia_spice/asc_compiler.py`.
3. Verify with `verify_asc_netlist` (`LTspice.exe -netlist`) that placing and wiring the symbol yields `unconnected_pins == []` (`NC_ = 0`).

## 3. Submitting a Verified Circuit Benchmark (`showcase/`)

Every benchmark submitted to `showcase/` must include:
1. The SPICE netlist (`.cir`) with deterministic `.meas` statements and `.options plotwinsize=0`.
2. The compiled 16-pixel grid `.asc` schematic (`style="A_textbook_wired"`) with **100% physical orthogonal `WIRE` connections on all signal nets** and `NC_ = 0`.
3. A `README.md` or reconciliation table comparing Target Specifications, Analytical Calculations, and LTspice `.meas` results (`< 5%` error).
