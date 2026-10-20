# CLAUDE.md — Anthropic Claude Code Guide for AI-SPICE

This repository provides **AI-SPICE** (`ai_spice`), an equation-grounded electronic design automation (EDA) framework, CLI, and Model Context Protocol (MCP) server for LTspice 24 / XVII.

## Quick Commands

- **Run full test suite**: `pytest`
- **Check LTspice installation**: `python -m ai_spice.cli doctor`
- **Compile `.cir` netlist to wired `.asc` schematic**: `python -m ai_spice.cli netlist-to-asc <in.cir> <out.asc> --style A_textbook_wired --verify`
- **Simulate `.cir` or `.asc` in batch mode**: `python -m ai_spice.cli sim <file.cir_or_asc>`
- **Verify `.asc` schematic (`NC_ = 0`)**: `python -m ai_spice.cli verify-asc <file.asc>`
- **Start stdio MCP server**: `python -m ai_spice.mcp_server`

## Architecture & Module Map (`src/ai_spice/` & `src/ia_spice/`)

- `sizing.py`: Analytical first-principles sizing across electronic domains (`size_opamp_stage`, `size_bjt_OperatingPoint`, `size_mosfet_stage`, `size_passive_filter`, `size_sallen_key_filter`, `size_colpitts_tank`, `evaluate_barkhausen_colpitts`, `size_l_matching_network`, `size_voltage_divider`, `StageContract`, `check_cascade_loading`, `snap_to_series` for E12/E24/E96).
- `asc_compiler.py`: 16-pixel grid-aligned LTspice `.asc` schematic AST (`SchematicBuilder`), calibrated symbol pin offset table (`SYMBOL_PIN_OFFSETS` for `res`, `cap`, `ind`, `diode`, `zener`, `schottky`, `led`, `varactor`, `npn`, `pnp`, `nmos`, `pmos`, `njf`, `pjf`, `Opamps\opamp2`, `Opamps\UniversalOpamp2`, `Opamps\LT1360`, plus custom `.asy` discovery), and geometric DRC (`run_geometric_drc`).
- `ltspice_project.py`: Topological left-to-right signal-flow `.asc` compiler (`compile_netlist_to_asc`), orthogonal Manhattan signal-net router (`wire_signal_nets_on_schematic`), and universal reader/editor for `.asc`, `.cir`, `.net`, `.sub`, `.lib`, `.asy`, `.plt`, and `.log` files.
- `simulator.py` & `meas_parser.py`: Headless `LTspice.exe -b` runner, deterministic `.meas` parser (UTF-8 / UTF-16LE), and closed-loop failure diagnostics.
- `netlist_verifier.py`: Formal `.asc` verification via `LTspice.exe -netlist` guaranteeing zero floating pins (`NC_ = 0`) and 100% node connectivity isomorphism.
- `vision_to_spice.py`: Reconstructs `.cir` and wired `.asc` schematics from circuit photos/diagrams with interactive missing-parameter auditing (`ask_user` and `autonomous` modes).

## Core Rule When Generating `.asc` Schematics

Always use `compile_netlist_to_asc(..., style="A_textbook_wired")` and verify with `verify_asc_netlist(...)`. Every signal, coupling, and feedback net must be physically connected by orthogonal `WIRE` segments on the 16-pixel grid (`NC_ = 0`), leaving only power rails (`VCC`, `VEE`, `VDD`, `VSS`) and Ground (`0`) on local flags.
