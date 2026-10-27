# 🔬 Benchmark 06: Standard BJT Amplifier Topologies (CE, CC, CB)

This benchmark demonstrates the topology-aware schematic compiler and analytical sizing engine across all three fundamental bipolar junction transistor (BJT) configurations:
1. **Common Emitter (CE)** with parallel emitter bypass capacitor ($R_E \parallel C_E$)
2. **Common Collector (CC / Emitter Follower)** with direct $V_{CC}$ collector rail and straight horizontal emitter output line
3. **Common Base (CB)** with emitter signal input, base AC-ground bypass ($R_2 \parallel C_B$), and collector output

All three circuits are generated from standard SPICE netlists (`.cir`) into textbook-wired LTspice schematics (`.asc`), verified via LTspice batch simulation (`-b`), and proven with formal netlist isomorphism (`NC_ = 0` floating pins).

---

## 1. Topologies & Engineering Comparison

| Parameter | Common Emitter (CE) | Common Collector (CC) | Common Base (CB) |
| :--- | :---: | :---: | :---: |
| **Input Terminal** | Base ($y = 288$) | Base ($y = 288$) | Emitter ($y = 368$) |
| **Output Terminal** | Collector ($y = 224$) | Emitter ($y = 368$) | Collector ($y = 224$) |
| **AC Common Ground** | Emitter (via $C_E$) | Collector (via $V_{CC}$) | Base (via $C_B$) |
| **Voltage Gain ($A_v$)** | **High (Inverting)**: $-168.2\text{ V/V}$ ($44.5\text{ dB}$) | **Near-Unity (Non-Inverting)**: $+0.989\text{ V/V}$ ($-0.09\text{ dB}$) | **High (Non-Inverting)**: $+96.4\text{ V/V}$ ($39.7\text{ dB}$) |
| **Input Impedance ($Z_{in}$)** | Medium: $\sim 2.2\text{ k}\Omega$ | High: $\sim 15.5\text{ k}\Omega$ | Very Low: $\sim 13\text{ }\Omega$ ($R_E \parallel r_e$) |
| **Output Impedance ($Z_{out}$)** | Medium: $R_C = 2.2\text{ k}\Omega$ | Very Low: $\sim 13\text{ }\Omega$ ($r_e$) | High: $R_C = 3.3\text{ k}\Omega$ |
| **Bandwidth / Miller Effect** | Susceptible to $C_{cb}$ Miller multiplication | Wideband (unity buffer) | **Ultra-Wideband** (zero Miller multiplication) |
| **Typical Use Case** | Voltage amplification stage | High-to-low impedance buffer | RF front-end, $50\,\Omega$ matching, cascode |

---

## 2. Headless LTspice Batch Simulation Verification

All three circuits were compiled into `.asc` schematics and simulated with headless LTspice 24 batch execution (`.tran 0 5m 0 1u`):

### A. Common Emitter (`01_common_emitter.asc`)
- **Input**: $V_{in} = 20\text{ mV}_{p-p}$ ($1\text{ kHz}$)
- **Output**: $V_{out} = 3.361\text{ V}_{p-p}$
- **Voltage Gain**: $A_v = \mathbf{168.21\text{ V/V}}$ ($44.52\text{ dB}$)
- **DRC & Netlist Status**: `unconnected_pins: []`, `NC_ = 0`, `DRC issues: 0`

### B. Common Collector / Emitter Follower (`02_common_collector.asc`)
- **Input**: $V_{in} = 200\text{ mV}_{p-p}$ ($1\text{ kHz}$)
- **Output**: $V_{out} = 197.62\text{ mV}_{p-p}$
- **Voltage Gain**: $A_v = \mathbf{0.9891\text{ V/V}}$ ($-0.095\text{ dB}$)
- **DRC & Netlist Status**: `unconnected_pins: []`, `NC_ = 0`, `DRC issues: 0`

### C. Common Base (`03_common_base.asc`)
- **Input**: $V_{in} = 20\text{ mV}_{p-p}$ injected into Emitter ($1\text{ kHz}$)
- **Output**: $V_{out} = 1.928\text{ V}_{p-p}$ at Collector
- **Voltage Gain**: $A_v = \mathbf{96.45\text{ V/V}}$ ($39.69\text{ dB}$, non-inverting)
- **DRC & Netlist Status**: `unconnected_pins: []`, `NC_ = 0`, `DRC issues: 0`

---

## 3. Schematic Routing & Visual Layout Highlights

1. **Topology-Aware Pin Alignment**:
   - For **CE** and **CC**: input source $V_{in}$ and coupling capacitor $C_{in}$ align horizontally with the transistor base pin at $y = 288$.
   - For **CB**: input source $V_{in}$ and coupling capacitor $C_{in}$ look ahead to the active device and align horizontally with the transistor emitter pin at $y = 368$.
2. **Parallel Bypass Branch Synthesis**:
   - In **CE**, the emitter bypass capacitor $C_E$ is placed side-by-side ($dx = 96\text{ px}$) with emitter resistor $R_E$ and linked with a single orthogonal horizontal bridging wire, avoiding ugly multi-column sprawling.
   - In **CB**, the base bypass capacitor $C_B$ is placed side-by-side ($dx = 96\text{ px}$) with lower divider resistor $R_2$ and connected to AC ground without crossing the input signal line.
3. **Collinear Output Taps**:
   - For **CE** and **CB**: output coupling capacitor $C_{out}$ and load resistor $R_L$ are collinear with the collector node at $y = 224$.
   - For **CC**: output coupling capacitor $C_{out}$ and load resistor $R_L$ are collinear with the emitter node at $y = 368$, forming a 100% straight horizontal output path with zero dogleg kinks.
