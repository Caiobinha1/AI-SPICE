# Demo 1: 1 MHz RLC Band-Pass Filter
**Multi-Feature IA-SPICE Validation**  
**Authors:** IA-SPICE Automated Suite

---

## 1. Analytical vs. SPICE Simulation Reconciliation Table

| Stage / Test | Parameter | Target Spec | Analytical | LTspice (`.meas`) | Rel. Error | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **RLC Tank** | Resonant Frequency f_0 (MHz) | 1.027 MHz | `1.027` | `1.027` | `-0.00%` | **PASS** |
| **RLC Tank** | Peak Passband Gain (V/V) | Rp/(Rs+Rp) | `0.8929` | `0.8928` | `-0.00%` | **PASS** |
