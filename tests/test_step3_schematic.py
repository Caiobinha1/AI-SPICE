"""
Unit and live LTspice schematic compilation tests for Step 3 (`ia_spice.asc_compiler` and `ia_spice.netlist_verifier`).

Validates:
1. 16-pixel grid enforcement (raising ValueError on off-grid coordinates).
2. Generating a complete BJT Emitter Follower `.asc` schematic from scratch via `SchematicBuilder`,
   compiling it with `LTspice.exe -netlist`, verifying `NC_ = 0`, verifying 100% node isomorphism,
   and running a batch simulation (`-b`) to confirm `.meas` results.
3. Detecting floating pins (`NC_01`) when a component pin is intentionally left unconnected.
4. Auditing the existing VCO Colpitts `.asc` schematics in `C:\\analogicos\\Esquematicos` to confirm
   zero unconnected pins.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.asc_compiler import SchematicBuilder
from ia_spice.netlist_verifier import verify_asc_netlist
from ia_spice.simulator import run_ltspice_batch

ESQUEMATICOS_DIR = r"C:\analogicos\Esquematicos"


class TestStep3SchematicCompilerAndVerifier(unittest.TestCase):
    def test_off_grid_coordinate_rejected(self):
        sch = SchematicBuilder()
        with self.assertRaises(ValueError):
            sch.add_component("R1", "res", x=100, y=100, value="1k")  # 100 is not divisible by 16

    def test_build_verify_and_simulate_bjt_emitter_follower_asc(self):
        sch = SchematicBuilder(width=960, height=640)

        # Place components on 16-pixel grid
        vcc = sch.add_component("VCC", "voltage", x=560, y=208, value="9")
        vin = sch.add_component("VAUDIO", "voltage", x=48, y=256, value="SINE(0 0.14 1k)")
        cin = sch.add_component("C_IN", "cap", x=160, y=288, value="1u", orientation="R90")
        rb1 = sch.add_component("RB1", "res", x=224, y=128, value="43k")
        rb2 = sch.add_component("RB2", "res", x=224, y=336, value="52k")
        q4 = sch.add_component("Q4", "npn", x=336, y=256, value="2N2222")
        re4 = sch.add_component("RE4", "res", x=384, y=400, value="3.9k")

        # Power and Ground flags
        sch.add_flag(vcc.pin("top"), "VCC_RAIL")
        sch.ground_pin(vcc.pin("bot"))

        # Input source -> C_IN -> Base node B4
        sch.add_flag(vin.pin("top"), "AUDIO_IN", stub_dy=-16)
        sch.ground_pin(vin.pin("bot"))
        sch.connect_points((48, 256), cin.pin("left"), vh_order="VH")

        # Base node B4 at (240, 304)
        b4_pt = q4.pin("B")  # (336, 304)
        sch.connect_points(cin.pin("right"), b4_pt)
        sch.add_flag((240, 304), "B4")

        # Bias resistors RB1 (to VCC_RAIL) and RB2 (to GND)
        sch.add_flag(rb1.pin("top"), "VCC_RAIL")
        sch.connect_points(rb1.pin("bot"), (240, 304))
        sch.connect_points((240, 304), rb2.pin("top"))
        sch.ground_pin(rb2.pin("bot"))

        # Q4 Collector to VCC_RAIL, Emitter to CTRL and RE4
        sch.add_flag(q4.pin("C"), "VCC_RAIL")
        sch.add_flag(q4.pin("E"), "CTRL")
        sch.connect_points(q4.pin("E"), re4.pin("top"))
        sch.ground_pin(re4.pin("bot"))

        # Directives
        sch.add_directive(".tran 0 5m 0 2u")
        sch.add_directive(".meas tran VE_DC AVG V(CTRL) FROM=1m TO=5m")
        sch.add_directive(".meas tran VB_PP PP V(B4) FROM=1m TO=5m")
        sch.add_directive(".meas tran VE_PP PP V(CTRL) FROM=1m TO=5m")
        sch.add_directive(".meas tran AV PARAM VE_PP/VB_PP")

        self.assertEqual(len(sch.run_geometric_drc()), 0)

        expected = {
            "VCC": ["VCC_RAIL", "0"],
            "VAUDIO": ["AUDIO_IN", "0"],
            "C_IN": ["AUDIO_IN", "B4"],
            "RB1": ["VCC_RAIL", "B4"],
            "RB2": ["B4", "0"],
            "Q4": ["VCC_RAIL", "B4", "CTRL"],
            "RE4": ["CTRL", "0"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            asc_path = os.path.join(tmpdir, "follower_test.asc")
            sch.save(asc_path)

            # 1. Verify netlist isomorphism and zero NC_ pins
            ver = verify_asc_netlist(asc_path, expected_connections=expected)
            self.assertTrue(
                ver.is_clean,
                f"Unconnected: {ver.unconnected_pins}, Mismatches: {ver.mismatches}, Missing: {ver.missing_components}",
            )

            # 2. Simulate the generated .asc directly in LTspice batch mode
            sim_res = run_ltspice_batch(asc_path)
            self.assertTrue(sim_res.succeeded, f"Diagnostics: {sim_res.diagnostics}")
            self.assertAlmostEqual(sim_res.parsed_log.get_scalar("ve_dc"), 4.108, delta=0.05)
            self.assertAlmostEqual(sim_res.parsed_log.get_scalar("av"), 0.9934, delta=0.005)

    def test_detect_unconnected_pin_nc(self):
        sch = SchematicBuilder()
        # Place a resistor with no wires or flags attached -> both pins should be flagged as NC_
        sch.add_component("R_FLOAT", "res", x=160, y=160, value="1k")

        with tempfile.TemporaryDirectory() as tmpdir:
            asc_path = os.path.join(tmpdir, "floating.asc")
            sch.save(asc_path)
            ver = verify_asc_netlist(asc_path)
            self.assertFalse(ver.is_clean)
            self.assertGreaterEqual(len(ver.unconnected_pins), 2)

    def test_audit_existing_vco_schematics_zero_nc(self):
        for fname in ("S2_S3_vco_nucleo.asc", "S6_modulacao_fm_completa.asc"):
            fpath = os.path.join(ESQUEMATICOS_DIR, fname)
            if os.path.isfile(fpath):
                ver = verify_asc_netlist(fpath)
                self.assertEqual(len(ver.unconnected_pins), 0, f"Found floating pins in {fname}: {ver.unconnected_pins}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
