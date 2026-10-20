"""
Unit and verification tests for Step 1 (`ia_spice.sizing`).

Runs with both standard `unittest` (`python -m unittest discover -s tests`) and `pytest`.

Validates:
1. Commercial EIA series snapping (E12, E24, E96) and SPICE notation conversion.
2. Exact analytical BJT DC/AC operating points against the 5 MHz FM Transmitter benchmark blocks:
   - Block 1: Audio Emitter Follower (Q4) -> VE4 ≈ 3.99 V, Av ≈ 0.9935 V/V
   - Block 4: Colpitts Oscillator DC bias (Q1) -> IE ≈ 1.626 mA, VCE ≈ 7.374 V, gm ≈ 61.9 mS
   - Block 5: RF Pre-Amplifier (Q2) -> IC2 ≈ 1.524 mA, VCE2 ≈ 4.631 V, Av2 ≈ -5.52 V/V
3. Varactor SMV1237-001LF C(V) analytical curve (0V to 6V) -> C(4V) = 20.69 pF.
4. Colpitts tank passive resonance & Barkhausen startup margin (margin ≈ 60.6x at DC).
5. Inter-stage cascade loading check (proving direct 50-Ohm load kills VCO, while C_EX = 2.2 pF draws only ~2.2% of max RF current).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.sizing import (
    StageContract,
    check_cascade_loading,
    evaluate_barkhausen_colpitts,
    evaluate_varactor_cv,
    format_spice_value,
    parse_spice_value,
    size_bjt_OperatingPoint,
    size_colpitts_tank,
    snap_to_series,
)


class TestStep1Sizing(unittest.TestCase):
    def test_snap_to_series_e12_e24_e96(self):
        val, err = snap_to_series(4700.0, "E24")
        self.assertAlmostEqual(val, 4700.0, places=3)
        self.assertAlmostEqual(err, 0.0, places=5)

        v_e12, _ = snap_to_series(10830.0, "E12")
        self.assertAlmostEqual(v_e12, 10000.0, places=1)

        v_e24, _ = snap_to_series(10830.0, "E24")
        self.assertAlmostEqual(v_e24, 11000.0, places=1)

        v_e96, err_e96 = snap_to_series(10830.0, "E96")
        self.assertAlmostEqual(v_e96, 10700.0, places=1)
        self.assertLess(abs(err_e96), 1.5)

    def test_spice_value_formatting_and_parsing(self):
        self.assertEqual(format_spice_value(4700), "4.7k")
        self.assertEqual(format_spice_value(5e6), "5Meg")
        self.assertEqual(format_spice_value(330e-12), "330p")
        self.assertEqual(format_spice_value(4.7e-6), "4.7u")

        self.assertAlmostEqual(parse_spice_value("4.7k"), 4700.0, places=3)
        self.assertAlmostEqual(parse_spice_value("5Meg"), 5e6, places=1)
        self.assertAlmostEqual(parse_spice_value("330p"), 330e-12, places=15)
        self.assertAlmostEqual(parse_spice_value("4.7u"), 4.7e-6, places=10)

    def test_bjt_operating_point_block1_audio_follower(self):
        res = size_bjt_OperatingPoint(
            v_cc=9.0,
            r1=43e3,
            r2=52e3,
            r_e_dc=3.9e3,
            topology="CC",
        )
        self.assertAlmostEqual(res.v_th, 4.9263, places=3)
        self.assertAlmostEqual(res.v_e, 3.986, places=2)
        self.assertAlmostEqual(res.voltage_gain_loaded, 0.9935, places=3)
        self.assertEqual(res.region, "ACTIVE")

    def test_bjt_operating_point_block4_vco_dc_bias(self):
        res = size_bjt_OperatingPoint(
            v_cc=9.0,
            r1=39e3,
            r2=15e3,
            r_e_dc=1e3,
            topology="CC",
        )
        self.assertAlmostEqual(res.v_th, 2.50, places=3)
        self.assertAlmostEqual(res.r_th, 10833.3, places=1)
        self.assertAlmostEqual(res.i_e * 1e3, 1.6257, places=3)
        self.assertAlmostEqual(res.v_ce, 7.374, places=2)
        self.assertAlmostEqual(res.g_m, 0.0619, places=3)
        self.assertEqual(res.region, "ACTIVE")

    def test_bjt_operating_point_block5_rf_preamp(self):
        res = size_bjt_OperatingPoint(
            v_cc=9.0,
            r1=39e3,
            r2=10e3,
            r_e_dc=660.0,
            r_c=2200.0,
            r_e_rf_unbypassed=100.0,
            r_load_ac=916.0,
            topology="CE",
        )
        self.assertAlmostEqual(res.i_c * 1e3, 1.524, places=2)
        self.assertAlmostEqual(res.v_ce, 4.631, places=2)
        self.assertAlmostEqual(res.voltage_gain_loaded, -5.52, places=1)
        self.assertEqual(res.region, "ACTIVE")

    def test_varactor_cv_curve_smv1237(self):
        self.assertAlmostEqual(evaluate_varactor_cv(0.0) * 1e12, 70.30, delta=0.05)
        self.assertAlmostEqual(evaluate_varactor_cv(2.0) * 1e12, 34.48, delta=0.05)
        self.assertAlmostEqual(evaluate_varactor_cv(4.0) * 1e12, 20.69, delta=0.05)
        self.assertAlmostEqual(evaluate_varactor_cv(6.0) * 1e12, 14.79, delta=0.05)

    def test_colpitts_tank_and_barkhausen(self):
        c_var_4v = evaluate_varactor_cv(4.0)
        tank = size_colpitts_tank(
            l1=4.7e-6,
            c1=330e-12,
            c2=470e-12,
            c_var=c_var_4v,
            c_cv=100e-12,
        )
        self.assertAlmostEqual(tank.c_12 * 1e12, 193.875, places=2)
        self.assertAlmostEqual(tank.c_eq * 1e12, 211.02, places=1)
        self.assertAlmostEqual(tank.f_lc_hz * 1e-6, 5.054, places=2)

        bk = evaluate_barkhausen_colpitts(
            l1=4.7e-6,
            r_ser_inductor=5.4,
            c1=330e-12,
            c2=470e-12,
            g_m=0.0619,
            f_osc_hz=5.0e6,
        )
        self.assertAlmostEqual(bk.r_p_tank, 4037.0, delta=10.0)
        self.assertAlmostEqual(bk.g_m_min * 1e3, 1.02, delta=0.02)
        self.assertAlmostEqual(bk.loop_gain_margin, 60.6, delta=0.5)
        self.assertLess(bk.r_negative_ohms, -300.0)
        self.assertTrue(bk.will_start)

    def test_cascade_loading_oscillator_isolation(self):
        vco_driver_direct = StageContract(
            name="Colpitts_VCO_Q1",
            z_in_ohms=4000.0,
            z_out_ohms=20.0,
            unloaded_gain_vv=1.0,
            max_output_current_peak_a=10.5e-3,
            coupling_reactance_ohms=0.0,
        )
        antenna_50ohm = StageContract(
            name="Antenna_50_Ohm",
            z_in_ohms=50.0,
            z_out_ohms=50.0,
            unloaded_gain_vv=1.0,
        )
        direct_report = check_cascade_loading(vco_driver_direct, antenna_50ohm, signal_peak_v=3.69)
        self.assertFalse(direct_report.passed)
        self.assertGreater(direct_report.current_utilization_pct, 400.0)

        vco_driver_weak = StageContract(
            name="Colpitts_VCO_Q1_WeakCoupled",
            z_in_ohms=4000.0,
            z_out_ohms=20.0,
            unloaded_gain_vv=1.0,
            max_output_current_peak_a=10.5e-3,
            coupling_reactance_ohms=14470.0,
        )
        preamp_q2 = StageContract(
            name="PreAmp_Q2",
            z_in_ohms=1220.0,
            z_out_ohms=2200.0,
            unloaded_gain_vv=4.9,
        )
        isolated_report = check_cascade_loading(vco_driver_weak, preamp_q2, signal_peak_v=3.69)
        self.assertTrue(isolated_report.passed)
        self.assertLess(isolated_report.current_utilization_pct, 3.0)

    def test_multi_domain_sizing(self):
        from ia_spice.sizing import (
            size_l_matching_network,
            size_mosfet_stage,
            size_opamp_stage,
            size_passive_filter,
            size_sallen_key_filter,
            size_voltage_divider,
        )

        # 1. Voltage divider
        vdiv = size_voltage_divider(v_in=12.0, v_out_target=3.3, r_total_target=20e3, series="E24")
        self.assertLess(abs(vdiv.error_pct), 1.5)

        # 2. Op-Amp stage (Non-inverting 10x and Inverting -4.7x)
        op_ni = size_opamp_stage(target_gain_vv=10.0, topology="non_inverting", r_input_target=1000.0)
        self.assertAlmostEqual(op_ni.actual_gain_vv, 10.1, delta=0.2)
        op_inv = size_opamp_stage(target_gain_vv=-4.7, topology="inverting", r_input_target=10e3)
        self.assertAlmostEqual(op_inv.actual_gain_vv, -4.7, places=2)

        # 3. MOSFET Common-Source stage
        mos = size_mosfet_stage(v_dd=12.0, r_g1=82e3, r_g2=39e3, r_d=2.2e3, r_s=1.0e3, k_p=4e-3, v_th=1.5)
        self.assertEqual(mos.region, "SATURATION")
        self.assertGreater(mos.g_m, 1e-3)

        # 4. Passive & Active Sallen-Key Filters
        f_rc = size_passive_filter(f_cutoff_hz=15.9e3, filter_type="rc_lowpass", r_characteristic=1000.0)
        self.assertLess(abs(f_rc.freq_error_pct), 2.0)
        f_sk = size_sallen_key_filter(f_cutoff_hz=10e3, q_factor=0.7071, c_base=10e-9)
        self.assertLess(abs(f_sk.freq_error_pct), 3.0)

        # 5. RF L-Section Matching Network (50 Ohm to 500 Ohm at 10 MHz)
        lmatch = size_l_matching_network(r_source=50.0, r_load=500.0, f_0_hz=10e6)
        self.assertAlmostEqual(lmatch.q_network, 3.0, places=2)
        self.assertGreater(lmatch.l_henries, 0.0)
        self.assertGreater(lmatch.c_farads, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
