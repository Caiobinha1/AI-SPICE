---
name: ia-spice-engineer
description: |
  Autonomous Equation-Grounded Analog & RF Circuit Design Protocol using IA-SPICE and LTspice.
  Activate whenever the user wants to design, size, simulate, debug, or generate LTspice schematics
  (`.asc` / `.cir`) and engineering reports for any electronic circuit (from simple RC/RLC filters
  and op-amp/BJT stages to multi-stage RF transmitters, VCOs, modulators, and power amplifiers).
---

# IA-SPICE Protocol: 7-Stage Equation-Grounded Circuit Engineering

> [!IMPORTANT]
> **WHY MOST AI CIRCUIT DESIGNS FAIL (AND HOW THIS PROTOCOL PREVENTS IT):**
> Never write a monolithic multi-stage SPICE netlist from scratch by guessing component values.
> Monolithic guesses fail due to:
> 1. **Load Pulling & Impedance Collapse**: Connecting Stage $N+1$ directly to Stage $N$ kills gain or extinguishes oscillation.
> 2. **Barkhausen Startup Failure**: LC/Colpitts/Hartley oscillators fail to start when inductor series loss ($R_{ser}$) exceeds negative resistance ($R_{neg} = -g_m / (\omega^2 C_1 C_2)$).
> 3. **Nonlinear Self-Rectification**: Large RF tank swings rectify across varactors or base-emitter junctions if coupling capacitance ($C_{CV}$) is too large.
> 4. **Broken `.asc` Schematics**: Guessing coordinates off the 16-pixel grid creates floating pins (`NC_01`) or unintended wire-to-pin short circuits.

---

## Mandatory 7-Phase Engineering Workflow

### Phase 1: Specification & Block Decomposition
1. Define global requirements: operating frequency ($f_0$), bandwidth, load impedance ($R_L$, e.g., $50\,\Omega$), output power ($P_{out}$), supply voltage ($V_{CC}$), and maximum DC power budget ($P_{DC,max}$).
2. Decompose the system into decoupled functional blocks (`Block 1 .. Block N`).
3. Establish a **`StageContract`** (`ia_spice.sizing.StageContract`) for every block:
   - Input impedance $Z_{in}$
   - Output impedance $Z_{out}$
   - Expected unloaded/loaded voltage gain $A_v$
   - Maximum peak current capacity $I_{max}$

### Phase 2: Analytical First-Principles Sizing (Before Any SPICE!)
1. Compute DC operating points ($V_B, V_E, V_C, V_{CE}, I_C, g_m, r_\pi$) using `ia_spice.sizing.size_bjt_OperatingPoint` or analytical equations.
2. Snap all passive components to standard commercial series (**E12 / E24 / E96**) using `ia_spice.sizing.snap_to_series`.
3. For oscillators, compute tank resonance (`size_colpitts_tank`) and prove Barkhausen startup margin $g_m / g_{m,min} \ge 3$ (`evaluate_barkhausen_colpitts`).
4. Verify inter-stage cascade loading (`check_cascade_loading`):
   - An oscillator must never have more than **10%–15%** of its maximum fundamental RF current ($I_{RF1,max} \approx 2 I_{DC}$) drawn by the next stage. Use weak capacitive coupling ($C_{EX}$) or an emitter-follower buffer.

### Phase 3: Isolated Block-by-Block SPICE Verification (`S1 .. SN`)
1. Create a standalone `.cir` netlist for **each block individually**, terminated with an equivalent load resistor representing the next stage's $Z_{in}$.
2. Embed deterministic `.meas` directives inside the steady-state window (`FROM=... TO=...` after initial transients settle):
   - Always include `.options plotwinsize=0` for high-accuracy waveform/FFT post-processing.
   - For oscillators, include `startup` in `.tran` and verify numerical timestep convergence (`S5`) by halving max timestep and checking $\Delta f_{osc} < 0.1\%$.
3. Execute via `ia_spice.simulator.run_ltspice_batch` and inspect `.meas` outputs.

### Phase 4: Closed-Loop Physics Diagnosis & Tuning
1. Compare analytical predictions against LTspice `.meas` scalars.
2. Run `diagnose_simulation_results`. Address root causes physically:
   - If `[VARACTOR_RECTIFICATION_WARNING]` ($V_{R,min} < -0.8\,\text{V}$): reduce series coupling capacitor $C_{CV}$ to attenuate RF swing across the varactor.
   - If `[OSCILLATION_FAILURE]`: increase $I_C$ ($g_m$), reduce inductor $R_{ser}$, or optimize $C_1/C_2$ ratio toward $n \approx 0.4\text{–}0.5$.

### Phase 5: Full System Integration
1. Connect all validated blocks into the complete system netlist (`S6`).
2. Verify that connecting stages does not shift carrier frequency ($<1\%$ pulling) or clip output waveforms, and verify overall DC power budget.

### Phase 6: 16-Pixel Grid `.asc` Compilation, Functional Block Organization & Full Signal Wiring
1. Compile validated `.cir` netlists into LTspice `.asc` schematics using `ia_spice.ltspice_project.compile_netlist_to_asc(netlist_path, output_asc_path, style="A_textbook_wired")` (or CLI `ia-spice netlist-to-asc <file.cir> --style A_textbook_wired --verify`):
   - **NEVER place components in isolated columns connected only by `FLAG` text labels.**
   - **Connect 100% of signal, bias, and feedback nets using physical orthogonal `WIRE` segments** (`wire_signal_nets_on_schematic`), keeping **ONLY** power supply rails (`VCC`, `VEE`, `VDD`, `VSS`) and Ground (`0`) on local flags.
   - Organize functional stages left-to-right so the schematic is immediately readable by an engineer:
     1. Input AC source + series coupling capacitor + input bias/termination;
     2. Amplifier / Op-Amp stages (`XU1`, `XU2`, etc.) with their feedback dividers and compensation networks positioned directly underneath each stage;
     3. Diode bias chains (`RBP-D1-D2-RBN`) and complementary Push-Pull output stages (`Q1` NPN `R0`, `RE1`, `RE2`, `Q2` PNP `M180`) wired vertically top-to-bottom;
     4. Output load (`RLOAD`) and Zobel / RC filter networks;
     5. Dedicated Power Supply & Decoupling Block (`VCC`, `VEE`, `CDP`, `CDN`, `CBP`, `CBN`) at the right.
2. **Mandatory Formal Verification (`ia_spice.netlist_verifier.verify_asc_netlist`)**:
   - Compile every `.asc` via `LTspice.exe -netlist`.
   - Assert **`unconnected_pins == []` (`NC_ = 0`)**.
   - Assert **100% node connectivity isomorphism** against the validated `.cir` netlist.

### Phase 7: Automated Engineering Report & Lab Testbench Guide
1. Generate the reconciliation table (*Target vs. Analytical vs. LTspice `.meas` vs. Error %*) and 300 DPI plots using `ia_spice.reporting`.
2. Include perturbation experiments (e.g., varying input amplitude, load resistance, or inductor $R_{ser}$) so users can observe physical limits firsthand.

---

## 🖼️ Special Workflow: Reconstructing a Circuit from a Photo / Schematic Image (Vision-to-SPICE)

When the user sends a photo, screenshot, or hand-drawn sketch of a schematic:

1. **Step 1 — Visual Topology & Parameter Extraction**:
   - Inspect the image and list every component (`inst_name`, `comp_type`, connected `nodes`, `role_hint`) into a `VisionCircuitGraph` (`ia_spice.vision_to_spice.VisionCircuitGraph`).
   - **NEVER guess unreadable or missing values silently.** If a resistor/capacitor has no value written on the drawing, a transistor has no part number, or a `+VCC` / `Vin` port has no numeric voltage/frequency, set its `value="?"` (`None`).
2. **Step 2 — Missing-Information Audit (`audit_circuit_from_image` / `ia_spice_import_vision_graph`)**:
   - Run `resolve_and_compile_vision_graph(graph, mode="ask_user")`.
   - If `audit.is_complete == False`, present `audit.format_user_prompt()` to the user. This table explicitly shows:
     - Which component values, device models, or supply voltages are missing from the image;
     - The analytical suggestion calculated by IA-SPICE (`suggested_value` + `sizing_rationale`);
     - And asks the user: **(1) Do you want to provide the exact values, or (2) Should I size and choose them autonomously?**
3. **Step 3 — Resolution, `.asc` Compilation & Verification**:
   - Once the user provides the values (passed via `user_overrides`) or authorizes autonomous sizing (`mode="autonomous"`), call `resolve_and_compile_vision_graph` to generate the `.cir` netlist and the 16-pixel grid `.asc` schematic, verify `NC_ = 0` via `verify_asc_netlist`, and simulate in LTspice.

---

## 🛠️ Special Workflow: Reading, Troubleshooting, and Modifying Existing LTspice Projects (`ia_spice.ltspice_project`)

When the user provides an existing LTspice file or project directory (`.asc`, `.cir`, `.net`, `.sub`, `.lib`, `.asy`, `.plt`, `.log`):

1. **Inspect & Diagnose (`inspect_ltspice_project` / `ia-spice inspect`)**:
   - Audits all files in the directory for unconnected pins (`NC_`), missing `.include`/`.lib` libraries, failed `.meas` statements in `.log`, and visual layout overlaps in `.asc` schematics.
2. **Modify or Add Components (`modify_asc_component`, `add_component_to_asc`, `parse_spice_netlist`)**:
   - Change component values (optionally snapping to `E12`/`E24`/`E96`) or inject new components (`res`, `cap`, `ind`, `npn`, `diode`) directly into an `.asc` schematic or `.cir` netlist; new components are automatically placed in a free 16px-grid column (`max_x + 224`) with horizontal flag tabs.
3. **Beautify & Auto-Layout (`beautify_asc_file`, `compile_netlist_to_asc`)**:
   - `beautify_asc_file` fixes legacy `.asc` schematics by converting vertical colliding flags into horizontal flag tabs, dropping ground symbols on `32 px` vertical stubs, and pushing `.tran`/`.meas` directives `>= 160 px` below the circuit.
   - `compile_netlist_to_asc` compiles any `.cir`/`.net` into a clean `.asc` using `style="A_textbook_wired"`, `style="B_power_rail_bus"`, or `style="C_modular_blocks"`.

