"""
Demo 1: RLC Series-Shunt Band-Pass Filter (1 MHz Center Frequency)
==================================================================
Tests multiple IA-SPICE features from scratch on a clean circuit:
1. Analytical sizing & E24 series snapping (`snap_to_series`)
2. 16-pixel grid `.asc` schematic compilation (`SchematicBuilder`)
3. Formal netlist verification (`verify_asc_netlist` -> NC_ = 0 & 100% node match)
4. Live headless LTspice `.ac` simulation & `.meas` extraction (`run_ltspice_batch`)
5. Automated Markdown/HTML report & comparison chart generation (`reporting`)
"""

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.asc_compiler import SchematicBuilder
from ia_spice.netlist_verifier import verify_asc_netlist
from ia_spice.reporting import EngineeringReportSpec, MetricRow, render_markdown_report
from ia_spice.simulator import run_ltspice_batch
from ia_spice.sizing import format_spice_value, snap_to_series


def run_demo1(output_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    # 1. Analytical Sizing: Parallel LC tank at f0 = 1.0 MHz driven through R_s = 1 kOhm
    f0_target = 1.0e6
    l_val, _ = snap_to_series(10.0e-6, "E12")  # 10 uH
    c_ideal = 1.0 / (((2.0 * math.pi * f0_target) ** 2) * l_val)  # 2.533 nF
    c_val, c_err = snap_to_series(c_ideal, "E24")                 # 2.4 nF
    r_s, _ = snap_to_series(1000.0, "E24")                        # 1 kOhm

    f0_analytical = 1.0 / (2.0 * math.pi * math.sqrt(l_val * c_val))  # ~1.0273 MHz
    r_ser = 0.5  # Inductor series resistance (Ohms)
    xl_f0 = 2.0 * math.pi * f0_analytical * l_val
    r_p = (xl_f0 ** 2) / r_ser  # Parallel tank equivalent resistance (~8.33 kOhm)
    vmax_analytical = r_p / (r_s + r_p)  # ~0.8929 V/V

    # 2. Build 16px Grid .asc Schematic
    sch = SchematicBuilder(width=800, height=560)
    vin = sch.add_component("VIN", "voltage", x=64, y=192, value="AC 1")
    rs = sch.add_component("RS", "res", x=224, y=192, value=format_spice_value(r_s), orientation="R90")
    l1 = sch.add_component("L1", "ind", x=304, y=224, value=format_spice_value(l_val), orientation="R0", spice_line=f"Rser={r_ser}")
    c1 = sch.add_component("C1", "cap", x=416, y=240, value=format_spice_value(c_val), orientation="R0")

    sch.add_flag(vin.pin("top"), "IN")
    sch.ground_pin(vin.pin("bot"))
    sch.connect_points(vin.pin("top"), rs.pin("left"))

    sch.add_flag(rs.pin("right"), "OUT")
    sch.connect_points(rs.pin("right"), l1.pin("top"))
    sch.ground_pin(l1.pin("bot"))

    sch.connect_points(l1.pin("top"), c1.pin("top"))
    sch.ground_pin(c1.pin("bot"))

    sch.add_directive(".ac dec 500 100k 10Meg")
    sch.add_directive(".meas ac VMAX MAX mag(V(OUT))")
    sch.add_directive(".meas ac F0 WHEN ph(V(OUT))=0")
    sch.add_directive(".meas ac ATTEN_100K FIND mag(V(OUT)) AT 100k")

    asc_path = os.path.join(output_dir, "demo1_rlc_bandpass.asc")
    sch.save(asc_path)

    # 3. Formal Netlist Verification (NC_ = 0 + Node Match)
    ver = verify_asc_netlist(
        asc_path,
        expected_connections={
            "VIN": ["IN", "0"],
            "RS": ["IN", "OUT"],
            "L1": ["OUT", "0"],
            "C1": ["OUT", "0"],
        },
    )
    assert ver.is_clean, f"Verification failed: {ver}"

    # 4. Run Headless LTspice Simulation
    sim = run_ltspice_batch(asc_path)
    assert sim.succeeded, f"Simulation failed: {sim.diagnostics}"

    f0_sim = sim.parsed_log.get_scalar("f0")
    vmax_sim = sim.parsed_log.get_scalar("vmax", linearize_db=True)
    atten_100k = sim.parsed_log.get_scalar("atten_100k", linearize_db=True)

    # 5. Generate Report
    rep_spec = EngineeringReportSpec(
        title="Demo 1: 1 MHz RLC Band-Pass Filter",
        subtitle="Multi-Feature IA-SPICE Validation",
        authors="IA-SPICE Automated Suite",
        metrics=[
            MetricRow("RLC Tank", "Resonant Frequency f_0", "MHz", "1.027 MHz", f0_analytical * 1e-6, f0_sim * 1e-6, tolerance_pct=2.0),
            MetricRow("RLC Tank", "Peak Passband Gain", "V/V", "Rp/(Rs+Rp)", vmax_analytical, vmax_sim, tolerance_pct=2.0),
        ],
    )
    md_path = os.path.join(output_dir, "demo1_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown_report(rep_spec))

    return {
        "asc_path": asc_path,
        "is_clean": ver.is_clean,
        "f0_analytical_hz": f0_analytical,
        "f0_sim_hz": f0_sim,
        "vmax_analytical": vmax_analytical,
        "vmax_sim": vmax_sim,
        "atten_100k": atten_100k,
        "report_md": md_path,
    }


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "out_demo1")
    res = run_demo1(out)
    print(f"[DEMO 1 PASS] f0_analytical={res['f0_analytical_hz']*1e-6:.4f} MHz | f0_sim={res['f0_sim_hz']*1e-6:.4f} MHz | NC_=0")
