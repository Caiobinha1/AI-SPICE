"""
Unit and live LTspice integration tests for Step 8 (`ia_spice.ltspice_project` and engine-level `sanitize_visual_layout`):
1. Engine-level automatic prevention of overlapping `.tran`/`.meas` directives and vertical colliding flags.
2. Automatic compilation of any SPICE netlist into 3 distinct `.asc` layout styles (`A_textbook_wired`,
   `B_power_rail_bus`, `C_modular_blocks`) with zero manual coordinates, `NC_ = 0`, and identical LTspice results.
3. Universal LTspice project editing across formats (`.asc`, `.cir`, `.net`, `.sub`, `.asy`, `.plt`, `.log`):
   reading existing `.asc`, modifying values (`modify_asc_component`), adding new components (`add_component_to_asc`),
   beautifying legacy schematics (`beautify_asc_file`), and inspecting project directories (`inspect_ltspice_project`).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples")))

from demo4_layout_styles_and_project_editor import run_demo4
from ia_spice.asc_compiler import SchematicBuilder
from ia_spice.ltspice_project import beautify_asc_file, parse_asc_file


class TestStep8ProjectEditorAndAutoLayouts(unittest.TestCase):
    def test_engine_level_auto_sanitization_of_flags_and_directives(self):
        """
        Even if a caller places flags directly on vertical pins (stub_dx=0, stub_dy=0) and
        forces a directive at y=200 right on top of components, `SchematicBuilder.save()`
        automatically converts vertical pin flags to horizontal tabs and pushes directives
        to the footer >=160px below all circuit elements!
        """
        sch = SchematicBuilder(width=800, height=600)
        r1 = sch.add_component("R1", "res", x=160, y=192, value="1k")
        # Intentionally pass zero stubs and an overlapping directive Y=208:
        sch.add_flag(r1.pin("top"), "VCC_TOP")
        sch.ground_pin(r1.pin("bot"), stub_dy=0)
        sch.add_directive(".tran 0 1m 0 1u", x=64, y=208)

        with tempfile.TemporaryDirectory() as tmpdir:
            asc_path = os.path.join(tmpdir, "sanitized.asc")
            sch.save(asc_path)

            loaded = parse_asc_file(asc_path)
            # 1. VCC_TOP flag must now sit at the end of a horizontal wire segment (dx=+32, dy=-32)
            vcc_flags = [fl for fl in loaded.flags if fl.net_name == "VCC_TOP"]
            self.assertEqual(len(vcc_flags), 1)
            self.assertEqual((vcc_flags[0].x, vcc_flags[0].y), (176 + 32, 208 - 32))

            # 2. Ground flag on bottom pin must have been pushed 32px down to (176, 288 + 32)
            gnd_flags = [fl for fl in loaded.flags if fl.net_name == "0"]
            self.assertEqual(len(gnd_flags), 1)
            self.assertEqual((gnd_flags[0].x, gnd_flags[0].y), (176, 288 + 32))

            # 3. Directive at y=208 must have been automatically relocated to >= max_y + 160 (320 + 160 = 480)
            self.assertGreaterEqual(loaded.directives[0].y, 480)

    def test_beautify_legacy_asc_file(self):
        raw_messy_asc = (
            "Version 4\n"
            "SHEET 1 800 600\n"
            "FLAG 176 208 VCC\n"
            "FLAG 176 288 0\n"
            "SYMBOL res 160 192 R0\n"
            "SYMATTR InstName R1\n"
            "SYMATTR Value 10k\n"
            "TEXT 64 220 Left 2 !.tran 1m\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            messy_path = os.path.join(tmpdir, "messy.asc")
            clean_path = os.path.join(tmpdir, "clean.asc")
            with open(messy_path, "w", encoding="utf-8") as f:
                f.write(raw_messy_asc)

            sch, fixes = beautify_asc_file(messy_path, output_path=clean_path)
            self.assertGreaterEqual(len(fixes), 2)
            loaded = parse_asc_file(clean_path)
            self.assertGreaterEqual(loaded.directives[0].y, 480)

    def test_demo4_three_layout_styles_and_full_project_editing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = run_demo4(tmpdir)
            for style_id in ("A_textbook_wired", "B_power_rail_bus", "C_modular_blocks"):
                st = res["styles"][style_id]
                self.assertTrue(st["is_clean"])
                self.assertEqual(st["drc_issues"], 0)
                self.assertAlmostEqual(st["av_sim"], 6.87, delta=0.2)

            # Raising RLOAD from 100 Ohm to 1k Ohm increases emitter-follower gain -> total gain rises from 6.87 to ~7.58 V/V
            self.assertGreater(res["modified_av_sim"], 7.2)
            self.assertEqual(res["plt_traces"], ["V(IN)", "V(C1)", "V(OUT)"])
            self.assertIn(".asc", res["inspected_formats"])
            self.assertIn(".cir", res["inspected_formats"])
            self.assertIn(".plt", res["inspected_formats"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
