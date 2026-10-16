"""
Unit and live LTspice integration tests for Step 2 (`ia_spice.meas_parser` and `ia_spice.simulator`).

Validates:
1. Parsing scalar transient logs (`S2_S3_vco_nucleo.log` -> fosc = 4.99574 MHz, vpp = 17.1764 V).
2. Parsing stepped transient tables (`S4_curva_fosc_vctrl.log` -> 9 steps of VC from 2V to 6V).
3. Parsing stepped AC `(dB, phase)` tables (`S1_varactor_validacao.log` -> exact conversion from -213.688 dB to 20.69 pF at 4V).
4. Live batch execution (`run_ltspice_batch`) of a temporary SPICE circuit in LTspice 24, verifying `.meas` extraction.
5. Automated physics diagnostics (`diagnose_simulation_results`) for failed oscillation and varactor forward-conduction clamping.
"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.meas_parser import parse_ltspice_log
from ia_spice.simulator import (
    diagnose_simulation_results,
    find_ltspice_executable,
    run_ltspice_batch,
)

VCO_PROJECT_DIR = r"C:\analogicos\vco_colpitts_fm"


class TestStep2SimulatorAndParser(unittest.TestCase):
    def test_parse_scalar_transient_log(self):
        log_path = os.path.join(VCO_PROJECT_DIR, "S2_S3_vco_nucleo.log")
        parsed = parse_ltspice_log(log_path)

        self.assertEqual(len(parsed.fatal_errors), 0)
        self.assertEqual(len(parsed.failed_measurements), 0)
        self.assertAlmostEqual(parsed.get_scalar("fosc") * 1e-6, 4.99574, places=4)
        self.assertAlmostEqual(parsed.get_scalar("vpp"), 17.1764, places=3)
        self.assertAlmostEqual(parsed.get_scalar("pvco") * 1e3, 47.2744, places=2)
        self.assertAlmostEqual(parsed.get_scalar("vr_min"), -0.758907, places=4)

    def test_parse_stepped_transient_log(self):
        log_path = os.path.join(VCO_PROJECT_DIR, "S4_curva_fosc_vctrl.log")
        parsed = parse_ltspice_log(log_path)

        self.assertEqual(parsed.step_variable, "vc")
        self.assertEqual(len(parsed.step_values), 9)
        self.assertAlmostEqual(parsed.step_values[0], 2.0)
        self.assertAlmostEqual(parsed.step_values[-1], 6.0)

        fosc_list = parsed.get_stepped_series("fosc")
        self.assertEqual(len(fosc_list), 9)
        self.assertAlmostEqual(fosc_list[0] * 1e-6, 4.9888, places=4)
        self.assertAlmostEqual(fosc_list[-1] * 1e-6, 5.04041, places=4)

        # Verify automatic varactor rectification warning when VR_MIN < -0.8V at VC=2.0V (-0.967V)
        diags = diagnose_simulation_results(parsed)
        self.assertTrue(any("VARACTOR_RECTIFICATION_WARNING" in d for d in diags))

    def test_parse_stepped_ac_db_log_and_capacitance_extraction(self):
        log_path = os.path.join(VCO_PROJECT_DIR, "S1_varactor_validacao.log")
        parsed = parse_ltspice_log(log_path)

        self.assertEqual(parsed.step_variable, "vrev")
        self.assertEqual(len(parsed.step_values), 17)

        # Step index 8 corresponds to VREV = 4.0 V
        # In S1_varactor_validacao.log, the expression is -im(i(vac)/v(ktest))/(2*pi*1e6),
        # so 10^(dB/20) is directly the capacitance in Farads!
        c_var_farads = parsed.get_stepped_series("c_var", linearize_db=True)
        c_4v_pf = c_var_farads[8] * 1e12
        self.assertAlmostEqual(c_4v_pf, 20.69, delta=0.02)

    def test_live_ltspice_batch_execution_rc_filter(self):
        exe = find_ltspice_executable()
        self.assertTrue(os.path.isfile(exe))

        netlist = """* RC Low-Pass Filter Test (fc = 1 / (2*pi*1k*159.15n) = 1.000 kHz)
V1 IN 0 AC 1
R1 IN OUT 1k
C1 OUT 0 159.1549n
.ac dec 100 10 100k
.meas ac GAIN_1K FIND mag(V(OUT)) AT 1k
.meas ac FC WHEN mag(V(OUT))=0.70710678
.end
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cir_path = os.path.join(tmpdir, "rc_filter_test.cir")
            with open(cir_path, "w", encoding="utf-8") as f:
                f.write(netlist)

            run_res = run_ltspice_batch(cir_path)
            self.assertTrue(run_res.succeeded, f"Diagnostics: {run_res.diagnostics}")

            # GAIN_1K is ~ -3.0103 dB -> linear magnitude ~ 0.7071
            gain_1k = run_res.parsed_log.get_scalar("gain_1k", linearize_db=True)
            self.assertIsNotNone(gain_1k)
            self.assertAlmostEqual(gain_1k, 0.7071, delta=0.005)

    def test_physics_diagnostics_on_dead_oscillator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_log = os.path.join(tmpdir, "dead_osc.log")
            with open(fake_log, "w", encoding="latin-1") as f:
                f.write('Circuit: * Dead Oscillator\nMeasurement "t100" FAIL\'ed\nMeasurement "fosc" FAIL\'ed\n')

            parsed = parse_ltspice_log(fake_log)
            diags = diagnose_simulation_results(parsed)
            self.assertIn("t100", parsed.failed_measurements)
            self.assertTrue(any("OSCILLATION_FAILURE" in d for d in diags))


if __name__ == "__main__":
    unittest.main(verbosity=2)
