# GitHub Copilot Instructions for AI-SPICE

When assisting with circuit design, SPICE netlists (`.cir`), or LTspice schematics (`.asc`) in this repository:

- Use `ai_spice.sizing` to compute analytical operating points and snap component values to standard E12/E24/E96 series before running SPICE simulations.
- Use `ai_spice.ltspice_project.compile_netlist_to_asc(cir_path, asc_path, style="A_textbook_wired")` to compile netlists into 16-pixel grid-aligned, topologically ordered, signal-wired `.asc` schematics (`NC_ = 0`).
- Use `ai_spice.simulator.run_ltspice_batch` and `ai_spice.netlist_verifier.verify_asc_netlist` to verify every schematic and simulation result in closed loop.
- See `skills/ai-spice-engineer/SKILL.md` and `AGENTS.md` for the complete 7-phase engineering workflow.
