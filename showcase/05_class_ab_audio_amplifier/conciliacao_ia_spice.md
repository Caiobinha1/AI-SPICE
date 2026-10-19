# Conciliação analítica e simulação
**Modelos nominais, 27 °C**  
**Authors:** Projeto solicitado pelo usuário

---

## 1. Analytical vs. SPICE Simulation Reconciliation Table

| Stage / Test | Parameter | Target Spec | Analytical | LTspice (`.meas`) | Rel. Error | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **20 Hz** | Potencia (W) | 1 W ±2% | `1` | `0.9908` | `-0.92%` | **PASS** |
| **1000 Hz** | Potencia (W) | 1 W ±2% | `1` | `0.997` | `-0.30%` | **PASS** |
| **20000 Hz** | Potencia (W) | 1 W ±2% | `1` | `0.9929` | `-0.71%` | **PASS** |
