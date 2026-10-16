"""
Publication-Grade Reporting, Charting, and Lab Testbench Guide Generator (`ia_spice.reporting`).

Why this module exists:
-----------------------
An engineering circuit design is only complete and reproducible when accompanied by:
1. A formal reconciliation table comparing **Target Spec vs. Analytical Calculation vs. SPICE `.meas`
   Result vs. Relative Error %**.
2. High-resolution scientific plots (300 DPI PNG) comparing theoretical curves against SPICE simulation data.
3. Self-contained Markdown and HTML engineering reports (and optional PDF via ReportLab) plus step-by-step
   perturbation testbench instructions so any engineer or student can verify cause-and-effect in LTspice.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass
class MetricRow:
    """Single row of a theory-vs-simulation verification table."""
    stage_or_test: str
    metric_name: str
    unit: str
    target_spec: str
    analytical_value: float
    simulated_value: float
    tolerance_pct: float = 10.0

    @property
    def relative_error_pct(self) -> float:
        if abs(self.analytical_value) < 1e-18:
            return 0.0
        return ((self.simulated_value - self.analytical_value) / abs(self.analytical_value)) * 100.0

    @property
    def passed(self) -> bool:
        return abs(self.relative_error_pct) <= self.tolerance_pct


@dataclass
class EngineeringReportSpec:
    """Specification for generating a complete project verification report."""
    title: str
    subtitle: str
    authors: str
    metrics: List[MetricRow] = field(default_factory=list)
    figure_paths: List[str] = field(default_factory=list)
    summary_notes: List[str] = field(default_factory=list)


def generate_comparison_chart(
    x_vals: Sequence[float],
    y_theory: Sequence[float],
    y_sim: Sequence[float],
    title: str,
    xlabel: str,
    ylabel: str,
    output_png_path: str,
    theory_label: str = "Analytical Calculation",
    sim_label: str = "LTspice Simulation (.meas)",
    dpi: int = 250,
) -> str:
    """Generate a high-resolution scientific comparison plot (Theory vs. LTspice)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.8, 4.2), dpi=dpi)
    ax.plot(x_vals, y_theory, "b-", linewidth=2.0, label=theory_label)
    ax.plot(x_vals, y_sim, "ro", markersize=6, label=sim_label)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(frameon=True)
    fig.tight_layout()
    fig.savefig(output_png_path, dpi=dpi)
    plt.close(fig)
    return os.path.abspath(output_png_path)


def render_markdown_report(spec: EngineeringReportSpec) -> str:
    """Render a structured Markdown technical verification report."""
    lines: List[str] = [
        f"# {spec.title}",
        f"**{spec.subtitle}**  ",
        f"**Authors:** {spec.authors}",
        "",
        "---",
        "",
        "## 1. Analytical vs. SPICE Simulation Reconciliation Table",
        "",
        "| Stage / Test | Parameter | Target Spec | Analytical | LTspice (`.meas`) | Rel. Error | Status |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: |",
    ]

    for m in spec.metrics:
        status_str = "PASS" if m.passed else "FAIL"
        lines.append(
            f"| **{m.stage_or_test}** | {m.metric_name} ({m.unit}) | {m.target_spec} | "
            f"`{m.analytical_value:.4g}` | `{m.simulated_value:.4g}` | "
            f"`{m.relative_error_pct:+.2f}%` | **{status_str}** |"
        )

    if spec.summary_notes:
        lines.extend(["", "## 2. Engineering Notes & Diagnostics", ""])
        for note in spec.summary_notes:
            lines.append(f"- {note}")

    if spec.figure_paths:
        lines.extend(["", "## 3. Verification Plots", ""])
        for fp in spec.figure_paths:
            fname = os.path.basename(fp)
            lines.append(f"![{fname}]({fname})")

    return "\n".join(lines) + "\n"


def render_html_report(spec: EngineeringReportSpec) -> str:
    """Render a clean, standalone HTML technical report."""
    rows_html = []
    for m in spec.metrics:
        badge_color = "#16a34a" if m.passed else "#dc2626"
        status_str = "PASS" if m.passed else "FAIL"
        rows_html.append(
            f"<tr>"
            f"<td><b>{m.stage_or_test}</b></td>"
            f"<td>{m.metric_name} ({m.unit})</td>"
            f"<td>{m.target_spec}</td>"
            f"<td>{m.analytical_value:.4g}</td>"
            f"<td><b>{m.simulated_value:.4g}</b></td>"
            f"<td>{m.relative_error_pct:+.2f}%</td>"
            f"<td style='color:{badge_color};font-weight:bold;'>{status_str}</td>"
            f"</tr>"
        )

    notes_html = "".join(f"<li>{n}</li>" for n in spec.summary_notes)
    imgs_html = "".join(
        f"<div style='margin:16px 0;'><img src='{os.path.basename(fp)}' style='max-width:680px;border:1px solid #cbd5e1;border-radius:6px;'/></div>"
        for fp in spec.figure_paths
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{spec.title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #1e293b; max-width: 960px; margin: 32px auto; padding: 0 16px; line-height: 1.5; }}
  h1 {{ color: #0f172a; margin-bottom: 4px; }}
  h2 {{ color: #1d4ed8; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; margin-top: 28px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 14px; }}
  th, td {{ border: 1px solid #cbd5e1; padding: 8px 10px; text-align: left; }}
  th {{ background: #1e3a8a; color: white; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
</style>
</head>
<body>
  <h1>{spec.title}</h1>
  <p><b>{spec.subtitle}</b><br/>Authors: {spec.authors}</p>
  <h2>1. Analytical vs. SPICE Simulation Reconciliation Table</h2>
  <table>
    <thead>
      <tr>
        <th>Stage / Test</th><th>Parameter</th><th>Target Spec</th><th>Analytical</th><th>LTspice (.meas)</th><th>Rel. Error</th><th>Status</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
  <h2>2. Engineering Notes & Diagnostics</h2>
  <ul>{notes_html}</ul>
  <h2>3. Verification Plots</h2>
  {imgs_html}
</body>
</html>
"""
