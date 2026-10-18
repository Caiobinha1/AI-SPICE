"""
Generate publication-grade 300 DPI showcase figures in `assets/` and populate `showcase/`
with ready-to-open `.asc`, `.cir`, `.plt`, and `.md` files for GitHub visitors.
"""

import os
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))
sys.path.insert(0, os.path.join(ROOT_DIR, "examples"))

from demo1_rlc_bandpass import run_demo1
from demo2_cascaded_bjt_amp import run_demo2
from demo3_vision_reconstruction import run_demo3
from demo4_layout_styles_and_project_editor import run_demo4
from ia_spice.sizing import evaluate_varactor_cv, size_colpitts_tank


def generate_charts(assets_dir: str) -> None:
    os.makedirs(assets_dir, exist_ok=True)

    # 1. Chart 1: 6-Block Flagship FM Transmitter — Analytical vs LTspice 24 Relative Error (%)
    stages = [
        "Block 1: Audio Bias\nV_CTRL (V)",
        "Block 1: Follower\nGain Av4 (V/V)",
        "Block 3: Varactor\nC_var @ 4V (pF)",
        "Block 4: Colpitts VCO\nf_osc (MHz)",
        "Blocks 5+6: RF Amp\nP_out 50Ω (mW)",
        "Full 6-Block System\nCarrier f_0 (MHz)",
        "Full 6-Block System\nAntenna P_out (mW)",
    ]
    analytical = [4.00, 0.9935, 20.69, 5.000, 10.00, 5.000, 10.00]
    simulated = [4.108, 0.9934, 20.69, 4.9957, 10.00, 4.9798, 9.823]
    errors_pct = [abs(s - a) / a * 100.0 for a, s in zip(analytical, simulated)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2), gridspec_kw={"width_ratios": [1.3, 1.0]})
    fig.patch.set_facecolor("#ffffff")

    y_pos = np.arange(len(stages))
    norm_sim = [s / a * 100.0 for a, s in zip(analytical, simulated)]
    ax1.barh(y_pos - 0.18, [100.0] * len(stages), height=0.34, label="Analytical Hand Calc (100% ref)", color="#1e3a8a", alpha=0.88)
    ax1.barh(y_pos + 0.18, norm_sim, height=0.34, label="LTspice 24 Simulation (.meas)", color="#10b981", alpha=0.92)
    ax1.set_yticks(y_pos)
    ax1.set_yticklabels(stages, fontsize=9.5, fontweight="bold")
    ax1.set_xlim(90, 108)
    ax1.axvline(100.0, color="#1e3a8a", linestyle="--", linewidth=1.0)
    ax1.set_xlabel("Normalized Agreement (% of Analytical Target)", fontsize=10, fontweight="bold")
    ax1.set_title("Analytical Calculation vs. LTspice 24 (.meas)", fontsize=11.5, fontweight="bold")
    ax1.legend(loc="lower right", fontsize=9)
    ax1.grid(axis="x", linestyle=":", alpha=0.6)

    bars = ax2.barh(y_pos, errors_pct, height=0.5, color="#0284c7", edgecolor="#0369a1")
    ax2.axvline(5.0, color="#dc2626", linestyle="--", linewidth=1.3, label="5.0% Engineering Tolerance")
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels([""] * len(stages))
    ax2.set_xlim(0, 6.0)
    ax2.set_xlabel("Absolute Relative Error |Sim - Calc| (%)", fontsize=10, fontweight="bold")
    ax2.set_title("Relative Error Across All 6 Stages (< 2.7%)", fontsize=11.5, fontweight="bold")
    for bar, err in zip(bars, errors_pct):
        ax2.text(bar.get_width() + 0.12, bar.get_y() + bar.get_height() / 2, f"{err:.2f}%", va="center", fontsize=9, fontweight="bold", color="#0f172a")
    ax2.legend(loc="upper right", fontsize=9)
    ax2.grid(axis="x", linestyle=":", alpha=0.6)

    fig.suptitle("IA-SPICE Flagship Benchmark: 6-Stage 5 MHz Colpitts VCO FM Transmitter", fontsize=13.5, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(os.path.join(assets_dir, "flagship_vco_theory_vs_ltspice.png"), dpi=300)
    plt.close(fig)

    # 2. Chart 2: Varactor CV Curve & Colpitts VCO Tuning Curve f_osc(V_ctrl)
    v_ctrl = np.linspace(2.0, 6.0, 41)
    c_var_pf = []
    f_osc_mhz = []
    for v in v_ctrl:
        cv = evaluate_varactor_cv(float(v))
        c_var_pf.append(cv * 1e12)
        tank = size_colpitts_tank(l1=4.7e-6, c1=330e-12, c2=470e-12, c_var=cv, c_cv=47e-12)
        f_osc_mhz.append(tank.f_lc_hz * 1e-6)

    fig, (ax_f, ax_c) = plt.subplots(1, 2, figsize=(12.5, 4.6))
    fig.patch.set_facecolor("#ffffff")

    ax_c.plot(v_ctrl, c_var_pf, color="#7c3aed", linewidth=2.5, marker="o", markevery=5, label="SMV1237 C_var(V_R)")
    ax_c.axvline(4.0, color="#64748b", linestyle="--", linewidth=1.1, label="Quiescent Bias V_Q = 4.0 V (20.69 pF)")
    ax_c.set_xlabel("Reverse Bias Voltage V_CTRL (V)", fontsize=10, fontweight="bold")
    ax_c.set_ylabel("Varactor Capacitance C_var (pF)", fontsize=10, fontweight="bold")
    ax_c.set_title("Block 3: SMV1237 Hyperabrupt Varactor C(V)", fontsize=11, fontweight="bold")
    ax_c.grid(True, linestyle=":", alpha=0.6)
    ax_c.legend(fontsize=9)

    ax_f.plot(v_ctrl, f_osc_mhz, color="#059669", linewidth=2.5, marker="s", markevery=5, label="Colpitts VCO f_osc(V_CTRL)")
    ax_f.axhline(5.000, color="#dc2626", linestyle=":", linewidth=1.2, label="Nominal Carrier f_0 = 5.000 MHz")
    ax_f.axvline(4.0, color="#64748b", linestyle="--", linewidth=1.1)
    ax_f.set_xlabel("Control Voltage V_CTRL (V)", fontsize=10, fontweight="bold")
    ax_f.set_ylabel("Oscillation Frequency f_osc (MHz)", fontsize=10, fontweight="bold")
    ax_f.set_title("Block 4: VCO Tuning Curve (K_VCO = 12.90 kHz/V)", fontsize=11, fontweight="bold")
    ax_f.grid(True, linestyle=":", alpha=0.6)
    ax_f.legend(fontsize=9)

    fig.suptitle("Closed-Loop Varactor & Colpitts VCO Characterization (Blocks 3 & 4)", fontsize=13, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(os.path.join(assets_dir, "vco_kvco_tuning_curve.png"), dpi=300)
    plt.close(fig)

    # 3. Chart 3: Inter-Stage Impedance Loading Proof (Direct 100-Ohm vs Emitter-Follower Buffered)
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    fig.patch.set_facecolor("#ffffff")
    labels = [
        "Unloaded CE Stage\n(Open Collector)",
        "Direct 100 Ω Load\n(No Buffer -> FAIL -95.7%!)",
        "With Q2 Emitter-Follower\n(Analytical StageContract)",
        "With Q2 Emitter-Follower\n(LTspice 24 .tran Sim)",
    ]
    gains = [9.20, 0.40, 6.95, 6.87]
    colors = ["#64748b", "#ef4444", "#2563eb", "#10b981"]
    bars = ax.bar(labels, gains, width=0.52, color=colors, edgecolor="#0f172a", linewidth=0.9)
    ax.set_ylim(0, 10.8)
    ax.set_ylabel("Voltage Gain |A_v| (V/V)", fontsize=10.5, fontweight="bold")
    ax.set_title("Why Inter-Stage Loading Verification (`StageContract`) Matters: 2-Stage BJT Driving 100 Ω", fontsize=11.5, fontweight="bold")
    ax.grid(axis="y", linestyle=":", alpha=0.6)
    for bar, g in zip(bars, gains):
        ax.text(bar.get_x() + bar.get_width() / 2, g + 0.25, f"{g:.2f} V/V", ha="center", fontsize=10, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(assets_dir, "cascade_loading_proof.png"), dpi=300)
    plt.close(fig)


def populate_showcase(showcase_dir: str) -> None:
    if os.path.exists(showcase_dir):
        shutil.rmtree(showcase_dir)
    os.makedirs(showcase_dir, exist_ok=True)

    # 1. Flagship 5 MHz FM Transmitter Schematics
    s1_dir = os.path.join(showcase_dir, "01_flagship_5mhz_fm_transmitter")
    os.makedirs(s1_dir, exist_ok=True)
    for fname in ("TESTE_bloco1_audio.asc", "S2_S3_vco_nucleo.asc", "S6_modulacao_fm_completa.asc"):
        src_asc = os.path.join(r"C:\analogicos\Esquematicos", fname)
        if os.path.isfile(src_asc):
            shutil.copy2(src_asc, os.path.join(s1_dir, fname))
    for fname in ("TESTE_bloco1_audio.cir", "S2_S3_vco_nucleo.cir", "S6_modulacao_fm_completa.cir"):
        src_cir = os.path.join(r"C:\analogicos\vco_colpitts_fm", fname)
        if os.path.isfile(src_cir):
            shutil.copy2(src_cir, os.path.join(s1_dir, fname))

    # 2. Demo 1: 1 MHz RLC Band-Pass Filter
    s2_dir = os.path.join(showcase_dir, "02_rlc_1mhz_bandpass_filter")
    run_demo1(s2_dir)

    # 3. Demo 2 & Demo 4: 2-Stage Cascaded BJT Amplifier in All 3 Auto-Layout Styles + Hand-Wired
    s3_dir = os.path.join(showcase_dir, "03_cascaded_bjt_amp_layout_styles")
    run_demo2(s3_dir)
    run_demo4(s3_dir)

    # 4. Demo 3: Vision-to-SPICE Reconstructed BJT Amplifier
    s4_dir = os.path.join(showcase_dir, "04_vision_to_spice_reconstruction")
    res3 = run_demo3(s4_dir)
    with open(os.path.join(s4_dir, "missing_params_audit_prompt.md"), "w", encoding="utf-8") as f:
        f.write(res3["user_prompt_markdown"] + "\n")

    # Clean up temporary LTspice .net/.log/.raw/.op.raw files inside showcase/ so only clean deliverables remain
    for root, _, files in os.walk(showcase_dir):
        for fn in files:
            if fn.endswith((".raw", ".log", ".net", ".fft", ".tmp")):
                os.remove(os.path.join(root, fn))


if __name__ == "__main__":
    assets_path = os.path.join(ROOT_DIR, "assets")
    showcase_path = os.path.join(ROOT_DIR, "showcase")
    generate_charts(assets_path)
    populate_showcase(showcase_path)
    print("[OK] Generated 300 DPI charts in assets/ and populated showcase/ directory!")
