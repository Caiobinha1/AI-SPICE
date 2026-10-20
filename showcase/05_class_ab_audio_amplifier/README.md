# 05 — 1 W / 8 Ω Class-AB Audio Amplifier (`±12 V`, `20 Hz – 20 kHz`)

Designed and verified end-to-end using AI-SPICE:

- **Architecture**: AC Input Coupling (`CIN`, `RIN`) $\rightarrow$ `LT1360` Preamp (`U1`, $10\times$) $\rightarrow$ `LT1360` Driver (`U2`) + `1N4148` Diode Bias Chain (`RBP–D1–D2–RBN`) $\rightarrow$ Complementary Push-Pull Output (`FZT849` NPN + `FZT949` PNP mirrored `M180` with $0.47\,\Omega$ emitter ballast resistors `RE1`, `RE2`) $\rightarrow$ $8\,\Omega$ Load (`RLOAD`) & Zobel Network (`RZ`, `CZ`).
- **LTspice 24 Verification (`classe_ab.asc` / `classe_ab.cir`)**:
  - Output Power in $8\,\Omega$: **$0.9970\text{ W}$** ($-0.30\%$ error vs. $1.00\text{ W}$ target)
  - Output Swing ($1\text{ kHz}$): **$7.991\text{ V}_{pp}$** ($2.824\text{ V}_{rms}$, symmetric clipping margin)
  - Closed-Loop Voltage Gain: **$28.38\text{ V/V}$** ($29.06\text{ dB}$)
  - $-3\text{ dB}$ Bandwidth: **$9.43\text{ Hz}$ to $560.5\text{ kHz}$** ($< 0.08\text{ dB}$ flatness across $20\text{ Hz–}20\text{ kHz}$)
  - Schematic DRC: **`NC_ = 0`** floating pins, 100% physical orthogonal `WIRE` connections on all signal/feedback paths.

