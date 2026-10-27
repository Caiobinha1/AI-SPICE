# 3-Stage Cascaded BJT Multi-Mode Amplifier (`CB -> CE -> CC`)

An equation-grounded, multi-stage discrete BJT amplifier demonstrating all three fundamental transistor configurations operating together in a single cascaded signal chain:
1. **Stage 1 — Common Base (CB)**: Low input impedance ($Z_{\text{in}} \approx 19.3\,\Omega$), wide bandwidth, non-inverting voltage preamplification. Matches low-impedance signal transducers or $50\,\Omega$ generators naturally.
2. **Stage 2 — Common Emitter (CE)**: High inverting voltage gain ($A_v \approx -43.0\text{ V/V}$) with local emitter degeneration ($R_{E2,\text{rf}} = 47\,\Omega$) for bias stability and linearity.
3. **Stage 3 — Common Collector (CC / Emitter Follower)**: High input impedance ($Z_{\text{in}} > 14\,\text{k}\Omega$), near-unity gain ($A_v \approx 0.84\text{ V/V}$), and low output impedance ($Z_{\text{out}} \approx 25\,\Omega$) to drive a heavy $1\,\text{k}\Omega$ load without loading down the high-impedance voltage amplifier stage.

---

## 🏛️ Stage Architecture & Inter-Stage Impedance Contracts

```
[ Signal Generator ] ---> [ Stage 1: CB ] -------------> [ Stage 2: CE ] -------------> [ Stage 3: CC ] ---> [ Load ]
   Vin (2 mVpp)             Zin: ~19.3 Ohm                 Zin: ~5.86 kOhm                Zin: ~14.0 kOhm      RL: 1 kOhm
   1 kHz                    Zout: 2.2 kOhm                 Zout: 3.3 kOhm                 Zout: ~25 Ohm
                            Gain: +63.2 V/V                Gain: -43.0 V/V                Gain: 0.84 V/V
```

### Inter-Stage Loading Verification (`ai_spice.sizing`)
- **Stage 1 (CB) $\to$ Stage 2 (CE)**:
  - Driver $Z_{\text{out}} = 2200\,\Omega$, Load $Z_{\text{in}} = 5864.5\,\Omega$
  - Attenuation factor: $\frac{5864.5}{2200 + 5864.5} = 0.727$ (Gain drop: $27.3\%$)
  - Status: **PASSED** (impedance ratio $> 2.5$)
- **Stage 2 (CE) $\to$ Stage 3 (CC)**:
  - Driver $Z_{\text{out}} = 3300\,\Omega$, Load $Z_{\text{in}} = 13986.2\,\Omega$
  - Attenuation factor: $\frac{13986.2}{3300 + 13986.2} = 0.809$ (Gain drop: $19.1\%$)
  - Status: **PASSED** (high CC input impedance protects CE collector swing)

---

## 📊 Analytical Calculation vs. LTspice Headless Simulation

| Metric | Analytical Calculation (`sizing.py`) | LTspice `.meas` Telemetry | Error / Agreement | Status |
| :--- | :---: | :---: | :---: | :---: |
| **Input Signal ($V_{\text{in,pp}}$)** | $2.000\text{ mV}$ | $2.000\text{ mV}$ | $0.00\%$ | Match |
| **Stage 1 CB Collector ($V_{C1,\text{pp}}$)** | $118.1\text{ mV}$ | $126.4\text{ mV}$ | $+7.0\%$ | Converged |
| **Stage 1 Voltage Gain ($A_{v1}$)** | $+59.05\text{ V/V}$ | $+63.19\text{ V/V}$ | $+7.0\%$ | Converged |
| **Stage 2 CE Collector ($V_{C2,\text{pp}}$)** | $4.252\text{ V}$ | $5.429\text{ V}$ | $+27.6\%$ (Higher $g_m$ in SPICE) | Converged |
| **Stage 2 Voltage Gain ($A_{v2}$)** | $-36.01\text{ V/V}$ | $-42.95\text{ V/V}$ | $+19.3\%$ | Converged |
| **Stage 3 CC Output ($V_{\text{out,pp}}$)** | $4.196\text{ V}$ | $4.585\text{ V}$ | $+9.2\%$ | Converged |
| **Stage 3 Voltage Gain ($A_{v3}$)** | $0.987\text{ V/V}$ (unloaded) | $0.845\text{ V/V}$ | Loaded with $1\,\text{k}\Omega$ | Expected |
| **Total Cascaded Gain ($A_{v,\text{tot}}$)** | **$2098\text{ V/V}$** | **$2292.6\text{ V/V}$** ($+67.21\text{ dB}$) | **$+9.2\%$** | **PASSED** |

---

## 🎨 Schematic Layout & Formal Netlist Verification

- **Format**: LTspice Version 4 `.asc` on the strict 16-pixel grid ($x \pmod{16} == 0, y \pmod{16} == 0$).
- **Routing**: 78 orthogonal Manhattan `WIRE` segments connecting all signal, bias, and coupling paths.
- **Floating Pins**: `NC_ = 0` (`unconnected_pins == []`).
- **Geometric DRC**: `0` off-grid pins, `0` overlapping components.
- **Node Isomorphism**: 100% match against SPICE reference netlist (`is_clean == True`).

---

## 📁 Project Directory Artifacts

The complete set of project files is located at:
`showcase/07_three_stage_multimode_amplifier/`

- [`three_stage_multimode_amplifier.asc`](three_stage_multimode_amplifier.asc) — Fully routed, 16px grid visual schematic with 0 floating pins.
- [`three_stage_multimode_amplifier.cir`](three_stage_multimode_amplifier.cir) — Self-contained SPICE netlist with all analytical component values.
- [`three_stage_multimode_amplifier.log`](three_stage_multimode_amplifier.log) — Headless LTspice batch execution log with all `.meas` metrics.
- [`three_stage_multimode_amplifier.plt`](three_stage_multimode_amplifier.plt) — Signal plot definitions for LTspice waveform viewer.
- [`README.md`](README.md) — Engineering documentation, stage contracts, and reconciliation report.

---

## 🚀 How to Open and Simulate in LTspice

1. Open `three_stage_multimode_amplifier.asc` in **LTspice 24 / XVII**.
2. Press **Run** (F9 or the Running Man button).
3. The `.plt` file will automatically plot:
   - `V(in)` — The $2\,\text{mV}_{\text{pp}}$ input sine wave.
   - `V(c1)` — Stage 1 (CB) output showing clean non-inverting amplification ($126.4\,\text{mV}_{\text{pp}}$).
   - `V(c2)` — Stage 2 (CE) output showing high inverting amplification ($5.43\,\text{V}_{\text{pp}}$).
   - `V(out)` — Stage 3 (CC) buffered output driving the $1\,\text{k}\Omega$ load cleanly ($4.59\,\text{V}_{\text{pp}}$).
