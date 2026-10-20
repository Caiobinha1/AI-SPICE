# AI-SPICE — Autonomous Agent Operating Manual (`AGENTS.md`)

Whenever a user asks you to design, size, simulate, debug, optimize, or generate LTspice schematics (`.asc`) and SPICE netlists (`.cir`) for **any electronic circuit** (analog, RF, op-amp, MOSFET/BJT/JFET, active/passive filter, oscillator, sensor front-end, audio amplifier, or power converter), **always use the `ai_spice` toolkit and CLI (`python -m ai_spice.cli`) instead of guessing raw `.asc` coordinates or untested netlist values.**

---

## 1. Environment Check & CLI Quick Reference

Run commands from the repository root (`pip install -e .`):

```bash
# 1. Verify LTspice 24 / XVII installation
python -m ai_spice.cli doctor

# 2. Snap any calculated R / L / C value to commercial E12 / E24 / E96 series
python -m ai_spice.cli snap 10.83k --series E24

# 3. Compile any SPICE netlist (.cir/.net) into a 16-pixel grid, signal-wired LTspice schematic (.asc)
python -m ai_spice.cli netlist-to-asc <input.cir> <output.asc> --style A_textbook_wired --verify

# 4. Run headless LTspice batch simulation (-b), parse all .meas metrics, and run physics diagnostics
python -m ai_spice.cli sim <circuit.cir_or_asc>

# 5. Formally verify an .asc schematic via LTspice -netlist (guarantees NC_ = 0 floating pins)
python -m ai_spice.cli verify-asc <circuit.asc>

# 6. Beautify & wire an existing or legacy .asc file
python -m ai_spice.cli beautify-asc <circuit.asc>

# 7. Inspect an entire LTspice project directory (.asc, .cir, .sub, .lib, .asy, .plt, .log)
python -m ai_spice.cli inspect <project_dir_or_file>

# 8. Reconstruct a complete LTspice project from a schematic image / photo
python -m ai_spice.cli import-vision <vision_spec.json> --mode ask_user -o <output_dir>
```

---

## 2. Mandatory 7-Phase Equation-Grounded Design Workflow

1. **Specification & Block Decomposition**:
   - Break multi-stage systems into functional blocks and define each stage's `StageContract(name, z_in_ohms, z_out_ohms, unloaded_gain_vv)` (`ai_spice.sizing`).
2. **First-Principles Analytical Sizing (Before SPICE)**:
   - Use `ai_spice.sizing` (`size_opamp_stage`, `size_bjt_OperatingPoint`, `size_mosfet_stage`, `size_passive_filter`, `size_sallen_key_filter`, `size_colpitts_tank`, `evaluate_barkhausen_colpitts`, `size_l_matching_network`, `size_voltage_divider`, `snap_to_series`) so every component starts from exact equations and standard E12/E24/E96 values.
   - Check inter-stage loading with `check_cascade_loading(driver, load)` to prevent gain collapse or oscillator pulling.
3. **Block-by-Block SPICE Verification**:
   - Simulate each block in headless LTspice (`run_ltspice_batch`) with deterministic `.meas` statements and `.options plotwinsize=0`.
4. **Closed-Loop Physics Diagnostics**:
   - Inspect `.meas` outputs and `sim.diagnostics`. Adjust parameters physically until target specifications are met within `< 5%` tolerance.
5. **Topological Signal-Flow `.asc` Compilation (`NC_ = 0`)**:
   - **Never place components in disconnected label-only islands.**
   - Always compile `.cir` netlists to `.asc` using `compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")` (or `python -m ai_spice.cli netlist-to-asc ... --style A_textbook_wired --verify`).
   - This organizes stages left-to-right following signal flow, routes **100% of signal and feedback nets with physical orthogonal `WIRE` segments**, keeps only DC power rails (`VCC`, `VEE`, `VDD`, `VSS`) and Ground (`0`) on local flags, and places `.tran`/`.meas` directives in a clean footer below the circuit.
6. **Formal Netlist Isomorphism Verification**:
   - Always run `verify_asc_netlist(asc_path, reference_cir_path=cir_path)` and assert `unconnected_pins == []` (`NC_ = 0`).
7. **Engineering Reconciliation Report**:
   - Generate the Markdown/PDF reconciliation table (*Target vs. Analytical Calc vs. LTspice `.meas` vs. Error %*) using `ai_spice.reporting`.
