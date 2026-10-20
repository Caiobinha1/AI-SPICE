# 03 — 2-Stage Cascaded BJT Amplifier & 3 Auto-Layout Styles

> **Demonstrates analytical inter-stage loading verification (`StageContract`), impedance buffering, and 3 distinct automatic `.asc` schematic layout architectures.**

---

## 1. Executive Summary & Why Inter-Stage Loading Matters

When connecting an amplifier stage to a low-impedance load (e.g. $100\,\Omega$), direct connection causes catastrophic gain collapse due to load pulling ($Z_{out} \gg Z_{load}$). This project proves how AI-SPICE detects this issue analytically and synthesizes an Emitter-Follower buffer to restore full voltage gain.

| Configuration | Analytical Voltage Gain | LTspice `.meas` Gain | Loading Attenuation | Status |
| :--- | :---: | :---: | :---: | :---: |
| **Q1 Common-Emitter (Unloaded)** | $9.20\text{ V/V}$ ($19.28\text{ dB}$) | $9.20\text{ V/V}$ | $0\%$ (Reference) | Baseline |
| **Q1 Driving 100 Ω Directly (No Buffer)** | **$0.40\text{ V/V}$** ($-7.96\text{ dB}$) | **$0.40\text{ V/V}$** | **$-95.7\%$ (COLLAPSE!)** | FAIL |
| **Q1 + Q2 Emitter-Follower Buffer + 100 Ω** | **$6.95\text{ V/V}$** ($16.84\text{ dB}$) | **$6.87\text{ V/V}$** ($16.74\text{ dB}$) | Restored ($-1.15\%$ error) | **PASS** |

---

## 2. Circuit Architecture & How it Works

1. **Stage 1 — Common-Emitter Voltage Amplifier (`Q1` 2N2222)**:
   Biased with $R_1 = 47\text{ k}\Omega$, $R_2 = 12\text{ k}\Omega$, $R_{C1} = 2.2\text{ k}\Omega$, split emitter resistors $R_{E1A} = 220\,\Omega$ (unbypassed for linearity and temperature stability) and $R_{E1B} = 470\,\Omega$ bypassed by $C_{E1} = 100\,\mu\text{F}$. Output impedance is $Z_{out1} \approx R_{C1} = 2.2\text{ k}\Omega$.
2. **Stage 2 — Common-Collector Emitter-Follower Buffer (`Q2` 2N2222)**:
   Biased with $R_3 = R_4 = 22\text{ k}\Omega$, $R_{E2} = 470\,\Omega$, presenting a high input impedance $Z_{in2} \approx 11.2\text{ k}\Omega$ to Stage 1 and a very low output impedance $Z_{out2} \approx 10\,\Omega$ to the $100\,\Omega$ load.

---

## 3. Engineering Decisions Made by the AI

1. **Detection of Gain Collapse via `StageContract`**:
   The AI evaluated `check_cascade_loading(driver=Q1, load=100_Ohm)` before running SPICE:
   $$\text{Attenuation} = \frac{R_L}{Z_{out} + R_L} = \frac{100}{2200 + 100} = 0.0435 \quad (-95.7\%)$$
   The tool immediately flagged the failure and prescribed a buffer stage.
2. **Sizing the Emitter-Follower (`size_bjt_OperatingPoint`)**:
   By inserting $Q_2$, the load seen by $Q_1$ increases from $100\,\Omega$ to $Z_{in2} \approx 11.2\text{ k}\Omega$, keeping Stage 1 loaded gain at $A_{v1} \approx 7.7\text{ V/V}$ and overall gain at $6.87\text{ V/V}$.
3. **E24 Series Snapping**:
   All bias resistors were snapped to standard commercial values ($47\text{k}, 12\text{k}, 2.2\text{k}, 220, 470, 22\text{k}$) using `snap_to_series`.

---

## 4. The 3 Auto-Layout Architectures (`.asc`)

AI-SPICE automatically compiles the pure netlist `demo2_reference.cir` into 3 layout styles, each verified with `NC_ = 0`:

- **`estilo_A_textbook_wired.asc` (`style="A_textbook_wired"`)**:
  Classic textbook layout with stages arranged left-to-right, orthogonal signal wires on the 16-pixel grid, and local horizontal power/ground flags.
- **`estilo_B_power_rail_bus.asc` (`style="B_power_rail_bus"`)**:
  Features a continuous top horizontal `VCC_12V` power bus wire (`y = 48`) with all pull-up resistors wired directly into the top rail.
- **`estilo_C_modular_blocks.asc` (`style="C_modular_blocks"`)**:
  Provides extra-wide spacing between functional blocks with visual comments and clear separation.
- **`estilo_A_modified_with_cdec.asc`**:
  Demonstrates programmatic component injection (`add_component_to_asc` and `modify_asc_component`), adding decoupling capacitor `C_DEC` and updating `RLOAD = 1k`.
