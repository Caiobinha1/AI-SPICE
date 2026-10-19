# 01 — 6-Stage 5 MHz Colpitts VCO FM Transmitter (`50 Ω`)

Complete multi-stage RF benchmark designed and verified with AI-SPICE:

- **`TESTE_bloco1_audio.asc` / `.cir`**: Audio Emitter-Follower & DC Bias Injection ($V_{CTRL} = 4.108\text{ V}$, $A_v = 0.9934\text{ V/V}$, `NC_ = 0`).
- **`S2_S3_vco_nucleo.asc` / `.cir`**: 5 MHz Colpitts VCO Core with SMV1237 Varactor ($f_{osc} = 4.9957\text{ MHz}$, $-0.085\%$ error, `NC_ = 0`).
- **`S6_modulacao_fm_completa.asc` / `.cir`**: Complete 6-Stage FM Transmitter ($f_0 = 4.9798\text{ MHz}$, $P_{out} = 9.823\text{ mW}$ into $50\,\Omega$, $P_{DC} = 387.97\text{ mW}$, `NC_ = 0`).
