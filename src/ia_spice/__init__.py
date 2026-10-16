"""
IA-SPICE: Equation-Grounded AI Analog & RF Circuit Engineering Framework.

Provides:
- Analytical first-principles sizing & E12/E24/E96 standard series snapping (`sizing`)
- Automated LTspice batch runner & deterministic `.meas` log parser (`simulator`, `meas_parser`)
- Physics-based diagnostic rules for analog/RF failure modes (`diagnostics`)
- 16-pixel grid-aligned `.asc` schematic compiler & formal netlist isomorphism verifier (`asc_compiler`, `netlist_verifier`)
- Publication-grade reporting & lab testbench guide generation (`reporting`)
"""

__version__ = "0.1.0"

from .sizing import (
    snap_to_series,
    format_spice_value,
    parse_spice_value,
    size_bjt_OperatingPoint,
    size_colpitts_tank,
    evaluate_barkhausen_colpitts,
    evaluate_varactor_cv,
    StageContract,
    check_cascade_loading,
)
from .vision_to_spice import (
    ExtractedComponent,
    MissingParamItem,
    VisionCircuitGraph,
    VisionImportAudit,
    audit_circuit_from_image,
    resolve_and_compile_vision_graph,
)

from .ltspice_project import (
    add_component_to_asc,
    beautify_asc_file,
    compile_netlist_to_asc,
    inspect_ltspice_project,
    modify_asc_component,
    parse_asc_file,
    parse_asy_symbol,
    parse_plt_file,
    parse_spice_library,
    parse_spice_netlist,
    write_plt_file,
)

__all__ = [
    "__version__",
    "snap_to_series",
    "format_spice_value",
    "parse_spice_value",
    "size_bjt_OperatingPoint",
    "size_colpitts_tank",
    "evaluate_barkhausen_colpitts",
    "evaluate_varactor_cv",
    "StageContract",
    "check_cascade_loading",
    "ExtractedComponent",
    "MissingParamItem",
    "VisionCircuitGraph",
    "VisionImportAudit",
    "audit_circuit_from_image",
    "resolve_and_compile_vision_graph",
    "parse_asc_file",
    "beautify_asc_file",
    "modify_asc_component",
    "add_component_to_asc",
    "parse_spice_netlist",
    "parse_spice_library",
    "parse_asy_symbol",
    "parse_plt_file",
    "write_plt_file",
    "compile_netlist_to_asc",
    "inspect_ltspice_project",
]
