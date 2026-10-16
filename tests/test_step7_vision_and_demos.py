"""
Unit and live LTspice integration tests for Step 7:
- `examples/demo1_rlc_bandpass.py` (Multi-feature RLC Band-Pass synthesis, .asc verification & .ac simulation)
- `examples/demo2_cascaded_bjt_amp.py` (2-stage BJT amplifier, cascade loading contract, .asc & .tran simulation)
- `examples/demo3_vision_reconstruction.py` (`vision_to_spice`: missing-parameter detection in `ask_user` mode,
  partial user overrides, and autonomous sizing + `.asc` reconstruction + live LTspice simulation).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples")))

from demo1_rlc_bandpass import run_demo1
from demo2_cascaded_bjt_amp import run_demo2
from demo3_vision_reconstruction import build_incomplete_image_graph, run_demo3
from ia_spice.vision_to_spice import resolve_and_compile_vision_graph


class TestStep7VisionAndMultiFeatureDemos(unittest.TestCase):
    def test_demo1_rlc_bandpass_multi_feature(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = run_demo1(tmpdir)
            self.assertTrue(res["is_clean"])
            self.assertAlmostEqual(res["f0_sim_hz"] * 1e-6, res["f0_analytical_hz"] * 1e-6, delta=0.02)
            self.assertAlmostEqual(res["vmax_sim"], res["vmax_analytical"], delta=0.01)
            self.assertLess(res["atten_100k"], 0.15)
            self.assertTrue(os.path.isfile(res["report_md"]))

    def test_demo2_cascaded_bjt_amp_multi_feature(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = run_demo2(tmpdir)
            self.assertTrue(res["is_clean"])
            # Direct 100-Ohm load causes >95% voltage gain collapse on Q1
            self.assertGreater(res["direct_100ohm_gain_drop_pct"], 90.0)
            # With Q2 Emitter-Follower buffer, gain is preserved (~2.0 to 3.5 V/V)
            self.assertGreater(res["simulated_gain_vv"], 2.0)
            self.assertAlmostEqual(res["simulated_gain_vv"], res["analytical_gain_vv"], delta=0.5)

    def test_demo3_vision_to_spice_ask_user_and_autonomous_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = run_demo3(tmpdir)
            # Should detect 9 missing parameters (VIN, CIN, R1, R2, RC, RE, Q1, COUT + VCC supply rail)
            self.assertEqual(res["missing_count_detected"], 9)
            self.assertIn("Parâmetros Não Identificados na Imagem", res["user_prompt_markdown"])
            self.assertIn("Como deseja prosseguir?", res["user_prompt_markdown"])

            # In autonomous mode, it must size all 9 items, compile a clean .asc (NC_=0), and amplify in LTspice!
            self.assertTrue(res["is_clean"])
            self.assertTrue(2.5 <= res["collector_dc_v"] <= 7.5)
            self.assertGreater(res["simulated_gain_vv"], 1.8)

    def test_vision_to_spice_partial_user_overrides(self):
        graph = build_incomplete_image_graph()
        # User provides exact values for R1 and R2, then asks to autonomously size the rest
        audit, cir_text, sch = resolve_and_compile_vision_graph(
            graph,
            mode="autonomous",
            user_overrides={"R1": "39k", "R2": "10k", "VCC": "12"},
        )
        self.assertIsNotNone(cir_text)
        self.assertIn("R1 VCC B 39k", cir_text)
        self.assertIn("R2 B 0 10k", cir_text)
        self.assertIn("V_VCC VCC 0 12", cir_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
