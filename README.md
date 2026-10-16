# ⚡ IA-SPICE

**Equation-Grounded AI Analog & RF Circuit Engineering Framework with Closed-Loop LTspice Verification and Clean `.asc` Schematic Compilation.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](pyproject.toml)
[![Simulator: LTspice 24](https://img.shields.io/badge/Simulator-LTspice%2024%20%2F%20XVII-red.svg)](#)
[![MCP Server Ready](https://img.shields.io/badge/MCP-JSON--RPC%202.0-16a34a.svg)](src/ia_spice/mcp_server.py)

---

## 💡 Why Does IA-SPICE Exist? (And Why Most AI Circuit Attempts Fail)

When engineers or students ask a standard LLM to *"Design an FM transmitter or an analog circuit in SPICE"*, the result almost always fails when simulated:
1. **Zero Analytical Grounding**: The LLM guesses resistor and capacitor values directly inside a monolithic netlist without solving DC operating points, tank resonance, or input/output impedances.
2. **Oscillator Startup Failure & Load Pulling**: Connecting Stage $N+1$ (e.g., an RF amplifier or $50\,\Omega$ antenna) directly to Stage $N$ (e.g., a Colpitts VCO) collapses loop gain below Barkhausen's criterion ($g_m R_p < 1$) and kills oscillation.
3. **Nonlinear Self-Rectification**: In VCOs, large RF tank swings rectify across varactor diodes if series coupling ($C_{CV}$) is not sized to prevent forward conduction.
4. **Broken `.asc` Schematics**: Asking an LLM to generate visual LTspice `.asc` files directly produces off-grid pins (`NC_01`), diagonal wires, and accidental short circuits across component bodies.

**IA-SPICE solves all four problems** by turning the exact engineering workflow used to design and validate a complete **6-Stage 5 MHz Colpitts VCO FM Transmitter** into a reusable open-source Python toolkit, CLI, MCP Server, and Agent Skill.

---

## 🏆 Comparison Against Existing Tools (2024–2026)

| Tool / Project | Category | Analytical Hand Calcs First | Stage-by-Stage Loading Check | Closed-Loop LTspice `.meas` | Physics Root-Cause Diagnostics | Verified 16px `.asc` Compiler (`NC_ = 0`) | Auto Technical Report |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **IA-SPICE (This Repo)** | **Full Engineering Framework** | ✅ **Yes (BJT, LC, Barkhausen, E24)** | ✅ **Yes (`StageContract`)** | ✅ **Yes** | ✅ **Yes** | ✅ **Yes (`LTspice -netlist` iso-verified)** | ✅ **Yes (MD/HTML/Plots)** |
| `Cognitohazard/ltspice-mcp` | MCP Plumbing | ❌ No | ❌ No | ✅ Yes | ❌ No | ⚠️ Manual edit | ❌ No |
| `clanker-lover/spicebridge` | ngspice Templates | ⚠️ 11 fixed RC/op-amp templates | ⚠️ Basic | ⚠️ ngspice only | ❌ No | ❌ No (`.asc` unsupported) | ❌ No |
| `AnalogCoder-Pro` *(IEEE TCAD '26)* | Academic IC Agent | ❌ No (Black-box BO) | ❌ No | ❌ (`PySpice` only) | ⚠️ VLM plot | ❌ No | ❌ No |

---

## 🏗️ Architecture & Core Modules (`src/ia_spice/`)

```text
IA-SPICE/
├── src/ia_spice/
│   ├── sizing.py            # First-principles DC/AC BJT sizing, Colpitts LC tank, Barkhausen margin, E12/E24/E96 snapping, StageContract
│   ├── meas_parser.py       # Deterministic parser for all 4 LTspice .log/.meas formats (scalars, AC dB/phase, .step tables, FAILs)
│   ├── simulator.py         # Headless LTspice batch runner (-b / -netlist) + physics-based failure diagnostics
│   ├── asc_compiler.py      # Calibrated 16-pixel grid LTspice .asc schematic builder with Manhattan routing & geometric DRC
│   ├── netlist_verifier.py  # Formal schematic-to-netlist isomorphism verifier (guarantees NC_ = 0 and 100% node match)
│   ├── vision_to_spice.py   # Vision-to-SPICE reconstructor with missing-parameter audit (ask_user vs autonomous E24 sizing)
│   ├── reporting.py         # Automated Theory-vs-SPICE reconciliation tables, 300 DPI Matplotlib charts & HTML/Markdown reports
│   ├── cli.py               # Command-line interface (`ia-spice doctor`, `snap`, `sim`, `verify-asc`, `import-vision`)
│   └── mcp_server.py        # Zero-dependency JSON-RPC 2.0 MCP Server for Claude Code, Antigravity, Cursor & Gemini CLI
├── examples/
│   ├── demo1_rlc_bandpass.py        # Demo 1: 1 MHz RLC Band-Pass Filter (E24 + .asc + .ac + report)
│   ├── demo2_cascaded_bjt_amp.py    # Demo 2: 2-Stage BJT CE + Emitter-Follower Buffer (Loading contract + .asc + .tran)
│   └── demo3_vision_reconstruction.py # Demo 3: Schematic photo reconstruction (missing-parameter detection & autonomous sizing)
├── skills/
│   └── ia-spice-engineer/
│       └── SKILL.md         # 7-Phase mandatory engineering protocol + Vision-to-SPICE image import guide
└── tests/                   # 30 automated unit + live LTspice integration & benchmark tests (Levels 1 to 4 + Vision)
```

---

## 🚀 Quickstart

### 1. Prerequisites
- **Python 3.9+** (`numpy`, `matplotlib`)
- **LTspice 24** or **LTspice XVII** installed (automatically detected in `%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe` or `C:\Program Files\ADI\LTspice\LTspice.exe`).

### 2. Run the Full Verification & Benchmark Suite
```bash
python -m unittest discover -s tests -v
```
This runs **30 automated tests** across 4 engineering complexity levels plus Vision-to-SPICE schematic reconstruction (including live headless LTspice simulations and formal `.asc` netlist verifications).

### 3. Command-Line Usage (`cli.py`)
```bash
# 1. Verify LTspice installation
python -m ia_spice.cli doctor

# 2. Snap an analytical resistor/capacitor value to commercial E24/E96 series
python -m ia_spice.cli snap 10.83k --series E24

# 3. Formally verify an LTspice .asc visual schematic (zero unconnected pins + netlist match)
python -m ia_spice.cli verify-asc ../Esquematicos/S6_modulacao_fm_completa.asc --cir ../vco_colpitts_fm/S6_modulacao_fm_completa.cir

# 4. Run a simulation in batch mode and extract structured .meas JSON + physics diagnostics
python -m ia_spice.cli sim ../vco_colpitts_fm/TESTE_bloco1_audio.cir

# 5. Reconstruct a circuit from an image graph (ask_user or autonomous mode)
python examples/demo3_vision_reconstruction.py
```

---

## 🤖 Connecting to AI Agents (MCP Server + Skill)

### Option A: Add the MCP Server (`Claude Code` / `Antigravity` / `Cursor`)
Add the following entry to your `mcp_config.json` / `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "ia-spice": {
      "command": "python",
      "args": ["-m", "ia_spice.mcp_server"],
      "env": {
        "PYTHONPATH": "C:/analogicos/IA-SPICE/src"
      }
    }
  }
}
```
Exposed MCP tools:
- `ia_spice_snap_series`: Snap ideal values to E12/E24/E96.
- `ia_spice_size_bjt_stage`: Exact analytical DC/AC operating point for Common-Emitter (CE) or Emitter-Follower (CC) stages.
- `ia_spice_size_colpitts`: Colpitts LC + Varactor tank resonance and Barkhausen startup margin ($g_m R_p$).
- `ia_spice_run_simulation`: Headless LTspice execution + `.meas` extraction + physics diagnostics.
- `ia_spice_verify_schematic_asc`: Formal `LTspice.exe -netlist` check (`NC_ = 0` and isomorphism).
- `ia_spice_import_vision_graph`: Reconstruct a circuit from a schematic photo/image graph, auditing any missing values (`mode="ask_user"`) or sizing them autonomously via E24 rules (`mode="autonomous"`).

### Option B: Install the Agent Skill
Copy `skills/ia-spice-engineer/` into your agent's skills directory. It enforces the **7-Phase Equation-Grounded Protocol** (Specification $\rightarrow$ Hand Calculations $\rightarrow$ Isolated Block Simulation $\rightarrow$ Physics Diagnostics $\rightarrow$ Integration $\rightarrow$ Verified 16px `.asc` Compilation $\rightarrow$ Engineering Report) as well as **Vision-to-SPICE Schematic Photo Reconstruction**.

---

## 📸 Vision-to-SPICE: Schematic Photo Reconstruction (`vision_to_spice.py`)

When a user sends a photo, textbook scan, or hand-drawn picture of an electronic schematic, values are often missing or unreadable (e.g., resistors labeled simply `$R_1, R_2, R_C, R_E$` without Ohm values, an unlabeled NPN transistor `$Q_1$`, or an unspecified `$V_{CC}$` rail).

`ia_spice.vision_to_spice` handles this with strict engineering discipline in two selectable modes:
1. **`mode="ask_user"` (Interactive Audit):** Detects every missing component value, model, supply voltage, or stimulus, computes an analytical E24 suggestion for each based on the circuit topology, and presents a structured Markdown table asking the user whether they want to supply specific values or authorize autonomous sizing.
2. **`mode="autonomous"` (Autonomous Sizing & Compilation):** Applies analytical DC/AC operating-point equations (plus any partial `user_overrides`), snaps values to commercial E24 series, compiles both a `.cir` netlist and a 16-pixel grid `.asc` schematic (`NC_ = 0`), and simulates the reconstructed circuit in LTspice.

---

## 📊 Flagship Benchmark: 6-Block 5 MHz Colpitts VCO FM Transmitter

Designed, sized, simulated, debugged, and compiled into clean `.asc` schematics 100% through the IA-SPICE methodology:

| Stage / Testbench | Parameter | Analytical Target | LTspice `.meas` Result | Relative Error | `.asc` `NC_` Pins | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Block 1 (`TESTE_bloco1_audio.asc`)** | Emitter DC Bias $V_{CTRL}$ | $4.00\text{ V}$ | **$4.108\text{ V}$** | $+2.70\%$ | `0` | ✅ PASS |
| **Block 1 (`TESTE_bloco1_audio.asc`)** | Voltage Gain $A_{v4}$ | $0.9935\text{ V/V}$ | **$0.9934\text{ V/V}$** | $-0.01\%$ | `0` | ✅ PASS |
| **Block 3 (`S1_varactor_validacao.asc`)** | Varactor $C_{var}(4\text{V})$ | $20.69\text{ pF}$ | **$20.69\text{ pF}$** | $0.00\%$ | `0` | ✅ PASS |
| **Block 4 (`S2_S3_vco_nucleo.asc`)** | VCO Core Frequency $f_{osc}$ | $5.000\text{ MHz}$ | **$4.9957\text{ MHz}$** | $-0.085\%$ | `0` | ✅ PASS |
| **Block 4 (`S4_curva_fosc_vctrl.asc`)** | Tuning Sensitivity $K_{VCO}$ | $\Delta f = 51.6\text{ kHz}$ | **$12.90\text{ kHz/V}$** | Optimized ($C_{CV}=47\text{pF}$) | `0` | ✅ PASS |
| **Blocks 5+6 (`TESTE_blocos5_6_amplificador.asc`)** | Output Power in $50\,\Omega$ | $10.00\text{ mW}$ | **$10.00\text{ mW}$** | $0.00\%$ | `0` | ✅ PASS |
| **Full System (`S6_modulacao_fm_completa.asc`)** | Integrated Carrier $f_0$ | $5.000\text{ MHz}$ | **$4.9798\text{ MHz}$** | $-0.40\%$ | `0` | ✅ PASS |
| **Full System (`S6_modulacao_fm_completa.asc`)** | Antenna Power $P_{out}$ ($50\,\Omega$) | $10.00\text{ mW}$ | **$9.823\text{ mW}$** | $-1.77\%$ | `0` | ✅ PASS |
| **Full System (`S6_modulacao_fm_completa.asc`)** | Total DC Power $P_{total}$ | $< 500\text{ mW}$ | **$387.97\text{ mW}$** | $-22.4\%$ margin | `0` | ✅ PASS |

---

## 📄 License
Released under the [MIT License](LICENSE).
