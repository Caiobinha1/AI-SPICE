"""
End-to-End 4-Level Benchmark Suite (`tests/test_step6_benchmarks.py`).

Validates the IA-SPICE methodology across 4 progressive engineering complexity levels:
- Level 1 (Basic Passive)      : RLC Band-Pass / RC Low-Pass Filter (E24 sizing + `.ac` + `.asc` verification)
- Level 2 (Intermediate Active): Degenerated Common-Emitter BJT RF Pre-Amplifier + 50-Ohm Emitter Follower Buffer
- Level 3 (Autonomous Nonlin.) : 5 MHz Colpitts LC Oscillator (Barkhausen startup + steady-state `.meas` + `.asc`)
- Level 4 (Full Multi-Stage RF): 6-Block Integrated 5 MHz FM Transmitter (`S6_modulacao_fm_completa.asc` + report)
"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.asc_compiler import SchematicBuilder
from ia_spice.netlist_verifier import verify_asc_netlist
from ia_spice.reporting import EngineeringReportSpec, MetricRow, render_markdown_report
from ia_spice.simulator import run_ltspice_batch
from ia_spice.sizing import (
    evaluate_barkhausen_colpitts,
    evaluate_varactor_cv,
    format_spice_value,
    size_bjt_OperatingPoint,
    size_colpitts_tank,
    snap_to_series,
)

VCO_LOG_DIR = r"C:\analogicos\vco_colpitts_fm"
ESQ_DIR = r"C:\analogicos\Esquematicos"


class TestStep6EndToEndBenchmarks(unittest.TestCase):
    def test_level1_e24_rc_lowpass_filter_end_to_end(self):
        # Target: fc = 159.15 kHz with R = 1 kOhm, C = 1 nF (exact Block 2 RF filter from FM Transmitter)
        r_val, _ = snap_to_series(1000.0, "E24")
        c_val, _ = snap_to_series(1e-9, "E24")
        fc_analytical = 1.0 / (2.0 * math.pi * r_val * c_val)  # 159,154.9 Hz

        sch = SchematicBuilder(width=640, height=480)
        vin = sch.add_component("VIN", "voltage", x=64, y=160, value="AC 1")
        r1 = sch.add_component("RFILT", "res", x=224, y=160, value=format_spice_value(r_val), orientation="R90")
        c1 = sch.add_component("CFILT", "cap", x=288, y=224, value=format_spice_value(c_val), orientation="R0")

        sch.add_flag(vin.pin("top"), "IN")
        sch.ground_pin(vin.pin("bot"))
        sch.connect_points(vin.pin("top"), r1.pin("left"))
        sch.add_flag(r1.pin("right"), "OUT")
        sch.connect_points(r1.pin("right"), c1.pin("top"))
        sch.ground_pin(c1.pin("bot"))

        sch.add_directive(".ac dec 200 1k 10Meg")
        sch.add_directive(".meas ac GAIN_3K FIND mag(V(OUT)) AT 3k")
        sch.add_directive(".meas ac GAIN_5MEG FIND mag(V(OUT)) AT 5Meg")

        with tempfile.TemporaryDirectory() as tmpdir:
            asc_path = os.path.join(tmpdir, "level1_filter.asc")
            sch.save(asc_path)

            ver = verify_asc_netlist(
                asc_path,
                expected_connections={
                    "VIN": ["IN", "0"],
                    "RFILT": ["IN", "OUT"],
                    "CFILT": ["OUT", "0"],
                },
            )
            self.assertTrue(ver.is_clean)

            sim = run_ltspice_batch(asc_path)
            self.assertTrue(sim.succeeded)

            # Audio at 3 kHz passes unattenuated (~0.9998 V/V), RF at 5 MHz is attenuated ~31.4x (~0.0318 V/V)
            gain_3k = sim.parsed_log.get_scalar("gain_3k", linearize_db=True)
            gain_5meg = sim.parsed_log.get_scalar("gain_5meg", linearize_db=True)
            self.assertAlmostEqual(gain_3k, 0.9998, delta=0.005)
            self.assertAlmostEqual(gain_5meg, fc_analytical / 5e6, delta=0.005)

    def test_level2_bjt_rf_amplifier_and_50ohm_buffer_verification(self):
        # Verify analytical sizing + LTspice schematic + simulation of Block 5+6 (Q2 CE + Q3 CC -> 10.0 mW in 50 Ohm)
        asc_path = os.path.join(ESQ_DIR, "TESTE_blocos5_6_amplificador.asc")
        self.assertTrue(os.path.isfile(asc_path))

        ver = verify_asc_netlist(asc_path)
        self.assertTrue(ver.is_clean)

        from ia_spice.meas_parser import parse_ltspice_log
        log_path = os.path.join(VCO_LOG_DIR, "TESTE_blocos5_6_amplificador.log")
        parsed = parse_ltspice_log(log_path)
        av2_series = parsed.get_stepped_series("av2")
        pout_series = parsed.get_stepped_series("pout")
        self.assertAlmostEqual(av2_series[0], 4.90, delta=0.15)
        self.assertAlmostEqual(pout_series[-1] * 1e3, 10.00, delta=0.15)

    def test_level3_autonomous_colpitts_vco_core_verification(self):
        # Verify analytical Barkhausen + schematic netlist isomorphism + simulation of 5 MHz Colpitts VCO Core
        c_var_4v = evaluate_varactor_cv(4.0)
        tank = size_colpitts_tank(4.7e-6, 330e-12, 470e-12, c_var=c_var_4v, c_cv=47e-12)
        op = size_bjt_OperatingPoint(9.0, 39e3, 15e3, 1e3, topology="CC")
        bk = evaluate_barkhausen_colpitts(4.7e-6, 5.4, 330e-12, 470e-12, op.g_m, tank.f_lc_hz)
        self.assertTrue(bk.will_start)

        asc_path = os.path.join(ESQ_DIR, "S2_S3_vco_nucleo.asc")
        cir_path = os.path.join(VCO_LOG_DIR, "S2_S3_vco_nucleo.cir")
        ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
        self.assertTrue(
            ver.is_clean,
            f"Mismatches: {ver.mismatches}, NC: {ver.unconnected_pins}, Missing: {ver.missing_components}",
        )

    def test_level4_full_6block_fm_transmitter_system_and_report(self):
        # Verify full 6-block FM transmitter schematic (`S6_modulacao_fm_completa.asc`) and generate report
        asc_path = os.path.join(ESQ_DIR, "S6_modulacao_fm_completa.asc")
        cir_path = os.path.join(VCO_LOG_DIR, "S6_modulacao_fm_completa.cir")
        ver = verify_asc_netlist(asc_path, reference_cir_path=cir_path)
        self.assertTrue(
            ver.is_clean,
            f"Mismatches: {ver.mismatches}, NC: {ver.unconnected_pins}, Missing: {ver.missing_components}",
        )

        from ia_spice.meas_parser import parse_ltspice_log
        parsed = parse_ltspice_log(os.path.join(VCO_LOG_DIR, "S6_modulacao_fm_completa.log"))
        fosc_mhz = parsed.get_scalar("fosc") * 1e-6
        pout_mw = parsed.get_scalar("pout") * 1e3
        ptot_mw = parsed.get_scalar("ptotal") * 1e3

        spec = EngineeringReportSpec(
            title="5 MHz Colpitts VCO FM Transmitter — Full System Benchmark",
            subtitle="Level 4 Multi-Stage Verification",
            authors="Caio Carlos Missfeld & Felipe Braun",
            metrics=[
                MetricRow("S6 Full TX", "Carrier Frequency f_0", "MHz", "5.00 MHz", 5.000, fosc_mhz, tolerance_pct=1.0),
                MetricRow("S6 Full TX", "Antenna Output Power P_out", "mW", "10.0 mW", 10.00, pout_mw, tolerance_pct=5.0),
                MetricRow("S6 Full TX", "Total DC Power P_total", "mW", "< 500 mW", 390.0, ptot_mw, tolerance_pct=10.0),
            ],
        )
        self.assertTrue(all(m.passed for m in spec.metrics))
        md = render_markdown_report(spec)
        self.assertIn("PASS", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
