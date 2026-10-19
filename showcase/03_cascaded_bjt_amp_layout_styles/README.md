# 03 — 2-Stage Cascaded BJT Amplifier & 3 Auto-Layout Styles

Demonstrates `StageContract` impedance isolation ($100\,\Omega$ load) and the 3 `.cir` $\rightarrow$ `.asc` wired layout architectures:
- **`estilo_A_textbook_wired.asc` + `.plt`**: Functional stage columns with full orthogonal signal wiring and local power/GND flags.
- **`estilo_B_power_rail_bus.asc`**: Continuous top horizontal `VCC` power bus wire + full orthogonal signal wiring.
- **`estilo_C_modular_blocks.asc`**: Ultra-spaced modular sub-circuit cells + full orthogonal signal wiring.
- **`estilo_A_modified_with_cdec.asc`**: Demonstrates programmatic component injection (`add_component_to_asc` + `modify_asc_component`).
