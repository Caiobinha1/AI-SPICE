"""
Unit and live LTspice integration tests for Step 9:
Topology-aware schematic compilation and simulation of textbook BJT amplifiers:
1. Common Emitter (CE) with parallel emitter bypass capacitor (RE || CE).
2. Common Collector (CC / Emitter Follower) with direct VCC collector and horizontal emitter output line.
3. Common Base (CB) with input fed to emitter, parallel base bypass capacitor (R2 || CB), and collector output.

All 3 topologies are tested for:
- Analytical sizing via `size_bjt_OperatingPoint(..., topology=...)`
- Automatic grid compilation onto the strict 16-pixel LTspice grid
- Geometric DRC (zero off-grid pins, zero overlapping components)
- Formal netlist isomorphism (`NC_ = 0`, 0 unconnected pins, 0 mismatches)
- Headless LTspice batch simulation with expected voltage gains.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.ltspice_project import compile_netlist_to_asc, verify_asc_netlist
from ia_spice.simulator import run_ltspice_batch
from ia_spice.sizing import size_bjt_OperatingPoint


NETLIST_COMMON_EMITTER = """* Common Emitter BJT Amplifier
VIN IN 0 SINE(0 10m 1k)
CIN B IN 10u
R1 VCC B 47k
R2 B 0 10k
Q1 C B E 0 2N2222
RC VCC C 2.2k
RE E 0 470
CE E 0 47u
COUT OUT C 10u
RLOAD OUT 0 10k
VCC VCC 0 12
.model 2N2222 NPN(IS=1E-14 BF=150 VAF=100)
.tran 0 5m 0 1u
.meas TRAN vin_max MAX V(IN) FROM=3m TO=5m
.meas TRAN vin_min MIN V(IN) FROM=3m TO=5m
.meas TRAN vout_max MAX V(OUT) FROM=3m TO=5m
.meas TRAN vout_min MIN V(OUT) FROM=3m TO=5m
.meas TRAN vin_pp PARAM (vin_max - vin_min)
.meas TRAN vout_pp PARAM (vout_max - vout_min)
.meas TRAN gain_vv PARAM (vout_pp / vin_pp)
.end
"""

NETLIST_COMMON_COLLECTOR = """* Common Collector / Emitter Follower BJT Amplifier
VIN IN 0 SINE(0 100m 1k)
CIN B IN 10u
R1 VCC B 33k
R2 B 0 33k
Q1 VCC B E 0 2N2222
RE E 0 1k
COUT OUT E 10u
RLOAD OUT 0 1k
VCC VCC 0 12
.model 2N2222 NPN(IS=1E-14 BF=150 VAF=100)
.tran 0 5m 0 1u
.meas TRAN vin_max MAX V(IN) FROM=3m TO=5m
.meas TRAN vin_min MIN V(IN) FROM=3m TO=5m
.meas TRAN vout_max MAX V(OUT) FROM=3m TO=5m
.meas TRAN vout_min MIN V(OUT) FROM=3m TO=5m
.meas TRAN vin_pp PARAM (vin_max - vin_min)
.meas TRAN vout_pp PARAM (vout_max - vout_min)
.meas TRAN gain_vv PARAM (vout_pp / vin_pp)
.end
"""

NETLIST_COMMON_BASE = """* Common Base BJT Amplifier
VIN IN 0 SINE(0 10m 1k)
CIN E IN 10u
R1 VCC B 47k
R2 B 0 10k
CB B 0 10u
Q1 C B E 0 2N2222
RC VCC C 3.3k
RE E 0 1k
COUT OUT C 10u
RLOAD OUT 0 10k
VCC VCC 0 12
.model 2N2222 NPN(IS=1E-14 BF=150 VAF=100)
.tran 0 5m 0 1u
.meas TRAN vin_max MAX V(IN) FROM=3m TO=5m
.meas TRAN vin_min MIN V(IN) FROM=3m TO=5m
.meas TRAN vout_max MAX V(OUT) FROM=3m TO=5m
.meas TRAN vout_min MIN V(OUT) FROM=3m TO=5m
.meas TRAN vin_pp PARAM (vin_max - vin_min)
.meas TRAN vout_pp PARAM (vout_max - vout_min)
.meas TRAN gain_vv PARAM (vout_pp / vin_pp)
.end
"""


class TestStep9AmplifierTopologies(unittest.TestCase):
    def test_analytical_sizing_all_topologies(self):
        """Analytical operating point models calculate correct gains and impedances."""
        ce = size_bjt_OperatingPoint(v_cc=12.0, r1=47000.0, r2=10000.0, r_e_dc=470.0, r_c=2200.0, r_e_rf_unbypassed=0.0, r_load_ac=10000.0, beta=150.0, topology="CE")
        self.assertEqual(ce.region, "ACTIVE")
        self.assertGreater(abs(ce.voltage_gain_loaded), 20.0)
        self.assertGreater(ce.z_in_stage, 500.0)

        cc = size_bjt_OperatingPoint(v_cc=12.0, r1=33000.0, r2=33000.0, r_e_dc=1000.0, r_c=0.0, r_load_ac=1000.0, beta=150.0, topology="CC")
        self.assertEqual(cc.region, "ACTIVE")
        self.assertAlmostEqual(cc.voltage_gain_loaded, 1.0, delta=0.05)
        self.assertGreater(cc.z_in_stage, 5000.0)

        cb = size_bjt_OperatingPoint(v_cc=12.0, r1=47000.0, r2=10000.0, r_e_dc=1000.0, r_c=3300.0, r_load_ac=10000.0, beta=150.0, topology="CB")
        self.assertEqual(cb.region, "ACTIVE")
        self.assertGreater(cb.voltage_gain_loaded, 50.0)
        self.assertLess(cb.z_in_stage, 50.0)  # Low Zin characteristic of CB

    def test_common_emitter_layout_and_simulation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "ce.cir")
            asc_path = os.path.join(tmpdir, "ce.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_COMMON_EMITTER)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"CE DRC issues: {drc}")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"CE netlist verification failed: {ver}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"CE simulation failed: {sim.diagnostics}")
            gain = sim.parsed_log.get_scalar("gain_vv")
            self.assertIsNotNone(gain)
            self.assertGreater(gain, 20.0, f"Expected CE voltage gain > 20, got {gain}")

    def test_common_collector_layout_and_simulation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "cc.cir")
            asc_path = os.path.join(tmpdir, "cc.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_COMMON_COLLECTOR)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"CC DRC issues: {drc}")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"CC netlist verification failed: {ver}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"CC simulation failed: {sim.diagnostics}")
            gain = sim.parsed_log.get_scalar("gain_vv")
            self.assertIsNotNone(gain)
            # CC / Emitter Follower voltage gain is slightly less than unity (0.90 to 0.99)
            self.assertGreater(gain, 0.90, f"Expected CC gain > 0.90, got {gain}")
            self.assertLess(gain, 1.02, f"Expected CC gain near unity, got {gain}")

    def test_common_base_layout_and_simulation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "cb.cir")
            asc_path = os.path.join(tmpdir, "cb.asc")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(NETLIST_COMMON_BASE)

            sch = compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")
            drc = sch.run_geometric_drc()
            self.assertEqual(len(drc), 0, f"CB DRC issues: {drc}")

            ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
            self.assertTrue(ver.is_clean, f"CB netlist verification failed: {ver}")
            self.assertEqual(ver.unconnected_pins, [])

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded, f"CB simulation failed: {sim.diagnostics}")
            gain = sim.parsed_log.get_scalar("gain_vv")
            self.assertIsNotNone(gain)
            self.assertGreater(gain, 30.0, f"Expected CB voltage gain > 30, got {gain}")


if __name__ == "__main__":
    unittest.main()
