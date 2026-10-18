---
name: New Circuit Benchmark Proposal
about: Submit a new analog or RF circuit benchmark to the IA-SPICE verification suite
title: "[BENCHMARK] "
labels: enhancement, benchmark
---

## Circuit Topology & Target Specifications
- **Circuit Name / Topology:** (e.g., 10.7 MHz IF Band-Pass Filter, Cascode LNA, Wien-Bridge Oscillator)
- **Target Specifications:** (e.g., $f_0$, Gain $A_v$, Input/Output Impedance $Z_{in}/Z_{out}$, DC Power $P_{DC}$)

## Analytical Hand Calculations
Provide the first-principles design equations used to size the components (E12/E24/E96).

## LTspice `.meas` Verification Results
Paste the `.meas` output or attach the `.cir` / `.asc` files showing `NC_ = 0` and `< 5%` error between theory and simulation.
