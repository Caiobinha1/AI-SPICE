"""
Demo 3: Vision-to-SPICE Schematic Reconstruction from Image Graph
=================================================================
Demonstrates how IA-SPICE handles a circuit extracted from a user's schematic photo where
several critical parameters are missing or unreadable (`value="?"`):
1. Runs in `mode="ask_user"` -> detects all missing values (`VCC`, `VIN`, `Q1`, `R1`, `R2`, `RC`, `RE`, `CIN`)
   and generates the structured markdown table asking the user whether to provide values or let
   IA-SPICE size them autonomously.
2. Runs in `mode="autonomous"` (or with partial user overrides) -> calculates physically sound E24
   values, compiles a clean 16-pixel grid `.asc` schematic and `.cir` netlist, verifies `NC_ = 0`,
   and simulates the reconstructed circuit in LTspice!
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.netlist_verifier import verify_asc_netlist
from ia_spice.simulator import run_ltspice_batch
from ia_spice.vision_to_spice import (
    ExtractedComponent,
    VisionCircuitGraph,
    resolve_and_compile_vision_graph,
)


def build_incomplete_image_graph() -> VisionCircuitGraph:
    """
    Simulates what a Vision LLM extracts when looking at a textbook photo of a Common-Emitter
    BJT amplifier where only component labels (R1, R2, RC, RE, Q1, CIN, COUT) and node topology
    are drawn, without numeric component values!
    """
    return VisionCircuitGraph(
        title="Textbook Common-Emitter BJT Amplifier (From Photo)",
        components=[
            ExtractedComponent("VIN", "voltage", ["IN", "GND"], value="?", role_hint="ac_input"),
            ExtractedComponent("CIN", "cap", ["IN", "B"], value="?", role_hint="coupling_cap"),
            ExtractedComponent("R1", "res", ["VCC", "B"], value="?", role_hint="pullup_base"),
            ExtractedComponent("R2", "res", ["B", "GND"], value="?", role_hint="pulldown_base"),
            ExtractedComponent("RC", "res", ["VCC", "C"], value="?", role_hint="collector_res"),
            ExtractedComponent("RE", "res", ["E", "GND"], value="?", role_hint="emitter_res"),
            ExtractedComponent("Q1", "npn", ["C", "B", "E"], value="?"),
            ExtractedComponent("COUT", "cap", ["C", "OUT"], value="?", role_hint="coupling_cap"),
            ExtractedComponent("RLOAD", "res", ["OUT", "GND"], value="10k", role_hint="load"),
        ],
        design_targets={"v_cc": 9.0, "f_target_hz": 1000.0, "i_c_a": 1.5e-3},
    )


def run_demo3(output_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    # Phase A: Audit in 'ask_user' mode (identifies missing info & formats prompt for user)
    graph_ask = build_incomplete_image_graph()
    audit_ask, cir_none, sch_none = resolve_and_compile_vision_graph(graph_ask, mode="ask_user")
    assert not audit_ask.is_complete
    assert cir_none is None and sch_none is None
    user_prompt = audit_ask.format_user_prompt()

    # Phase B: User says "Size and choose them autonomously!" (mode="autonomous")
    graph_auto = build_incomplete_image_graph()
    audit_auto, cir_text, sch = resolve_and_compile_vision_graph(graph_auto, mode="autonomous")
    assert cir_text is not None and sch is not None

    # Add transient simulation & measurement directives to verify the reconstructed circuit works!
    sch.add_directive(".tran 0 5m 0 5u")
    sch.add_directive(".meas tran VIN_PP PP V(IN) FROM=2m TO=5m")
    sch.add_directive(".meas tran VOUT_PP PP V(OUT) FROM=2m TO=5m")
    sch.add_directive(".meas tran AV PARAM VOUT_PP/VIN_PP")
    sch.add_directive(".meas tran VC_DC AVG V(C) FROM=2m TO=5m")

    asc_path = os.path.join(output_dir, "demo3_reconstructed_from_photo.asc")
    cir_path = os.path.join(output_dir, "demo3_reconstructed_from_photo.cir")
    sch.save(asc_path)
    with open(cir_path, "w", encoding="utf-8") as f:
        f.write(cir_text)

    # Verify zero unconnected pins (NC_ = 0) and 100% isomorphism between .asc and .cir
    ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
    assert ver.is_clean, f"Verification failed: {ver}"

    # Simulate in LTspice batch mode
    sim = run_ltspice_batch(asc_path)
    assert sim.succeeded, f"Simulation failed: {sim.diagnostics}"

    av_sim = sim.parsed_log.get_scalar("av")
    vc_dc = sim.parsed_log.get_scalar("vc_dc")

    return {
        "missing_count_detected": len(audit_ask.missing_items),
        "user_prompt_markdown": user_prompt,
        "asc_path": asc_path,
        "cir_path": cir_path,
        "is_clean": ver.is_clean,
        "simulated_gain_vv": av_sim,
        "collector_dc_v": vc_dc,
    }


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "out_demo3")
    res = run_demo3(out)
    print(res["user_prompt_markdown"])
    print("\n" + "=" * 70)
    print(
        f"[DEMO 3 PASS] Detected {res['missing_count_detected']} missing image params -> "
        f"Autonomously sized & simulated in LTspice: VC_DC={res['collector_dc_v']:.2f}V, "
        f"Av={res['simulated_gain_vv']:.2f} V/V, NC_=0"
    )
