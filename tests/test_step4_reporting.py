"""
Unit tests for Step 4 (`ia_spice.reporting`).

Validates:
1. `MetricRow` relative error % and pass/fail tolerance logic.
2. High-resolution comparison chart generation (`generate_comparison_chart`) producing a valid PNG file.
3. Markdown and HTML engineering report rendering (`render_markdown_report`, `render_html_report`).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ia_spice.reporting import (
    EngineeringReportSpec,
    MetricRow,
    generate_comparison_chart,
    render_html_report,
    render_markdown_report,
)


class TestStep4Reporting(unittest.TestCase):
    def test_metric_row_error_and_pass_fail(self):
        row_ok = MetricRow(
            stage_or_test="VCO Core (S2/S3)",
            metric_name="Carrier Frequency f_0",
            unit="MHz",
            target_spec="5.000 MHz",
            analytical_value=5.000,
            simulated_value=4.99574,
            tolerance_pct=1.0,
        )
        self.assertAlmostEqual(row_ok.relative_error_pct, -0.0852, places=3)
        self.assertTrue(row_ok.passed)

        row_fail = MetricRow(
            stage_or_test="RF Output",
            metric_name="Output Power P_out",
            unit="mW",
            target_spec="10.0 mW",
            analytical_value=10.0,
            simulated_value=5.0,
            tolerance_pct=10.0,
        )
        self.assertAlmostEqual(row_fail.relative_error_pct, -50.0, places=2)
        self.assertFalse(row_fail.passed)

    def test_chart_and_report_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            png_path = os.path.join(tmpdir, "varactor_cv.png")
            out_png = generate_comparison_chart(
                x_vals=[0, 2, 4, 6],
                y_theory=[70.30, 34.48, 20.69, 14.79],
                y_sim=[70.30, 34.48, 20.69, 14.78],
                title="Varactor SMV1237 C(V) Verification",
                xlabel="Reverse Voltage V_R (V)",
                ylabel="Capacitance C_var (pF)",
                output_png_path=png_path,
                dpi=150,
            )
            self.assertTrue(os.path.isfile(out_png))
            self.assertGreater(os.path.getsize(out_png), 1000)

            spec = EngineeringReportSpec(
                title="5 MHz FM Transmitter Verification Report",
                subtitle="Closed-Loop IA-SPICE Validation",
                authors="Caio Carlos Missfeld",
                metrics=[
                    MetricRow("S1", "Varactor C(4V)", "pF", "20.69 pF", 20.69, 20.69, tolerance_pct=2.0),
                    MetricRow("S2/S3", "Oscillator f_osc", "MHz", "5.00 MHz", 5.000, 4.9957, tolerance_pct=1.0),
                    MetricRow("S6", "RF Power P_out", "mW", "10.0 mW", 10.00, 9.823, tolerance_pct=5.0),
                ],
                figure_paths=[out_png],
                summary_notes=[
                    "All 6 functional stages verified individually before full integration.",
                    "Zero unconnected pins (NC_ = 0) across all generated .asc schematics.",
                ],
            )

            md = render_markdown_report(spec)
            self.assertIn("# 5 MHz FM Transmitter Verification Report", md)
            self.assertIn("Varactor C(4V)", md)
            self.assertIn("PASS", md)

            html = render_html_report(spec)
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("9.823", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
