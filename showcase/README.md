# 📊 AI-SPICE Verified Circuit Benchmarks (`showcase/`)

Every directory below contains a complete, formally verified LTspice 24 project (`NC_ = 0` floating pins, 100% node isomorphism, and 100% physical orthogonal `WIRE` connections on all signal nets):

| # | Directory | Circuit Topology | Key LTspice `.meas` Result | `.asc` `NC_` Pins | Signal Nets Wired |
| :---: | :--- | :--- | :--- | :---: | :---: |
| **01** | [`01_flagship_5mhz_fm_transmitter/`](01_flagship_5mhz_fm_transmitter/) | 6-Stage 5 MHz Colpitts VCO FM Transmitter ($50\,\Omega$) | $f_0 = 4.9798\text{ MHz}$ ($-0.40\%$), $P_{out} = 9.823\text{ mW}$ ($-1.77\%$) | `0` | **100% Wired** |
| **02** | [`02_rlc_1mhz_bandpass_filter/`](02_rlc_1mhz_bandpass_filter/) | 1 MHz E24 Series RLC Bandpass Filter | $f_0 = 1.005\text{ MHz}$ ($+0.50\%$), $BW = 148.6\text{ kHz}$ | `0` | **100% Wired** |
| **03** | [`03_cascaded_bjt_amp_layout_styles/`](03_cascaded_bjt_amp_layout_styles/) | 2-Stage CE + Emitter-Follower BJT Amplifier ($100\,\Omega$) | Restores collapsed gain $0.40 \rightarrow 6.87\text{ V/V}$ ($-1.15\%$) | `0` | **100% Wired** |
| **04** | [`04_vision_to_spice_reconstruction/`](04_vision_to_spice_reconstruction/) | Vision-to-SPICE BJT Amplifier from Incomplete Schematic Photo | $V_E = 1.15\text{ V}$, $A_v = 9.40\text{ V/V}$ ($20\text{ dB}$) | `0` | **100% Wired** |
| **05** | [`05_class_ab_audio_amplifier/`](05_class_ab_audio_amplifier/) | 1 W / $8\,\Omega$ Class-AB Audio Amplifier (`LT1360` + `FZT849`/`FZT949`) | $P_{out} = 0.997\text{ W}$ ($-0.30\%$), $BW = 9.4\text{ Hz–}560\text{ kHz}$ | `0` | **100% Wired** |
| **06** | [`06_bjt_amplifier_topologies/`](06_bjt_amplifier_topologies/) | Fundamental BJT Amplifier Topologies (CE, CC, CB) | CE: $A_v = 168.2\text{ V/V}$; CC: $A_v = 0.989\text{ V/V}$; CB: $A_v = 96.4\text{ V/V}$ | `0` | **100% Wired** |
