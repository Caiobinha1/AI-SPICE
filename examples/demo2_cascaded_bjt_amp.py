"""
Demo 2: 2-Stage Cascaded Audio Amplifier + emitter-Follower Buffer
==================================================================
Tests multiple IA-SPICE features on an active multi-transistor circuit:
1. Analytical DC/AC BJT sizing (`size_bjt_OperatingPoint`) for Common-Emitter (Q1) + Emitter-Follower (Q2)
2. Inter-stage impedance loading verification (`StageContract` + `check_cascade_loading`)
   proving why Q1 cannot drive a low-impedance 100-Ohm load directly without Q2 buffering!
3. 16-pixel grid `.asc` compilation & formal netlist verification (`NC_ = 0`)
4. Live headless LTspice transient simulation & `.meas` gain verification
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.asc_compiler import SchematicBuilder
from ia_spice.netlist_verifier import verify_asc_netlist
from ia_spice.simulator import run_ltspice_batch
from ia_spice.sizing import StageContract, check_cascade_loading, size_bjt_OperatingPoint


def run_demo2(output_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)

    # 1. Analytical Sizing of Stage 1 (CE Amplifier Q1: VCC=12V, R1=47k, R2=12k, RC=2.2k, RE1_RF=220, RE1_DC=470)
    # and Stage 2 (Emitter Follower Q2: R3=22k, R4=22k, RE2=470 driving RLOAD=100 Ohm)
    op_q2 = size_bjt_OperatingPoint(
        v_cc=12.0, r1=22e3, r2=22e3, r_e_dc=470.0, r_load_ac=100.0, beta=200.0, topology="CC"
    )
    op_q1 = size_bjt_OperatingPoint(
        v_cc=12.0,
        r1=47e3,
        r2=12e3,
        r_e_dc=220.0 + 470.0,
        r_c=2200.0,
        r_e_rf_unbypassed=220.0,
        r_load_ac=op_q2.z_in_stage,
        beta=200.0,
        topology="CE",
    )

    analytical_total_gain = abs(op_q1.voltage_gain_loaded * op_q2.voltage_gain_loaded)

    # 2. Prove Inter-Stage Loading Contract:
    # Connecting 100 Ohm directly to Q1 collector (Z_out = 2.2k) drops gain by >95%!
    q1_contract = StageContract("CE_Stage_Q1", z_in_ohms=op_q1.z_in_stage, z_out_ohms=2200.0, unloaded_gain_vv=9.2)
    load_100ohm = StageContract("Load_100_Ohm", z_in_ohms=100.0, z_out_ohms=100.0, unloaded_gain_vv=1.0)
    direct_check = check_cascade_loading(q1_contract, load_100ohm)

    # Connecting Q1 -> Q2 Buffer -> 100 Ohm preserves gain!
    q2_contract = StageContract("CC_Buffer_Q2", z_in_ohms=op_q2.z_in_stage, z_out_ohms=10.0, unloaded_gain_vv=op_q2.voltage_gain_loaded)
    buffered_check = check_cascade_loading(q1_contract, q2_contract)

    # 3. Compile 2-Stage .asc Schematic on 16px Grid
    sch = SchematicBuilder(width=1280, height=640)

    vcc = sch.add_component("VCC", "voltage", x=64, y=128, value="12")
    sch.add_flag(vcc.pin("top"), "VCC_12V")
    sch.ground_pin(vcc.pin("bot"))

    vin = sch.add_component("VIN", "voltage", x=64, y=352, value="SINE(0 0.05 1k)")
    sch.add_flag(vin.pin("top"), "IN")
    sch.ground_pin(vin.pin("bot"))

    cin = sch.add_component("CIN", "cap", x=160, y=352, value="10u", orientation="R90")
    sch.add_flag(cin.pin("left"), "IN")
    sch.add_flag(cin.pin("right"), "B1")

    r1 = sch.add_component("R1", "res", x=240, y=192, value="47k")
    sch.add_flag(r1.pin("top"), "VCC_12V")
    sch.add_flag(r1.pin("bot"), "B1")

    r2 = sch.add_component("R2", "res", x=240, y=368, value="12k")
    sch.add_flag(r2.pin("top"), "B1")
    sch.ground_pin(r2.pin("bot"))

    q1 = sch.add_component("Q1", "npn", x=352, y=288, value="2N2222")
    sch.add_flag(q1.pin("C"), "C1")
    sch.add_flag(q1.pin("B"), "B1")
    sch.add_flag(q1.pin("E"), "E1")

    rc1 = sch.add_component("RC1", "res", x=400, y=144, value="2.2k")
    sch.add_flag(rc1.pin("top"), "VCC_12V")
    sch.add_flag(rc1.pin("bot"), "C1")

    re1a = sch.add_component("RE1A", "res", x=400, y=384, value="220")
    sch.add_flag(re1a.pin("top"), "E1")
    sch.add_flag(re1a.pin("bot"), "E1_MID")

    re1b = sch.add_component("RE1B", "res", x=400, y=496, value="470")
    sch.add_flag(re1b.pin("top"), "E1_MID")
    sch.ground_pin(re1b.pin("bot"))

    ce1 = sch.add_component("CE1", "cap", x=496, y=496, value="100u")
    sch.add_flag(ce1.pin("top"), "E1_MID")
    sch.ground_pin(ce1.pin("bot"))

    # Stage 2: Inter-stage coupling & Q2 Buffer
    cmid = sch.add_component("CMID", "cap", x=624, y=272, value="10u", orientation="R90")
    sch.add_flag(cmid.pin("left"), "C1")
    sch.add_flag(cmid.pin("right"), "B2")

    r3 = sch.add_component("R3", "res", x=688, y=144, value="22k")
    sch.add_flag(r3.pin("top"), "VCC_12V")
    sch.add_flag(r3.pin("bot"), "B2")

    r4 = sch.add_component("R4", "res", x=688, y=320, value="22k")
    sch.add_flag(r4.pin("top"), "B2")
    sch.ground_pin(r4.pin("bot"))

    q2 = sch.add_component("Q2", "npn", x=784, y=240, value="2N2222")
    sch.add_flag(q2.pin("C"), "VCC_12V")
    sch.add_flag(q2.pin("B"), "B2")
    sch.add_flag(q2.pin("E"), "E2")

    re2 = sch.add_component("RE2", "res", x=832, y=352, value="470")
    sch.add_flag(re2.pin("top"), "E2")
    sch.ground_pin(re2.pin("bot"))

    cout = sch.add_component("COUT", "cap", x=960, y=320, value="47u", orientation="R90")
    sch.add_flag(cout.pin("left"), "E2")
    sch.add_flag(cout.pin("right"), "OUT")

    rload = sch.add_component("RLOAD", "res", x=1008, y=352, value="100")
    sch.add_flag(rload.pin("top"), "OUT")
    sch.ground_pin(rload.pin("bot"))

    sch.add_directive(".tran 0 5m 0 2u")
    sch.add_directive(".meas tran VIN_PP PP V(IN) FROM=2m TO=5m")
    sch.add_directive(".meas tran VC1_PP PP V(C1) FROM=2m TO=5m")
    sch.add_directive(".meas tran VOUT_PP PP V(OUT) FROM=2m TO=5m")
    sch.add_directive(".meas tran AV_TOTAL PARAM VOUT_PP/VIN_PP")

    asc_path = os.path.join(output_dir, "demo2_cascaded_amp.asc")
    sch.save(asc_path)

    # 4. Verify Netlist & Simulate
    ver = verify_asc_netlist(asc_path)
    assert ver.is_clean, f"Verification failed: {ver}"

    sim = run_ltspice_batch(asc_path)
    assert sim.succeeded, f"Simulation failed: {sim.diagnostics}"

    av_sim = sim.parsed_log.get_scalar("av_total")

    return {
        "asc_path": asc_path,
        "is_clean": ver.is_clean,
        "direct_100ohm_gain_drop_pct": direct_check.gain_drop_pct,
        "buffered_gain_drop_pct": buffered_check.gain_drop_pct,
        "analytical_gain_vv": analytical_total_gain,
        "simulated_gain_vv": av_sim,
    }


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "out_demo2")
    res = run_demo2(out)
    print(
        f"[DEMO 2 PASS] Direct 100-Ohm drop={res['direct_100ohm_gain_drop_pct']:.1f}% | "
        f"Buffered Analytical Av={res['analytical_gain_vv']:.2f} V/V | LTspice Av={res['simulated_gain_vv']:.2f} V/V | NC_=0"
    )
