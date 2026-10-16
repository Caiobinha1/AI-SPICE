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

    # 3. Compile 2-Stage .asc Schematic on 16px Grid with Full Orthogonal Wiring & Generous Spacing (>=144px)
    sch = SchematicBuilder(width=1856, height=1120)

    # Supply rail source (top-left)
    vcc = sch.add_component("VCC", "voltage", x=64, y=80, value="12")
    sch.power_pin(vcc.pin("top"), "VCC_12V")
    sch.ground_pin(vcc.pin("bot"), stub_dy=32)

    # Input signal source (bottom-left, plenty of clearance for SINE text)
    vin = sch.add_component("VIN", "voltage", x=64, y=336, value="SINE(0 0.05 1k)")
    sch.ground_pin(vin.pin("bot"), stub_dy=32)

    # Input coupling capacitor (x=272, y=240 -> left=(208,256), right=(272,256))
    cin = sch.add_component("CIN", "cap", x=272, y=240, value="10u", orientation="R90")
    sch.connect_points(vin.pin("top"), (64, 256), vh_order="VH")
    sch.connect_points((64, 256), cin.pin("left"), vh_order="HV")
    sch.add_flag((128, 256), "IN")

    # Stage 1 Base bias divider (x=416 -> pin X = 432)
    r1 = sch.add_component("R1", "res", x=416, y=80, value="47k")
    r2 = sch.add_component("R2", "res", x=416, y=368, value="12k")
    sch.power_pin(r1.pin("top"), "VCC_12V")
    sch.connect_points(r1.pin("bot"), r2.pin("top"))
    sch.ground_pin(r2.pin("bot"), stub_dy=32)

    # Stage 1 NPN Transistor Q1 (x=592, y=208 -> B=(592,256), C=(656,208), E=(656,304))
    q1 = sch.add_component("Q1", "npn", x=592, y=208, value="2N2222")
    sch.connect_points(cin.pin("right"), (432, 256))
    sch.connect_points((432, 256), q1.pin("B"))
    sch.add_flag((512, 256), "B1")

    # Stage 1 Collector & Split Emitter Resistors (x=640 -> pin X = 656)
    rc1 = sch.add_component("RC1", "res", x=640, y=64, value="2.2k")
    sch.power_pin(rc1.pin("top"), "VCC_12V")
    sch.connect_points(rc1.pin("bot"), q1.pin("C"))

    re1a = sch.add_component("RE1A", "res", x=640, y=352, value="220")
    sch.connect_points(q1.pin("E"), re1a.pin("top"))

    re1b = sch.add_component("RE1B", "res", x=640, y=528, value="470")
    sch.connect_points(re1a.pin("bot"), re1b.pin("top"))
    sch.ground_pin(re1b.pin("bot"), stub_dy=32)

    # Stage 1 Emitter Bypass Capacitor CE1 (x=816, y=544 -> pin X = 832)
    ce1 = sch.add_component("CE1", "cap", x=816, y=544, value="100u")
    sch.connect_points((656, 496), (832, 496))
    sch.connect_points((832, 496), ce1.pin("top"))
    sch.add_flag((736, 496), "E1_MID")
    sch.ground_pin(ce1.pin("bot"), stub_dy=48)

    # Inter-stage Coupling Capacitor CMID (x=912, y=176 -> left=(848,192), right=(912,192))
    cmid = sch.add_component("CMID", "cap", x=912, y=176, value="10u", orientation="R90")
    sch.connect_points((656, 192), cmid.pin("left"))
    sch.add_flag((752, 192), "C1")

    # Stage 2 Base Bias Divider R3 & R4 (x=1056 -> pin X = 1072)
    r3 = sch.add_component("R3", "res", x=1056, y=64, value="22k")
    r4 = sch.add_component("R4", "res", x=1056, y=352, value="22k")
    sch.power_pin(r3.pin("top"), "VCC_12V")
    sch.connect_points(r3.pin("bot"), r4.pin("top"))
    sch.ground_pin(r4.pin("bot"), stub_dy=32)

    # Stage 2 Emitter-Follower Buffer Q2 (x=1232, y=208 -> B=(1232,256), C=(1296,208), E=(1296,304))
    q2 = sch.add_component("Q2", "npn", x=1232, y=208, value="2N2222")
    sch.connect_points(cmid.pin("right"), (1072, 192))
    sch.connect_points((1072, 256), q2.pin("B"))
    sch.add_flag((1152, 256), "B2")
    sch.power_pin(q2.pin("C"), "VCC_12V")

    re2 = sch.add_component("RE2", "res", x=1280, y=400, value="470")
    sch.connect_points(q2.pin("E"), re2.pin("top"))
    sch.ground_pin(re2.pin("bot"), stub_dy=32)

    # Output Coupling Capacitor COUT (x=1504, y=336 -> left=(1440,352), right=(1504,352))
    cout = sch.add_component("COUT", "cap", x=1504, y=336, value="47u", orientation="R90")
    sch.connect_points((1296, 352), cout.pin("left"))
    sch.add_flag((1360, 352), "E2")

    # Heavy 100-Ohm Load Resistor RLOAD (x=1648, y=400 -> pin X = 1664)
    rload = sch.add_component("RLOAD", "res", x=1648, y=400, value="100")
    sch.connect_points(cout.pin("right"), (1664, 352))
    sch.connect_points((1664, 352), rload.pin("top"))
    sch.add_flag((1584, 352), "OUT")
    sch.ground_pin(rload.pin("bot"), stub_dy=32)

    # Directives are automatically placed in a dedicated footer >=160px below all components
    sch.add_directive(".tran 0 5m 0 2u")
    sch.add_directive(".meas tran VIN_PP PP V(IN) FROM=2m TO=5m")
    sch.add_directive(".meas tran VC1_PP PP V(C1) FROM=2m TO=5m")
    sch.add_directive(".meas tran VOUT_PP PP V(OUT) FROM=2m TO=5m")
    sch.add_directive(".meas tran AV_TOTAL PARAM VOUT_PP/VIN_PP")

    asc_path = os.path.join(output_dir, "demo2_cascaded_amp.asc")
    sch.save(asc_path)

    # 4. Verify Geometric DRC, Netlist Isomorphism & Simulate
    drc_issues = sch.run_geometric_drc()
    assert len(drc_issues) == 0, f"Geometric DRC issues: {drc_issues}"
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
