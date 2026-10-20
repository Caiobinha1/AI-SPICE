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


def cmd_import_vision(args: argparse.Namespace) -> int:
    import dataclasses
    from .vision_to_spice import (
        ExtractedComponent,
        VisionCircuitGraph,
        resolve_and_compile_vision_graph,
    )

    with open(args.json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    comps = [
        ExtractedComponent(
            inst_name=c["inst_name"],
            comp_type=c["comp_type"],
            nodes=list(c["nodes"]),
            value=c.get("value"),
            role_hint=c.get("role_hint"),
        )
        for c in data["components"]
    ]
    graph = VisionCircuitGraph(
        title=data.get("title", "Vision Imported Circuit"),
        components=comps,
        design_targets=dict(data.get("design_targets") or {}),
    )
    audit, cir_text, sch = resolve_and_compile_vision_graph(graph, mode=args.mode)
    if args.out_cir and cir_text:
        with open(args.out_cir, "w", encoding="utf-8") as f:
            f.write(cir_text)
    if args.out_asc and sch:
        sch.save(args.out_asc)

    out = {
        "is_complete": audit.is_complete,
        "mode": args.mode,
        "missing_items": [dataclasses.asdict(m) for m in audit.missing_items],
        "topology_warnings": audit.topology_warnings,
        "user_prompt_markdown": audit.format_user_prompt(),
        "compiled_cir": cir_text,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    import dataclasses
    from .ltspice_project import inspect_ltspice_project

    rep = inspect_ltspice_project(args.path)
    print(json.dumps(dataclasses.asdict(rep), indent=2, ensure_ascii=False))
    return 0


def cmd_beautify_asc(args: argparse.Namespace) -> int:
    from .ltspice_project import beautify_asc_file

    _, fixes = beautify_asc_file(args.asc, output_path=args.out)
    print(json.dumps({"asc": args.out or args.asc, "fixes_applied": fixes}, indent=2, ensure_ascii=False))
    return 0


def cmd_netlist_to_asc(args: argparse.Namespace) -> int:
    from .ltspice_project import compile_netlist_to_asc

    compile_netlist_to_asc(args.netlist, args.out, style=args.style)
    print(json.dumps({"netlist": args.netlist, "output_asc": args.out, "style": args.style}, indent=2))
    return 0


def cmd_size_block(args: argparse.Namespace) -> int:
    import dataclasses
    from .sizing import (
        size_l_matching_network,
        size_mosfet_stage,
        size_opamp_stage,
        size_passive_filter,
        size_sallen_key_filter,
        size_voltage_divider,
    )

    btype = args.block_type.lower()
    if btype == "opamp":
        res = size_opamp_stage(
            target_gain_vv=float(args.gain),
            topology=args.topology or "non_inverting",
            r_input_target=parse_spice_value(args.rin) if args.rin else 10e3,
            gbw_hz=parse_spice_value(args.gbw) if args.gbw else 10e6,
            series=args.series or "E24",
        )
    elif btype == "divider":
        res = size_voltage_divider(
            v_in=float(args.vin),
            v_out_target=float(args.vout),
            r_total_target=parse_spice_value(args.rtotal) if args.rtotal else 20e3,
            series=args.series or "E24",
        )
    elif btype == "filter":
        res = size_passive_filter(
            f_cutoff_hz=parse_spice_value(args.fc),
            filter_type=args.filter_type or "rc_lowpass",
            r_characteristic=parse_spice_value(args.r) if args.r else 1000.0,
            q_factor=float(args.q) if args.q else 5.0,
            series=args.series or "E24",
        )
    elif btype in ("sallen-key", "sallen_key"):
        res = size_sallen_key_filter(
            f_cutoff_hz=parse_spice_value(args.fc),
            q_factor=float(args.q) if args.q else 0.7071,
            c_base=parse_spice_value(args.c) if args.c else 10e-9,
            series=args.series or "E24",
        )
    elif btype == "mosfet":
        res = size_mosfet_stage(
            v_dd=float(args.vdd),
            r_g1=parse_spice_value(args.rg1),
            r_g2=parse_spice_value(args.rg2),
            r_d=parse_spice_value(args.rd),
            r_s=parse_spice_value(args.rs),
            k_p=parse_spice_value(args.kp) if args.kp else 2e-3,
            v_th=float(args.vth) if args.vth else 1.5,
            topology=args.topology or "CS",
        )
    elif btype in ("l-match", "l_match"):
        res = size_l_matching_network(
            r_source=float(args.rsource),
            r_load=float(args.rload),
            f_0_hz=parse_spice_value(args.f0),
            series=args.series or "E24",
        )
    else:
        print(f"Unknown block type: {btype}", file=sys.stderr)
        return 1

    print(json.dumps(dataclasses.asdict(res), indent=2))
    return 0


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

    p_size = sub.add_parser("size-block", help="Analytically size Op-Amp, Filter, Sallen-Key, MOSFET, L-Match, or Divider")
    p_size.add_argument("block_type", choices=["opamp", "divider", "filter", "sallen-key", "mosfet", "l-match"])
    p_size.add_argument("--gain", default=None, help="Target gain in V/V (for opamp)")
    p_size.add_argument("--topology", default=None, help="Topology (e.g. non_inverting, inverting, CS, CD)")
    p_size.add_argument("--rin", default=None, help="Input resistor target (e.g. 10k)")
    p_size.add_argument("--gbw", default=None, help="Op-amp gain-bandwidth product in Hz")
    p_size.add_argument("--vin", default=None, help="Input voltage for divider")
    p_size.add_argument("--vout", default=None, help="Target output voltage for divider")
    p_size.add_argument("--rtotal", default=None, help="Target total divider resistance")
    p_size.add_argument("--fc", default=None, help="Cutoff frequency in Hz (e.g. 10k, 1meg)")
    p_size.add_argument("--filter-type", default="rc_lowpass", help="Passive filter type (rc_lowpass, rlc_series_bandpass, etc.)")
    p_size.add_argument("--r", default=None, help="Filter characteristic resistance")
    p_size.add_argument("--q", default=None, help="Quality factor Q")
    p_size.add_argument("--c", default=None, help="Base capacitance")
    p_size.add_argument("--vdd", default=None, help="Supply voltage VDD for MOSFET")
    p_size.add_argument("--rg1", default=None, help="MOSFET gate pull-up resistor RG1")
    p_size.add_argument("--rg2", default=None, help="MOSFET gate pull-down resistor RG2")
    p_size.add_argument("--rd", default=None, help="MOSFET drain resistor RD")
    p_size.add_argument("--rs", default=None, help="MOSFET source resistor RS")
    p_size.add_argument("--kp", default=None, help="MOSFET transconductance parameter Kp")
    p_size.add_argument("--vth", default=None, help="MOSFET threshold voltage Vth")
    p_size.add_argument("--rsource", default=None, help="L-match source impedance")
    p_size.add_argument("--rload", default=None, help="L-match load impedance")
    p_size.add_argument("--f0", default=None, help="Center / resonant frequency")
    p_size.add_argument("--series", default="E24", choices=["E12", "E24", "E96"])
    p_size.set_defaults(func=cmd_size_block)

    p_sim = sub.add_parser("sim", help="Run a .cir or .asc file in LTspice batch mode and parse .meas")
    p_sim.add_argument("circuit", help="Path to .cir or .asc file")
    p_sim.add_argument("--timeout", type=int, default=180, help="Timeout in seconds")
    p_sim.set_defaults(func=cmd_sim)

    p_ver = sub.add_parser("verify-asc", help="Compile .asc schematic and verify zero NC_ pins & netlist match")
    p_ver.add_argument("asc", help="Path to .asc schematic file")
    p_ver.add_argument("--cir", default=None, help="Optional reference .cir netlist to compare against")
    p_ver.set_defaults(func=cmd_verify_asc)

    p_vis = sub.add_parser("import-vision", help="Audit & reconstruct a circuit from an extracted image graph JSON")
    p_vis.add_argument("json_file", help="Path to JSON graph extracted from schematic image")
    p_vis.add_argument("--mode", default="ask_user", choices=["ask_user", "autonomous"])
    p_vis.add_argument("--out-cir", default=None, help="Output .cir file path (in autonomous mode)")
    p_vis.add_argument("--out-asc", default=None, help="Output .asc file path (in autonomous mode)")
    p_vis.set_defaults(func=cmd_import_vision)

    p_insp = sub.add_parser("inspect", help="Inspect an LTspice file (.asc/.cir/.net/.sub/.lib/.asy/.plt/.log) or folder")
    p_insp.add_argument("path", help="Path to LTspice file or project directory")
    p_insp.set_defaults(func=cmd_inspect)

    p_beau = sub.add_parser("beautify-asc", help="Auto-fix overlapping .tran/.meas directives and vertical flags in an .asc file")
    p_beau.add_argument("asc", help="Path to input .asc file")
    p_beau.add_argument("--out", default=None, help="Optional output .asc path")
    p_beau.set_defaults(func=cmd_beautify_asc)

    p_nta = sub.add_parser("netlist-to-asc", help="Compile a .cir/.net netlist into a clean .asc schematic")
    p_nta.add_argument("netlist", help="Path to input .cir or .net file")
    p_nta.add_argument("out", help="Path to output .asc file")
    p_nta.add_argument("--style", default="A_textbook_wired", choices=["A_textbook_wired", "B_power_rail_bus", "C_modular_blocks"])
    p_nta.set_defaults(func=cmd_netlist_to_asc)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
