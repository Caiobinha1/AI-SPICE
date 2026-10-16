"""
Demo 4: Automatic Multi-Style `.asc` Layout Comparison & Universal LTspice Project Editor
=========================================================================================
1. Takes a pure SPICE netlist (`demo2_cascaded_bjt.cir`) with ZERO manual coordinates and compiles
   it automatically into 3 distinct `.asc` visual layout architectures so the user can inspect all 3
   in LTspice and choose their favorite default style:
   - `estilo_A_textbook_wired.asc` (`style="A_textbook_wired"`): Stage-grouped stacks with local horizontal flags.
   - `estilo_B_power_rail_bus.asc` (`style="B_power_rail_bus"`): Continuous top `VCC_12V` horizontal bus bar.
   - `estilo_C_modular_blocks.asc` (`style="C_modular_blocks"`): Extra-wide modular cells (`256 px` pitch).
2. Demonstrates reading, diagnosing, modifying component values (`modify_asc_component`), and adding new
   components (`add_component_to_asc`) on existing `.asc`, `.cir`, `.sub`, `.asy`, `.plt`, and `.log` files.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.ltspice_project import (
    add_component_to_asc,
    compile_netlist_to_asc,
    inspect_ltspice_project,
    modify_asc_component,
    parse_asy_symbol,
    parse_plt_file,
    parse_spice_library,
    write_plt_file,
)
from ia_spice.netlist_verifier import verify_asc_netlist
from ia_spice.simulator import run_ltspice_batch


DEMO2_NETLIST_TEXT = """* 2-Stage Cascaded BJT Amplifier + Emitter-Follower Buffer
VCC VCC_12V 0 12
VIN IN 0 SINE(0 0.05 1k)
CIN IN B1 10u
R1 VCC_12V B1 47k
R2 B1 0 12k
RC1 VCC_12V C1 2.2k
Q1 C1 B1 E1 2N2222
RE1A E1 E1_MID 220
RE1B E1_MID 0 470
CE1 E1_MID 0 100u
CMID C1 B2 10u
R3 VCC_12V B2 22k
R4 B2 0 22k
Q2 VCC_12V B2 E2 2N2222
RE2 E2 0 470
COUT E2 OUT 47u
RLOAD OUT 0 100
.tran 0 5m 0 2u
.meas tran VIN_PP PP V(IN) FROM=2m TO=5m
.meas tran VOUT_PP PP V(OUT) FROM=2m TO=5m
.meas tran AV_TOTAL PARAM VOUT_PP/VIN_PP
.end
"""


def run_demo4(output_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    cir_path = os.path.join(output_dir, "demo2_reference.cir")
    with open(cir_path, "w", encoding="utf-8") as f:
        f.write(DEMO2_NETLIST_TEXT)

    style_results = {}
    for style_id, fname in [
        ("A_textbook_wired", "estilo_A_textbook_wired.asc"),
        ("B_power_rail_bus", "estilo_B_power_rail_bus.asc"),
        ("C_modular_blocks", "estilo_C_modular_blocks.asc"),
    ]:
        asc_out = os.path.join(output_dir, fname)
        sch = compile_netlist_to_asc(cir_path, asc_out, style=style_id)
        drc = sch.run_geometric_drc()
        assert len(drc) == 0, f"Style {style_id} had DRC issues: {drc}"

        ver = verify_asc_netlist(asc_out, reference_cir_path=cir_path)
        assert ver.is_clean, f"Style {style_id} failed netlist verification: {ver}"

        sim = run_ltspice_batch(asc_out)
        assert sim.succeeded, f"Style {style_id} simulation failed: {sim.diagnostics}"
        style_results[style_id] = {
            "asc_path": asc_out,
            "is_clean": ver.is_clean,
            "drc_issues": len(drc),
            "av_sim": sim.parsed_log.get_scalar("av_total"),
        }

    # Test modifying an existing .asc schematic:
    # 1) Change RLOAD from 100 Ohm to 1k Ohm (E24)
    # 2) Add a 100nF power supply decoupling capacitor C_DEC between VCC_12V and GND
    mod_asc_path = os.path.join(output_dir, "estilo_A_modified_with_cdec.asc")
    modify_asc_component(
        style_results["A_textbook_wired"]["asc_path"],
        inst_name="RLOAD",
        new_value="1k",
        snap_series="E24",
        output_path=mod_asc_path,
    )
    sch_mod = add_component_to_asc(
        mod_asc_path,
        inst_name="C_DEC",
        symbol_type="cap",
        value="100n",
        nodes=["VCC_12V", "0"],
        output_path=mod_asc_path,
    )
    assert len(sch_mod.run_geometric_drc()) == 0
    ver_mod = verify_asc_netlist(mod_asc_path)
    assert ver_mod.is_clean, f"Modified .asc had floating pins: {ver_mod.unconnected_pins}"
    sim_mod = run_ltspice_batch(mod_asc_path)
    assert sim_mod.succeeded
    av_mod = sim_mod.parsed_log.get_scalar("av_total")

    # Test .plt waveform configuration generation & parsing
    plt_path = os.path.join(output_dir, "estilo_A_textbook_wired.plt")
    write_plt_file(plt_path, traces=["V(IN)", "V(C1)", "V(OUT)"])
    parsed_traces = parse_plt_file(plt_path)

    # Test .asy and .sub parsing on existing project if present
    asy_pins_found = 0
    subckt_found = False
    vco_dir = r"C:\analogicos\vco_colpitts_fm\models"
    asy_file = os.path.join(vco_dir, "SMV1237.asy")
    sub_file = os.path.join(vco_dir, "SMV1237.sub")
    if os.path.isfile(asy_file):
        asy_info = parse_asy_symbol(asy_file)
        asy_pins_found = len(asy_info.pins)
    if os.path.isfile(sub_file):
        lib_info = parse_spice_library(sub_file)
        subckt_found = "SMV1237" in lib_info.subckts

    # Full directory inspection
    insp = inspect_ltspice_project(output_dir)

    return {
        "styles": style_results,
        "modified_asc_path": mod_asc_path,
        "modified_av_sim": av_mod,
        "plt_traces": parsed_traces,
        "asy_pins_found": asy_pins_found,
        "subckt_found": subckt_found,
        "inspected_formats": insp.detected_formats,
    }


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "out_demo4")
    res = run_demo4(out)
    for st, info in res["styles"].items():
        print(f"[STYLE {st}] Av={info['av_sim']:.2f} V/V | DRC=0 | NC_=0 -> {info['asc_path']}")
    print(f"[MODIFIED .ASC (+C_DEC, RLOAD=1k)] Av={res['modified_av_sim']:.2f} V/V -> {res['modified_asc_path']}")
