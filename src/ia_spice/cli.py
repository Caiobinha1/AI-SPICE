"""
Command-Line Interface (`ia-spice`) for the IA-SPICE Framework (`ia_spice.cli`).

Subcommands:
- `ia-spice doctor`                  : Check LTspice installation and Python environment.
- `ia-spice snap <val> [--series]`   : Snap an ideal value to E12/E24/E96 commercial series.
- `ia-spice sim <file>`              : Run `.cir` or `.asc` in batch mode, print `.meas` & physics diagnostics.
- `ia-spice verify-asc <asc> [--cir]`: Compile `.asc`, verify `NC_ = 0`, and check netlist isomorphism.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .netlist_verifier import verify_asc_netlist
from .simulator import find_ltspice_executable, run_ltspice_batch
from .sizing import format_spice_value, parse_spice_value, snap_to_series


def cmd_doctor(_args: argparse.Namespace) -> int:
    try:
        exe = find_ltspice_executable()
        print(f"[OK] LTspice executable found: {exe}")
        return 0
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


def cmd_snap(args: argparse.Namespace) -> int:
    raw_val = parse_spice_value(args.value)
    snapped, err_pct = snap_to_series(raw_val, args.series)
    out = {
        "input": args.value,
        "input_float": raw_val,
        "series": args.series.upper(),
        "snapped_float": snapped,
        "snapped_spice": format_spice_value(snapped),
        "relative_error_pct": round(err_pct, 4),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_sim(args: argparse.Namespace) -> int:
    res = run_ltspice_batch(args.circuit, timeout_sec=args.timeout)
    payload = {
        "circuit_path": res.circuit_path,
        "log_path": res.log_path,
        "succeeded": res.succeeded,
        "scalars": {
            k: res.parsed_log.get_scalar(k, linearize_db=True)
            for k in res.parsed_log.scalars
        },
        "step_variable": res.parsed_log.step_variable,
        "step_values": res.parsed_log.step_values,
        "stepped": {
            k: res.parsed_log.get_stepped_series(k, linearize_db=True)
            for k in res.parsed_log.stepped
        },
        "failed_measurements": res.parsed_log.failed_measurements,
        "diagnostics": res.diagnostics,
    }
    print(json.dumps(payload, indent=2))
    return 0 if res.succeeded else 1


def cmd_verify_asc(args: argparse.Namespace) -> int:
    rep = verify_asc_netlist(args.asc, reference_cir_path=args.cir)
    payload = {
        "asc_path": rep.asc_path,
        "net_path": rep.net_path,
        "is_clean": rep.is_clean,
        "unconnected_pins": rep.unconnected_pins,
        "mismatches": rep.mismatches,
        "missing_components": rep.missing_components,
        "component_nodes": rep.component_nodes,
    }
    print(json.dumps(payload, indent=2))
    return 0 if rep.is_clean else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ia-spice",
        description="IA-SPICE: Equation-Grounded AI Analog & RF Circuit Engineering Toolkit",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Check LTspice installation and environment")
    p_doc.set_defaults(func=cmd_doctor)

    p_snap = sub.add_parser("snap", help="Snap a component value to E12/E24/E96 standard series")
    p_snap.add_argument("value", help="Target value (e.g. 10.83k, 211p, 4.7u)")
    p_snap.add_argument("--series", default="E24", choices=["E12", "E24", "E96"])
    p_snap.set_defaults(func=cmd_snap)

    p_sim = sub.add_parser("sim", help="Run a .cir or .asc file in LTspice batch mode and parse .meas")
    p_sim.add_argument("circuit", help="Path to .cir or .asc file")
    p_sim.add_argument("--timeout", type=int, default=180, help="Timeout in seconds")
    p_sim.set_defaults(func=cmd_sim)

    p_ver = sub.add_parser("verify-asc", help="Compile .asc schematic and verify zero NC_ pins & netlist match")
    p_ver.add_argument("asc", help="Path to .asc schematic file")
    p_ver.add_argument("--cir", default=None, help="Optional reference .cir netlist to compare against")
    p_ver.set_defaults(func=cmd_verify_asc)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
