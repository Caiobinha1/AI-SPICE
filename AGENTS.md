# AGENTS.md — Instructions for OpenAI Codex & Autonomous Coding Agents

You are operating inside the **IA-SPICE** repository (`c:\analogicos\IA-SPICE`), a closed-loop AI-for-SPICE & LTspice engineering framework.

Whenever the user asks you to **design, calculate, simulate, reconstruct from an image, inspect, troubleshoot, or modify an electronic circuit or LTspice file (`.asc`, `.cir`, `.net`, `.sub`, `.lib`, `.asy`, `.plt`, `.log`)**, you **MUST** use the `ia-spice` CLI and/or Python library `ia_spice` rather than guessing component values or hand-writing raw `.asc` coordinates without verification.

---

## 1. Environment & Quick Check

- **Python package:** `ia_spice` is already installed in editable mode (`pip install -e .`).
- **CLI binary:** Run via `python -m ia_spice.cli <command>` or `ia-spice <command>`.
- **Skill methodology:** Read `skills/ia-spice/SKILL.md` for the full 6-phase engineering methodology.

```powershell
python -m ia_spice.cli doctor
```

---

## 2. CLI Commands Available to You

1. **Snap component values to commercial E12/E24/E96 series:**
   ```powershell
   python -m ia_spice.cli snap 4530 --series E24
   ```
2. **Run headless LTspice simulation & parse `.meas` + diagnostics:**
   ```powershell
   python -m ia_spice.cli sim path/to/circuit.cir
   ```
3. **Convert any SPICE netlist (`.cir`/`.net`) to a clean LTspice `.asc` schematic (3 layout styles):**
   ```powershell
   python -m ia_spice.cli netlist-to-asc path/to/circuit.cir --style A_textbook_wired --verify
   # Available styles:
   # - A_textbook_wired (classic wired layout + automatic .plt waveform config)
   # - B_power_rail_bus (horizontal VCC/GND buses)
   # - C_modular_blocks (grouped by functional stages, clean wireless horizontal flags)
   ```
4. **Formally verify an `.asc` schematic (`LTspice.exe -netlist`, checks `NC_ = 0` floating pins):**
   ```powershell
   python -m ia_spice.cli verify-asc path/to/circuit.asc
   ```
5. **Fix overlapping labels & move `.tran`/`.meas` directives to a clean footer on any `.asc`:**
   ```powershell
   python -m ia_spice.cli beautify-asc path/to/circuit.asc
   ```
6. **Inspect any LTspice project folder or file (`.asc`, `.cir`, `.sub`, `.lib`, `.asy`, `.plt`, `.log`):**
   ```powershell
   python -m ia_spice.cli inspect path/to/project_or_file
   ```
7. **Reconstruct a circuit from a schematic photo/image (via Vision JSON spec):**
   ```powershell
   python -m ia_spice.cli import-vision path/to/vision_spec.json --mode ask_user -o output_dir
   python -m ia_spice.cli import-vision path/to/vision_spec.json --mode autonomous -o output_dir
   ```

---

## 3. Python API Quick Reference (`import ia_spice`)

If you write a Python script to design or modify a circuit, use:

- **Analytical Sizing (`ia_spice.sizing`):**
  - `snap_to_series(val, "E24")`
  - `size_bjt_stage(vcc=12.0, ic_target_ma=2.0, gain_target=10.0, r_load=10000.0, topology="CE")`
  - `size_colpitts_vco(f_target_hz=5e6, l_tank_h=4.7e-6, c_varactor_nom_f=55.6e-12)`
  - `StageContract(stage_name=..., z_in_ohm=..., z_out_ohm=...).verify_cascade(next_stage)`
- **Simulation & `.meas` Parsing (`ia_spice.simulator`):**
  - `LTspiceRunner().run_simulation(netlist_path)`
- **Schematic Compilation (`ia_spice.asc_compiler`):**
  - `SchematicBuilder()` (always uses `sanitize_visual_layout()` automatically so no labels rotate vertically or overlap components, and all `.tran`/`.meas` directives go to a clean footer $\ge 160\text{ px}$ below the circuit).
- **Universal LTspice Project Editor (`ia_spice.ltspice_project`):**
  - `inspect_ltspice_project(path)`
  - `modify_asc_component(asc_path, {"R1": "10k", "C1": "100n"})`
  - `add_component_to_asc(asc_path, inst_name="C_BYP", symbol="cap", value="100n", ...)`
  - `beautify_asc_file(asc_path)`
  - `compile_netlist_to_asc(netlist_path, style="A_textbook_wired")`

---

## 4. Mandatory Quality Rules ("Good Engineering Behavior")

1. **Never deliver an `.asc` file without running `verify-asc`** (`NC_ = 0` floating pins required).
2. **Never use non-standard resistor/capacitor values** — always snap to E24/E12.
3. **Always verify simulation `.meas` results** against theoretical targets before concluding a task.
