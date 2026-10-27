"""
Unit and live LTspice integration tests for Step 10:
Generalized Schematic Layout Engine for Arbitrary Circuit Topologies.

This suite proves that the AI-SPICE layout compiler is NOT overfitted to specific
amplifier stages, but applies universal graph-theoretic signal-flow algorithms across:
1. Full-Wave Diode Bridge Rectifier with smoothing reservoir and load.
2. Sallen-Key 2nd-order active Low-Pass Op-Amp Filter (verifying input shunts precede the active block).
3. Twin-T Notch Filter (verifying topological DAG deferral of load until all parallel branches converge).
4. 5th-Order LC Chebyshev Ladder (verifying monotonic alternating series-L / shunt-C ladder sequence).
5. BJT Differential Pair with shared tail emitter and collector pull-ups.
6. Antiparallel Diode Clipper pair with symmetric orientation.

All topologies are formally verified for:
- Zero off-grid pins / zero overlapping components (Geometric DRC == 0)
- Zero floating pins (`NC_ = 0`, `unconnected_pins == []`)
- 100% Netlist Isomorphism (`ver.is_clean == True`)
- Headless LTspice batch execution with expected electrical behavior.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.ltspice_project import compile_netlist_to_asc, verify_asc_netlist
from ia_spice.simulator import run_ltspice_batch


NETLIST_BRIDGE_RECTIFIER = """* Full-Wave Diode Bridge Rectifier with Smoothing Filter
VAC AC1 AC2 SINE(0 12 60)
RISO AC2 0 10MEG
D1 AC1 DC_POS 1N4148
D2 0 AC1 1N4148
D3 AC2 DC_POS 1N4148
D4 0 AC2 1N4148
C_FILT DC_POS 0 100u
RLOAD DC_POS 0 1k
.model 1N4148 D(Is=2.52n Rs=.568 N=1.752 Cjo=4p M=.4 tt=20n)
.tran 0 50m 0 10u
.meas TRAN vdc_avg AVG V(DC_POS) FROM=30m TO=50m
.meas TRAN vdc_ripple PP V(DC_POS) FROM=30m TO=50m
.end
"""

NETLIST_SALLEN_KEY = """* Sallen-Key 2nd-order Low-Pass Filter
VIN IN 0 SINE(0 1 1k)
R1 IN MID 10k
R2 MID NINV 10k
C1 OUT MID 10n
C2 NINV 0 10n
X1 NINV OUT VDD VSS OUT UniversalOpamp2
RL OUT 0 10k
VDD VDD 0 15
VSS VSS 0 -15
.tran 0 3m 0 1u
.meas TRAN vin_max MAX V(IN) FROM=1m TO=3m
.meas TRAN vin_min MIN V(IN) FROM=1m TO=3m
.meas TRAN vout_max MAX V(OUT) FROM=1m TO=3m
.meas TRAN vout_min MIN V(OUT) FROM=1m TO=3m
.meas TRAN vin_pp PARAM (vin_max - vin_min)
.meas TRAN vout_pp PARAM (vout_max - vout_min)
.meas TRAN gain_vv PARAM (vout_pp / vin_pp)
.end
"""

NETLIST_TWIN_T = """* Twin-T Notch Filter (60 Hz Notch)
VIN IN 0 SINE(0 1 60)
R1 IN N_R 26.5k
R2 N_R OUT 26.5k
C_SH N_R 0 200n
C1 IN N_C 100n
C2 N_C OUT 100n
R_SH N_C 0 13.3k
RLOAD OUT 0 100k
.tran 0 100m 0 10u
.meas TRAN vout_max MAX V(OUT) FROM=60m TO=100m
.meas TRAN vout_min MIN V(OUT) FROM=60m TO=100m
.meas TRAN vout_pp PARAM (vout_max - vout_min)
.end
"""

NETLIST_LC_LADDER = """* 5th-Order LC Low-Pass Chebyshev Filter
VIN IN 0 SINE(0 1 100k)
RS IN L1_IN 50
L1 L1_IN C1_NODE 150u
C1 C1_NODE 0 100n
L2 C1_NODE C2_NODE 300u
C2 C2_NODE 0 100n
L3 C2_NODE OUT 150u
RLOAD OUT 0 50
.tran 0 100u 0 10n
.meas TRAN vout_max MAX V(OUT) FROM=80u TO=100u
.meas TRAN vout_min MIN V(OUT) FROM=80u TO=100u
.meas TRAN vout_pp PARAM (vout_max - vout_min)
.end
"""

NETLIST_DIFF_PAIR = """* BJT Differential Pair
VIN1 IN1 0 SINE(0 10m 1k)
VIN2 IN2 0 0
RC1 VCC C1 4.7k
RC2 VCC C2 4.7k
Q1 C1 IN1 E 0 2N2222
Q2 C2 IN2 E 0 2N2222
RE_TAIL E VEE 2.2k
VCC VCC 0 15
VEE VEE 0 -15
.model 2N2222 NPN(IS=1E-14 BF=150 VAF=100)
.tran 0 5m 0 1u
.meas TRAN vc1_max MAX V(C1) FROM=3m TO=5m
.meas TRAN vc1_min MIN V(C1) FROM=3m TO=5m
.meas TRAN vc1_pp PARAM (vc1_max - vc1_min)
.meas TRAN diff_gain PARAM (vc1_pp / 20m)
.end
"""

NETLIST_ANTIPARALLEL_CLIPPER = """* Antiparallel Diode Clipper
VIN IN 0 SINE(0 2 1k)
RIN IN CLIP 1k
D1 CLIP 0 1N4148
D2 0 CLIP 1N4148
RLOAD CLIP 0 10k
.model 1N4148 D(Is=2.52n Rs=.568 N=1.752 Cjo=4p M=.4 tt=20n)
.tran 0 3m 0 1u
.meas TRAN vclip_max MAX V(CLIP) FROM=1m TO=3m
.meas TRAN vclip_min MIN V(CLIP) FROM=1m TO=3m
.meas TRAN vclip_pp PARAM (vclip_max - vclip_min)
.end
"""


class TestStep10GeneralizedLayouts(unittest.TestCase):
    def test_full_wave_diode_bridge_rectifier(self):
        """Bridge rectifier compiles without short circuits, zero NC pins, and simulates DC output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "bridge.cir")
            asc_path = os.path.join(tmpdir, "bridge.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_BRIDGE_RECTIFIER)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"Bridge DRC issues: {drc}")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"Bridge netlist verification failed: {ver.mismatches}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"Bridge sim failed: {sim.diagnostics}")
            vdc = sim.parsed_log.get_scalar("vdc_avg")
            ripple = sim.parsed_log.get_scalar("vdc_ripple")
            self.assertIsNotNone(vdc)
            self.assertGreater(vdc, 9.0, f"Expected rectified DC > 9V, got {vdc}")
            self.assertLess(ripple, 1.5, f"Expected ripple < 1.5V, got {ripple}")

    def test_sallen_key_active_lowpass_filter(self):
        """Input shunt C2 precedes Op-Amp X1, zero NC pins, and cutoff gain matches ~0.71 V/V."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "sallen_key.cir")
            asc_path = os.path.join(tmpdir, "sallen_key.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_SALLEN_KEY)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"Sallen-Key DRC issues: {drc}")

            # Verify topological ordering: C2 precedes X1
            c2_x = sch.components["C2"].x
            x1_x = sch.components["X1"].x
            self.assertLess(c2_x, x1_x, f"Input shunt C2 ({c2_x}) must precede Op-Amp X1 ({x1_x})")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"Sallen-Key verification failed: {ver.mismatches}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"Sallen-Key sim failed: {sim.diagnostics}")
            gain = sim.parsed_log.get_scalar("gain_vv")
            self.assertIsNotNone(gain)
            self.assertGreater(gain, 0.65)
            self.assertLess(gain, 0.75)

    def test_twin_t_notch_filter(self):
        """Topological load deferral places RLOAD strictly after both parallel T-branches converge."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "twin_t.cir")
            asc_path = os.path.join(tmpdir, "twin_t.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_TWIN_T)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"Twin-T DRC issues: {drc}")

            # Verify DAG load placement: RLOAD x-coord is >= both R2 and C2
            rload_x = sch.components["RLOAD"].x
            r2_x = sch.components["R2"].x
            c2_x = sch.components["C2"].x
            self.assertGreaterEqual(rload_x, max(r2_x, c2_x), "RLOAD must terminate after both branches")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"Twin-T verification failed: {ver.mismatches}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"Twin-T sim failed: {sim.diagnostics}")
            vout_pp = sim.parsed_log.get_scalar("vout_pp")
            self.assertIsNotNone(vout_pp)
            self.assertLess(vout_pp, 0.005, f"Expected notch suppression < 5mV, got {vout_pp}")

    def test_5th_order_chebyshev_lc_ladder(self):
        """Ladder filter forms monotonic alternating series-L and shunt-C topology left-to-right."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "lc_ladder.cir")
            asc_path = os.path.join(tmpdir, "lc_ladder.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_LC_LADDER)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"LC Ladder DRC issues: {drc}")

            # Verify strictly monotonic left-to-right ladder ordering
            comps = sch.components
            self.assertLess(comps["RS"].x, comps["L1"].x)
            self.assertLess(comps["L1"].x, comps["C1"].x)
            self.assertLess(comps["C1"].x, comps["L2"].x)
            self.assertLess(comps["L2"].x, comps["C2"].x)
            self.assertLess(comps["C2"].x, comps["L3"].x)
            self.assertLess(comps["L3"].x, comps["RLOAD"].x)

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"LC Ladder verification failed: {ver.mismatches}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"LC Ladder sim failed: {sim.diagnostics}")
            vout_pp = sim.parsed_log.get_scalar("vout_pp")
            self.assertIsNotNone(vout_pp)
            self.assertLess(vout_pp, 0.05, f"Expected low-pass attenuation at 100kHz, got {vout_pp}")

    def test_bjt_differential_pair(self):
        """Differential pair compiles cleanly with shared tail and differential gain > 100 V/V."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "diff_pair.cir")
            asc_path = os.path.join(tmpdir, "diff_pair.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_DIFF_PAIR)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"Diff Pair DRC issues: {drc}")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"Diff Pair verification failed: {ver.mismatches}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"Diff Pair sim failed: {sim.diagnostics}")
            gain = sim.parsed_log.get_scalar("diff_gain")
            self.assertIsNotNone(gain)
            self.assertGreater(gain, 100.0, f"Expected differential gain > 100, got {gain}")

    def test_antiparallel_diode_clipper(self):
        """Antiparallel diode clipper pair renders side-by-side with horizontal rails and symmetrical clipping."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "clipper.cir")
            asc_path = os.path.join(tmpdir, "clipper.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_ANTIPARALLEL_CLIPPER)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"Clipper DRC issues: {drc}")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"Clipper verification failed: {ver.mismatches}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"Clipper sim failed: {sim.diagnostics}")
            vclip_pp = sim.parsed_log.get_scalar("vclip_pp")
            self.assertIsNotNone(vclip_pp)
            # Diode forward drops clamp +/- 0.6V to 0.7V -> total ~1.2V to 1.5V pp from 4V pp input
            self.assertGreater(vclip_pp, 1.0)
            self.assertLess(vclip_pp, 1.6)


if __name__ == "__main__":
    unittest.main()
