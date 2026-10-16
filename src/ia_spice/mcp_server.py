"""
JSON-RPC 2.0 Model Context Protocol (MCP) Server for IA-SPICE (`ia_spice.mcp_server`).

Why zero external MCP SDK dependency:
-------------------------------------
Implementing the standard JSON-RPC 2.0 stdio transport directly in pure Python stdlib means users
can launch `python -m ia_spice.mcp_server` immediately inside Claude Code, Antigravity, Cursor, or
Gemini CLI without fighting dependency version conflicts.

Exposed Tools:
1. `ia_spice_snap_series`          : Snap ideal component values to E12/E24/E96 commercial series.
2. `ia_spice_size_bjt_stage`       : Calculate analytical DC/AC operating point for CE/CC BJT stages.
3. `ia_spice_size_colpitts`        : Calculate Colpitts LC + Varactor resonance and Barkhausen startup margin.
4. `ia_spice_run_simulation`       : Run `.cir` or `.asc` in LTspice batch mode, parse `.meas`, and diagnose physics.
5. `ia_spice_verify_schematic_asc` : Compile `.asc` via `LTspice.exe -netlist`, check `NC_ = 0` & netlist isomorphism.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import Any, Dict, List

from .netlist_verifier import verify_asc_netlist
from .simulator import run_ltspice_batch
from .sizing import (
    evaluate_barkhausen_colpitts,
    evaluate_varactor_cv,
    format_spice_value,
    parse_spice_value,
    size_bjt_OperatingPoint,
    size_colpitts_tank,
    snap_to_series,
)


MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "ia_spice_snap_series",
        "description": "Snap an ideal component value (Ohms, Farads, Henries) to the nearest commercial EIA series (E12, E24, E96).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "Value in SPICE or float notation (e.g. '10.83k', '211p')"},
                "series": {"type": "string", "enum": ["E12", "E24", "E96"], "default": "E24"},
            },
            "required": ["value"],
        },
    },
    {
        "name": "ia_spice_size_bjt_stage",
        "description": "Compute exact analytical DC operating point and small-signal AC parameters for a BJT stage (CE or CC).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "v_cc": {"type": "number"},
                "r1": {"type": "number"},
                "r2": {"type": "number"},
                "r_e_dc": {"type": "number"},
                "r_c": {"type": "number", "default": 0.0},
                "r_e_rf_unbypassed": {"type": "number"},
                "r_load_ac": {"type": "number"},
                "beta": {"type": "number", "default": 100.0},
                "topology": {"type": "string", "enum": ["CE", "CC"], "default": "CE"},
            },
            "required": ["v_cc", "r1", "r2", "r_e_dc"],
        },
    },
    {
        "name": "ia_spice_size_colpitts",
        "description": "Calculate Colpitts LC + Varactor tank resonance and Barkhausen startup margin (g_m * R_p).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "l1": {"type": "number", "description": "Inductance in Henries"},
                "r_ser_inductor": {"type": "number", "description": "Inductor series loss resistance in Ohms"},
                "c1": {"type": "number", "description": "Base-Emitter feedback capacitance in Farads"},
                "c2": {"type": "number", "description": "Emitter-Ground feedback capacitance in Farads"},
                "g_m": {"type": "number", "description": "Transistor transconductance in Siemens"},
                "c_var": {"type": "number", "default": 0.0, "description": "Varactor capacitance in Farads"},
                "c_cv": {"type": "number", "default": 0.0, "description": "Varactor coupling capacitance in Farads"},
            },
            "required": ["l1", "r_ser_inductor", "c1", "c2", "g_m"],
        },
    },
    {
        "name": "ia_spice_run_simulation",
        "description": "Run a .cir or .asc file in LTspice batch mode (-b), extract all .meas results, and run physics diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "circuit_path": {"type": "string", "description": "Absolute path to .cir or .asc file"},
                "timeout_sec": {"type": "integer", "default": 180},
            },
            "required": ["circuit_path"],
        },
    },
    {
        "name": "ia_spice_verify_schematic_asc",
        "description": "Compile an LTspice .asc schematic via LTspice -netlist, verify zero unconnected pins (NC_), and check netlist isomorphism against a reference .cir file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asc_path": {"type": "string", "description": "Absolute path to .asc file"},
                "reference_cir_path": {"type": "string", "description": "Optional reference .cir file path"},
            },
            "required": ["asc_path"],
        },
    },
    {
        "name": "ia_spice_import_vision_graph",
        "description": "Audit a circuit graph extracted from a schematic image. Detects missing values/models/supplies. In 'ask_user' mode, returns the user prompt listing missing parameters and analytical suggestions. In 'autonomous' mode, sizes missing components (E24) and compiles both .cir and 16px .asc files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "components": {"type": "array", "items": {"type": "object"}},
                "design_targets": {"type": "object"},
                "mode": {"type": "string", "enum": ["ask_user", "autonomous"], "default": "ask_user"},
                "user_overrides": {"type": "object"},
                "output_asc_path": {"type": "string"},
                "output_cir_path": {"type": "string"},
            },
            "required": ["title", "components"],
        },
    },
    {
        "name": "ia_spice_edit_ltspice_project",
        "description": "Inspect, beautify, modify component values, add components, or compile netlist-to-asc across existing LTspice project files (.asc, .cir, .net, .sub, .lib, .asy, .plt, .log).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["inspect", "beautify_asc", "modify_component", "add_component", "netlist_to_asc"],
                },
                "target_path": {"type": "string"},
                "output_path": {"type": "string"},
                "inst_name": {"type": "string"},
                "symbol_type": {"type": "string"},
                "value": {"type": "string"},
                "nodes": {"type": "array", "items": {"type": "string"}},
                "snap_series": {"type": "string", "enum": ["E12", "E24", "E96"]},
                "style": {
                    "type": "string",
                    "enum": ["A_textbook_wired", "B_power_rail_bus", "C_modular_blocks"],
                    "default": "A_textbook_wired",
                },
            },
            "required": ["action", "target_path"],
        },
    },
]


def dispatch_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool call and return a JSON-serializable dictionary."""
    if name == "ia_spice_edit_ltspice_project":
        from .ltspice_project import (
            add_component_to_asc,
            beautify_asc_file,
            compile_netlist_to_asc,
            inspect_ltspice_project,
            modify_asc_component,
            parse_spice_netlist,
        )

        act = str(args["action"])
        tpath = str(args["target_path"])
        opath = args.get("output_path")

        if act == "inspect":
            return dataclasses.asdict(inspect_ltspice_project(tpath))
        if act == "beautify_asc":
            _, fixes = beautify_asc_file(tpath, output_path=opath)
            return {"output_path": opath or tpath, "fixes_applied": fixes}
        if act == "modify_component":
            if tpath.lower().endswith(".asc"):
                modify_asc_component(
                    tpath,
                    inst_name=str(args["inst_name"]),
                    new_value=str(args["value"]),
                    snap_series=args.get("snap_series"),
                    output_path=opath,
                )
            else:
                proj = parse_spice_netlist(tpath)
                proj.modify_component_value(
                    str(args["inst_name"]),
                    str(args["value"]),
                    snap_series=args.get("snap_series"),
                )
                proj.save(opath or tpath)
            return {"status": "modified", "output_path": opath or tpath, "inst_name": args["inst_name"]}
        if act == "add_component":
            if tpath.lower().endswith(".asc"):
                add_component_to_asc(
                    tpath,
                    inst_name=str(args["inst_name"]),
                    symbol_type=str(args.get("symbol_type", "res")),
                    value=str(args["value"]),
                    nodes=list(args["nodes"]),
                    output_path=opath,
                )
            else:
                proj = parse_spice_netlist(tpath)
                proj.add_component(str(args["inst_name"]), list(args["nodes"]), str(args["value"]))
                proj.save(opath or tpath)
            return {"status": "added", "output_path": opath or tpath, "inst_name": args["inst_name"]}
        if act == "netlist_to_asc":
            style = str(args.get("style", "A_textbook_wired"))
            out_asc = str(opath or (tpath.rsplit(".", 1)[0] + ".asc"))
            compile_netlist_to_asc(tpath, out_asc, style=style)
            return {"status": "compiled", "output_asc": out_asc, "style": style}

    if name == "ia_spice_import_vision_graph":
        from .vision_to_spice import (
            ExtractedComponent,
            VisionCircuitGraph,
            resolve_and_compile_vision_graph,
        )
        comps = [
            ExtractedComponent(
                inst_name=c["inst_name"],
                comp_type=c["comp_type"],
                nodes=list(c["nodes"]),
                value=c.get("value"),
                role_hint=c.get("role_hint"),
            )
            for c in args["components"]
        ]
        graph = VisionCircuitGraph(
            title=str(args["title"]),
            components=comps,
            design_targets=dict(args.get("design_targets") or {}),
        )
        mode = str(args.get("mode", "ask_user"))
        audit, cir_text, sch = resolve_and_compile_vision_graph(
            graph,
            mode=mode,
            user_overrides=args.get("user_overrides"),
        )
        if cir_text and args.get("output_cir_path"):
            with open(args["output_cir_path"], "w", encoding="utf-8") as f:
                f.write(cir_text)
        if sch and args.get("output_asc_path"):
            sch.save(args["output_asc_path"])
        return {
            "is_complete": audit.is_complete,
            "mode_used": mode,
            "missing_items": [dataclasses.asdict(m) for m in audit.missing_items],
            "topology_warnings": audit.topology_warnings,
            "user_prompt_markdown": audit.format_user_prompt(),
            "compiled_cir": cir_text,
        }

    if name == "ia_spice_snap_series":
        raw = parse_spice_value(str(args["value"]))
        series = args.get("series", "E24")
        snapped, err = snap_to_series(raw, series)
        return {
            "input_float": raw,
            "series": series,
            "snapped_float": snapped,
            "snapped_spice": format_spice_value(snapped),
            "relative_error_pct": round(err, 4),
        }

    if name == "ia_spice_size_bjt_stage":
        res = size_bjt_OperatingPoint(
            v_cc=float(args["v_cc"]),
            r1=float(args["r1"]),
            r2=float(args["r2"]),
            r_e_dc=float(args["r_e_dc"]),
            r_c=float(args.get("r_c", 0.0)),
            r_e_rf_unbypassed=float(args["r_e_rf_unbypassed"]) if "r_e_rf_unbypassed" in args else None,
            r_load_ac=float(args["r_load_ac"]) if "r_load_ac" in args else None,
            beta=float(args.get("beta", 100.0)),
            topology=str(args.get("topology", "CE")),
        )
        return dataclasses.asdict(res)

    if name == "ia_spice_size_colpitts":
        tank = size_colpitts_tank(
            l1=float(args["l1"]),
            c1=float(args["c1"]),
            c2=float(args["c2"]),
            c_var=float(args.get("c_var", 0.0)),
            c_cv=float(args["c_cv"]) if args.get("c_cv") else None,
        )
        bk = evaluate_barkhausen_colpitts(
            l1=float(args["l1"]),
            r_ser_inductor=float(args["r_ser_inductor"]),
            c1=float(args["c1"]),
            c2=float(args["c2"]),
            g_m=float(args["g_m"]),
            f_osc_hz=tank.f_lc_hz,
        )
        return {"tank": dataclasses.asdict(tank), "barkhausen": dataclasses.asdict(bk)}

    if name == "ia_spice_run_simulation":
        sim = run_ltspice_batch(
            circuit_path=str(args["circuit_path"]),
            timeout_sec=int(args.get("timeout_sec", 180)),
        )
        return {
            "succeeded": sim.succeeded,
            "circuit_path": sim.circuit_path,
            "log_path": sim.log_path,
            "scalars": {k: sim.parsed_log.get_scalar(k) for k in sim.parsed_log.scalars},
            "step_variable": sim.parsed_log.step_variable,
            "step_values": sim.parsed_log.step_values,
            "stepped": {k: sim.parsed_log.get_stepped_series(k) for k in sim.parsed_log.stepped},
            "failed_measurements": sim.parsed_log.failed_measurements,
            "diagnostics": sim.diagnostics,
        }

    if name == "ia_spice_verify_schematic_asc":
        ver = verify_asc_netlist(
            asc_path=str(args["asc_path"]),
            reference_cir_path=args.get("reference_cir_path"),
        )
        return {
            "is_clean": ver.is_clean,
            "asc_path": ver.asc_path,
            "net_path": ver.net_path,
            "unconnected_pins": ver.unconnected_pins,
            "mismatches": ver.mismatches,
            "missing_components": ver.missing_components,
            "component_nodes": ver.component_nodes,
        }

    raise ValueError(f"Unknown MCP tool: {name}")


def handle_jsonrpc_Message(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single JSON-RPC 2.0 request dictionary and return a response dictionary."""
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ia-spice-mcp", "version": "0.1.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS},
        }

    if method == "tools/call":
        t_name = params.get("name", "")
        t_args = params.get("arguments", {}) or {}
        try:
            res_obj = dispatch_tool_call(t_name, t_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(res_obj, indent=2)}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    """Run the JSON-RPC 2.0 stdio loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if "id" not in msg:
                continue  # Notification (e.g. notifications/initialized)
            resp = handle_jsonrpc_Message(msg)
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
        except Exception as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
