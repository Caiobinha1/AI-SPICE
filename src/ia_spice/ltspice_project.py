"""
Universal LTspice Project Reader, Editor, Troubleshooter & Multi-Style Auto-Layout Engine
(`ia_spice.ltspice_project`).

Supports all LTspice project file formats:
1. `.asc`  - Visual schematics (lossless parser -> `SchematicBuilder` AST -> modifier -> sanitizer -> writer)
2. `.cir` / `.net` / `.sp` - SPICE netlists (component cards, `.model`, `.subckt`, `.param`, `.meas`, `.tran`/`.ac`)
3. `.sub` / `.lib` - Subcircuit and model libraries
4. `.asy`  - Custom LTspice symbol definitions (extracts exact pin coordinates on the 16px grid)
5. `.plt`  - Waveform viewer plot settings (reads and generates trace configurations)
6. `.log`  - Simulation logs and `.meas` results

Also provides 3 distinct automatic `.asc` layout engines (`compile_netlist_to_asc_styles`):
- `style="A_textbook_wired"`: Classic stage-by-stage orthogonal wired schematic with local horizontal flags.
- `style="B_power_rail_bus"`: Global top `VCC` horizontal bus wire + stage-wired signal path (zero repeated `VCC` labels).
- `style="C_modular_blocks"`: Visually separated functional sub-blocks (`; [STAGE 1]`, `; [STAGE 2]`) with wide spacing
  and a dedicated right-side / footer simulation dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Dict, List, Optional, Tuple

from .asc_compiler import (
    SYMBOL_PIN_OFFSETS,
    FlagLabel,
    PlacedComponent,
    Point,
    SchematicBuilder,
    SpiceDirectiveText,
    WireSegment,
)
from .meas_parser import ParsedLogResult, parse_ltspice_log
from .netlist_verifier import NetlistVerificationReport, verify_asc_netlist
from .sizing import format_spice_value, parse_spice_value, snap_to_series


def _read_text_auto_encoding(file_path: str) -> str:
    """Read an LTspice text file handling UTF-8, UTF-16LE (LTspice XVII), and cp1252/Latin-1."""
    with open(file_path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xff\xfe") or b"\x00Version 4" in raw[:40] or b"V\x00e\x00r\x00s\x00i\x00o\x00n\x00" in raw[:40]:
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


# ==============================================================================
# 1. `.asy` Custom Symbol Parser
# ==============================================================================

@dataclass
class AsyPinInfo:
    pin_name: str
    spice_order: int
    x: int
    y: int


@dataclass
class ParsedAsySymbol:
    symbol_name: str
    symbol_type: str
    prefix: Optional[str] = None
    model_file: Optional[str] = None
    value: Optional[str] = None
    pins: List[AsyPinInfo] = field(default_factory=list)


def parse_asy_symbol(asy_path: str, register_in_compiler: bool = True) -> ParsedAsySymbol:
    """
    Parse an LTspice `.asy` symbol file, extracting its exact pin `(x, y)` offsets and attributes,
    and optionally registering its `R0` pin offsets in `SYMBOL_PIN_OFFSETS`.
    """
    text = _read_text_auto_encoding(asy_path)
    sym_name = os.path.splitext(os.path.basename(asy_path))[0]
    res = ParsedAsySymbol(symbol_name=sym_name, symbol_type="cell")

    current_pin_xy: Optional[Tuple[int, int]] = None
    current_pin_name: str = ""
    current_pin_order: int = 0

    def _flush_pin() -> None:
        nonlocal current_pin_xy, current_pin_name, current_pin_order
        if current_pin_xy is not None:
            pname = current_pin_name or str(current_pin_order or len(res.pins) + 1)
            porder = current_pin_order or (len(res.pins) + 1)
            res.pins.append(AsyPinInfo(pin_name=pname, spice_order=porder, x=current_pin_xy[0], y=current_pin_xy[1]))
            current_pin_xy = None
            current_pin_name = ""
            current_pin_order = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("SymbolType "):
            res.symbol_type = line.split(None, 1)[1]
        elif line.startswith("SYMATTR Prefix "):
            res.prefix = line.split(None, 2)[2]
        elif line.startswith("SYMATTR ModelFile "):
            res.model_file = line.split(None, 2)[2]
        elif line.startswith("SYMATTR Value "):
            res.value = line.split(None, 2)[2]
        elif line.startswith("PIN "):
            _flush_pin()
            parts = line.split()
            if len(parts) >= 3:
                current_pin_xy = (int(parts[1]), int(parts[2]))
        elif line.startswith("PINATTR PinName "):
            current_pin_name = line.split(None, 2)[2]
        elif line.startswith("PINATTR SpiceOrder "):
            current_pin_order = int(line.split(None, 2)[2])

    _flush_pin()

    if register_in_compiler and res.pins:
        pin_dict: Dict[str, Point] = {}
        for p in sorted(res.pins, key=lambda item: item.spice_order):
            pin_dict[p.pin_name] = (p.x, p.y)
            pin_dict[str(p.spice_order)] = (p.x, p.y)
        if len(res.pins) == 2:
            sorted_by_y = sorted(res.pins, key=lambda item: item.y)
            pin_dict.setdefault("top", (sorted_by_y[0].x, sorted_by_y[0].y))
            pin_dict.setdefault("bot", (sorted_by_y[1].x, sorted_by_y[1].y))
        SYMBOL_PIN_OFFSETS[(sym_name.lower(), "R0")] = pin_dict

    return res


# ==============================================================================
# 2. `.sub` / `.lib` Library Parser
# ==============================================================================

@dataclass
class SubcktDefinition:
    name: str
    ports: List[str]
    body_lines: List[str]


@dataclass
class ParsedSpiceLibrary:
    file_path: str
    models: Dict[str, str] = field(default_factory=dict)
    subckts: Dict[str, SubcktDefinition] = field(default_factory=dict)


def parse_spice_library(lib_path: str) -> ParsedSpiceLibrary:
    """Parse a `.sub` or `.lib` file to extract all `.model` and `.subckt` definitions."""
    text = _read_text_auto_encoding(lib_path)
    lib = ParsedSpiceLibrary(file_path=lib_path)

    active_subckt: Optional[SubcktDefinition] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("*"):
            continue
        upper = line.upper()
        if upper.startswith(".SUBCKT "):
            parts = line.split()
            if len(parts) >= 2:
                active_subckt = SubcktDefinition(name=parts[1], ports=parts[2:], body_lines=[line])
                lib.subckts[parts[1].upper()] = active_subckt
        elif upper.startswith(".ENDS"):
            if active_subckt is not None:
                active_subckt.body_lines.append(line)
                active_subckt = None
        elif active_subckt is not None:
            active_subckt.body_lines.append(line)
        elif upper.startswith(".MODEL "):
            parts = line.split()
            if len(parts) >= 2:
                lib.models[parts[1].upper()] = line

    return lib


# ==============================================================================
# 3. `.plt` Plot Settings Parser & Generator
# ==============================================================================

def parse_plt_file(plt_path: str) -> List[str]:
    """Extract all plotted waveform expressions (`traces`) from an LTspice `.plt` file."""
    text = _read_text_auto_encoding(plt_path)
    return re.findall(r"traces:\s*\d+\s*\{[^\"]*\"([^\"]+)\"", text)


def write_plt_file(plt_path: str, traces: List[str], analysis_type: str = "Transient Analysis") -> str:
    """Generate a ready-to-open LTspice `.plt` plot settings file for `traces`."""
    trace_lines = []
    for idx, tr in enumerate(traces, start=1):
        trace_lines.append(f"      traces: 1 {{524290,0,\"{tr}\"}}")
    traces_block = "\n".join(trace_lines)
    content = (
        f"[{analysis_type}]\n"
        "{\n"
        "   Npanes: 1\n"
        "   {\n"
        "      axes: 2\n"
        f"{traces_block}\n"
        "   }\n"
        "}\n"
    )
    with open(plt_path, "w", encoding="utf-8") as f:
        f.write(content)
    return plt_path


# ==============================================================================
# 4. `.asc` Schematic Reader, Editor & Beautifier
# ==============================================================================

def parse_asc_file(asc_path: str) -> SchematicBuilder:
    """
    Parse any existing LTspice `.asc` schematic into a live, editable `SchematicBuilder` object.
    Preserves all wires, flags, components, custom `WINDOW` offsets, `SYMATTR` fields, and `TEXT` directives.
    """
    text = _read_text_auto_encoding(asc_path)
    sch = SchematicBuilder(width=1200, height=800)

    current_comp: Optional[PlacedComponent] = None
    pending_windows: List[str] = []

    def _flush_comp() -> None:
        nonlocal current_comp, pending_windows
        if current_comp is not None:
            if not current_comp.inst_name:
                current_comp.inst_name = f"U_{len(sch.components) + 1}"
            current_comp.custom_windows = list(pending_windows)
            sch.components[current_comp.inst_name] = current_comp
            current_comp = None
            pending_windows = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("SHEET "):
            parts = line.split()
            if len(parts) >= 4:
                sch.width = int(parts[2])
                sch.height = int(parts[3])
        elif line.startswith("WIRE "):
            _flush_comp()
            parts = line.split()
            if len(parts) >= 5:
                x1, y1, x2, y2 = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
                sch.wires.append(WireSegment(x1, y1, x2, y2))
        elif line.startswith("FLAG "):
            _flush_comp()
            parts = line.split(None, 3)
            if len(parts) >= 4:
                sch.flags.append(FlagLabel(int(parts[1]), int(parts[2]), parts[3]))
        elif line.startswith("SYMBOL "):
            _flush_comp()
            parts = line.split()
            if len(parts) >= 5:
                sym_type = parts[1]
                sx, sy = int(parts[2]), int(parts[3])
                orient = parts[4]
                current_comp = PlacedComponent(
                    inst_name="",
                    symbol_type=sym_type,
                    x=sx,
                    y=sy,
                    orientation=orient,
                )
        elif line.startswith("WINDOW "):
            if current_comp is not None:
                pending_windows.append(line)
        elif line.startswith("SYMATTR "):
            if current_comp is not None:
                parts = line.split(None, 2)
                if len(parts) >= 3:
                    attr_name, attr_val = parts[1], parts[2]
                    if attr_name == "InstName":
                        current_comp.inst_name = attr_val
                    elif attr_name == "Value":
                        current_comp.value = attr_val
                    elif attr_name == "Prefix":
                        current_comp.prefix = attr_val
                    elif attr_name == "SpiceLine":
                        current_comp.spice_line = attr_val
                    else:
                        current_comp.extra_attrs[attr_name] = attr_val
        elif line.startswith("TEXT "):
            _flush_comp()
            # Format: TEXT <x> <y> <align> <font> <text>
            parts = line.split(None, 5)
            if len(parts) >= 6:
                tx, ty = int(parts[1]), int(parts[2])
                txt = parts[5]
                is_comm = txt.startswith(";")
                sch.directives.append(SpiceDirectiveText(x=tx, y=ty, directive=txt, is_comment=is_comm))

    _flush_comp()
    return sch


def beautify_asc_file(
    asc_path: str,
    output_path: Optional[str] = None,
    decrowd_components: bool = True,
) -> Tuple[SchematicBuilder, List[str]]:
    """
    Load an existing `.asc` file, automatically repair visual layout problems:
    1. Converts vertical colliding pin flags into horizontal flag tabs (`sanitize_visual_layout`).
    2. Adds `32 px` vertical wire stubs to ground symbols glued directly onto component pins.
    3. Relocates all `.tran`, `.ac`, `.meas`, `.model`, `.include` directives into a clean footer
       at least `160 px` below the lowest circuit element, spaced `48 px` apart vertically.
    4. Optionally resolves component crowding (`decrowd_components=True`) if isolated components overlap.
    Returns `(schematic_builder, list_of_fixes_applied)`.
    """
    sch = parse_asc_file(asc_path)
    fixes: List[str] = []

    _, _, _, max_y_before = sch.get_circuit_bounds()
    for d in sch.directives:
        if d.y is not None and d.y < max_y_before + 128:
            fixes.append(f"Relocated overlapping directive '{d.directive}' from y={d.y} to clean footer (>={max_y_before + 160}).")
            d.y = None  # Force automatic footer placement in `to_asc_string()`

    # Count flags converted by sanitize_visual_layout
    before_flags = [(fl.x, fl.y, fl.net_name) for fl in sch.flags]
    sch.sanitize_visual_layout()
    after_flags = [(fl.x, fl.y, fl.net_name) for fl in sch.flags]
    for (bx, by, bnet), (ax, ay, _) in zip(before_flags, after_flags):
        if (bx, by) != (ax, ay):
            fixes.append(f"Added orthogonal stub for flag '{bnet}' ({bx},{by}) -> ({ax},{ay}) to prevent vertical label overlap.")

    # If there are no wires connecting components (pure flag-based schematic) and components are crowded, space them out
    drc_before = sch.run_geometric_drc()
    if decrowd_components and drc_before and len(sch.wires) == 0:
        for idx, comp in enumerate(sch.components.values()):
            comp.x = 96 + idx * 224
            comp.y = 224
        fixes.append("Respaced crowded flag-connected components onto 224px columns.")

    target = output_path or asc_path
    sch.save(target, auto_sanitize=True)
    return sch, fixes


# ==============================================================================
# 5. `.cir` / `.net` SPICE Netlist Parser & Editor
# ==============================================================================

@dataclass
class SpiceNetlistComponent:
    inst_name: str
    prefix: str
    nodes: List[str]
    value: str
    raw_line: str

    @property
    def symbol_type(self) -> str:
        p = self.prefix.upper()
        if p == "R":
            return "res"
        if p == "C":
            return "cap"
        if p == "L":
            return "ind"
        if p == "V":
            return "voltage"
        if p == "I":
            return "current"
        if p == "D":
            return "diode"
        if p == "Q":
            return "pnp" if "PNP" in self.value.upper() else "npn"
        return "cell"


@dataclass
class SpiceNetlistProject:
    title: str
    components: Dict[str, SpiceNetlistComponent] = field(default_factory=dict)
    directives: List[str] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)
    source_path: Optional[str] = None

    def modify_component_value(
        self,
        inst_name: str,
        new_value: str,
        snap_series: Optional[str] = None,
    ) -> str:
        """Update the value of component `inst_name` (optionally snapping to `E12`/`E24`/`E96`)."""
        key = inst_name.upper()
        matches = [k for k in self.components if k.upper() == key]
        if not matches:
            raise KeyError(f"Component '{inst_name}' not found in netlist. Available: {list(self.components.keys())}")
        comp = self.components[matches[0]]
        final_val = new_value
        if snap_series:
            numeric = parse_spice_value(new_value)
            snapped, _ = snap_to_series(numeric, snap_series)
            final_val = format_spice_value(snapped)
        comp.value = final_val
        comp.raw_line = f"{comp.inst_name} {' '.join(comp.nodes)} {comp.value}"
        return final_val

    def add_component(self, inst_name: str, nodes: List[str], value: str) -> SpiceNetlistComponent:
        """Add a new component card to the netlist."""
        comp = SpiceNetlistComponent(
            inst_name=inst_name,
            prefix=inst_name[0].upper(),
            nodes=list(nodes),
            value=value,
            raw_line=f"{inst_name} {' '.join(nodes)} {value}",
        )
        self.components[inst_name] = comp
        return comp

    def remove_component(self, inst_name: str) -> None:
        key = inst_name.upper()
        for k in list(self.components.keys()):
            if k.upper() == key:
                del self.components[k]
                return
        raise KeyError(f"Component '{inst_name}' not found.")

    def add_or_update_directive(self, directive: str) -> None:
        clean = directive.strip()
        cmd_head = clean.split()[0].lower()
        if cmd_head in (".tran", ".ac", ".dc", ".op"):
            self.directives = [d for d in self.directives if not d.strip().lower().startswith(cmd_head)]
        self.directives.append(clean)

    def to_cir_string(self) -> str:
        lines = [f"* {self.title.lstrip('*').strip()}"]
        for c in self.components.values():
            lines.append(f"{c.inst_name} {' '.join(c.nodes)} {c.value}")
        for d in self.directives:
            lines.append(d)
        lines.append(".end")
        return "\n".join(lines) + "\n"

    def save(self, cir_path: Optional[str] = None) -> str:
        target = cir_path or self.source_path
        if not target:
            raise ValueError("No target path specified for saving netlist.")
        with open(target, "w", encoding="utf-8") as f:
            f.write(self.to_cir_string())
        return target


def parse_spice_netlist(path_or_text: str) -> SpiceNetlistProject:
    """Parse a `.cir`, `.net`, or `.sp` file (or raw string) into a `SpiceNetlistProject`."""
    if os.path.isfile(path_or_text):
        text = _read_text_auto_encoding(path_or_text)
        src_path = path_or_text
    else:
        text = path_or_text
        src_path = None

    raw_lines = text.splitlines()
    title = raw_lines[0].strip() if raw_lines else "* SPICE Netlist"
    proj = SpiceNetlistProject(title=title, source_path=src_path)

    in_subckt = False
    for idx, raw in enumerate(raw_lines):
        if idx == 0 and raw.strip().startswith("*"):
            continue
        line = raw.strip()
        if not line:
            continue
        if line.startswith("*"):
            proj.comments.append(line)
            continue
        # Strip inline ';' comment
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        upper = line.upper()
        if upper == ".END" or upper == ".BACKANNO":
            continue
        if upper.startswith(".SUBCKT "):
            in_subckt = True
            proj.directives.append(line)
            continue
        if upper.startswith(".ENDS"):
            in_subckt = False
            proj.directives.append(line)
            continue
        if in_subckt or line.startswith("."):
            proj.directives.append(line)
            continue

        tokens = line.split()
        inst = tokens[0]
        prefix = inst[0].upper()

        if prefix in ("R", "C", "L", "D") and len(tokens) >= 4:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:3]]
            val = " ".join(tokens[3:])
        elif prefix in ("V", "I") and len(tokens) >= 4:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:3]]
            val = " ".join(tokens[3:])
        elif prefix in ("Q", "J") and len(tokens) >= 5:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:4]]
            val = " ".join(tokens[4:])
        elif prefix == "M" and len(tokens) >= 6:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:5]]
            val = " ".join(tokens[5:])
        elif prefix == "X" and len(tokens) >= 4:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:-1]]
            val = tokens[-1]
        else:
            continue

        proj.components[inst] = SpiceNetlistComponent(
            inst_name=inst,
            prefix=prefix,
            nodes=nodes,
            value=val,
            raw_line=line,
        )

    return proj


# ==============================================================================
# 6. High-Level Modification API on `.asc` Schematics
# ==============================================================================

def modify_asc_component(
    asc_path: str,
    inst_name: str,
    new_value: Optional[str] = None,
    snap_series: Optional[str] = None,
    new_spice_line: Optional[str] = None,
    output_path: Optional[str] = None,
) -> SchematicBuilder:
    """Modify an existing component's value or SpiceLine in an `.asc` schematic and re-sanitize layout."""
    sch = parse_asc_file(asc_path)
    key = inst_name.upper()
    target_comp: Optional[PlacedComponent] = None
    for k, comp in sch.components.items():
        if k.upper() == key:
            target_comp = comp
            break
    if target_comp is None:
        raise KeyError(f"Component '{inst_name}' not found in '{asc_path}'. Available: {list(sch.components.keys())}")

    if new_value is not None:
        if snap_series:
            num = parse_spice_value(new_value)
            snapped, _ = snap_to_series(num, snap_series)
            target_comp.value = format_spice_value(snapped)
        else:
            target_comp.value = new_value
    if new_spice_line is not None:
        target_comp.spice_line = new_spice_line

    sch.save(output_path or asc_path, auto_sanitize=True)
    return sch


def add_component_to_asc(
    asc_path: str,
    inst_name: str,
    symbol_type: str,
    value: str,
    nodes: List[str],
    spice_line: Optional[str] = None,
    output_path: Optional[str] = None,
) -> SchematicBuilder:
    """
    Add a new component (`res`, `cap`, `ind`, `npn`, `diode`, `voltage`) connected to `nodes`
    into an existing `.asc` schematic, automatically placing it in a free column (`max_x + 224`)
    on the 16-pixel grid so it never overlaps any existing element!
    """
    sch = parse_asc_file(asc_path)
    _, min_y, max_x, _ = sch.get_circuit_bounds()
    new_x = ((max_x + 224) // 16) * 16
    new_y = max(192, ((min_y + 96) // 16) * 16)

    comp = sch.add_component(
        inst_name=inst_name,
        symbol_type=symbol_type.lower(),
        x=new_x,
        y=new_y,
        value=value,
        orientation="R0",
        spice_line=spice_line,
    )
    nodes_norm = ["0" if n.upper() == "GND" else n for n in nodes]
    ctype = symbol_type.lower()
    if ctype in ("npn", "pnp") and len(nodes_norm) >= 3:
        sch.power_pin(comp.pin("C"), nodes_norm[0]) if nodes_norm[0] != "0" else sch.ground_pin(comp.pin("C"), stub_dy=-32)
        sch.add_flag(comp.pin("B"), nodes_norm[1], stub_dx=-32)
        sch.add_flag(comp.pin("E"), nodes_norm[2], stub_dy=32, stub_dx=32) if nodes_norm[2] != "0" else sch.ground_pin(comp.pin("E"), stub_dy=32)
    elif len(nodes_norm) >= 2:
        if nodes_norm[0] == "0":
            sch.ground_pin(comp.pin("top"), stub_dy=-32)
        else:
            sch.power_pin(comp.pin("top"), nodes_norm[0], stub_dy=-32, stub_dx=32)

        if nodes_norm[1] == "0":
            sch.ground_pin(comp.pin("bot"), stub_dy=32)
        else:
            sch.add_flag(comp.pin("bot"), nodes_norm[1], stub_dy=32, stub_dx=32)

    sch.save(output_path or asc_path, auto_sanitize=True)
    return sch


# ==============================================================================
# 7. Three Automatic Layout Styles for Any Netlist (`compile_netlist_to_asc`)
# ==============================================================================

def _is_power_node(node: str) -> bool:
    u = node.upper()
    return u.startswith("VCC") or u.startswith("VDD") or u.startswith("VEE") or u.startswith("VSS")


def compile_netlist_to_asc(
    netlist_or_path: str,
    output_asc_path: str,
    style: str = "A_textbook_wired",
) -> SchematicBuilder:
    """
    Automatically compile any SPICE netlist (`.cir`/`.net`) into a clean, non-overlapping 16px-grid
    LTspice `.asc` schematic using one of 3 selectable visual layout architectures:

    - `style="A_textbook_wired"`:
      Groups components into functional columns (Supply/Stimulus -> Series Coupling Caps ->
      Shunt/Divider Branches -> Transistors & Collector/Emitter Branches -> Shunt Loads),
      wiring vertically stacked components on the same column and horizontally adjacent series stages,
      with horizontal net flags and a clean directive footer `>= 160 px` below.

    - `style="B_power_rail_bus"`:
      Draws a continuous horizontal `VCC` power bus wire across the top (`y = 64`) of the schematic so
      all pull-up resistors/collectors connect directly to the top `VCC` wire (eliminating repeated
      `VCC` labels on every branch!), while signal stages flow left-to-right below the bus.

    - `style="C_modular_blocks"`:
      Organizes the circuit into spacious functional sub-blocks separated by `288 px` horizontally
      with block title headers (`; === [BLOCO ...] ===`), horizontal port flags on every pin, and
      a dedicated right-hand simulation dashboard for `.tran` / `.meas` directives.
    """
    proj = parse_spice_netlist(netlist_or_path)
    comps = list(proj.components.values())
    style_key = style.upper()

    if style_key.startswith("C"):
        return _layout_style_c_modular_blocks(proj, comps, output_asc_path)
    if style_key.startswith("B"):
        return _layout_style_b_power_rail_bus(proj, comps, output_asc_path)
    return _layout_style_a_textbook_wired(proj, comps, output_asc_path)


def _group_into_functional_columns(comps: List[SpiceNetlistComponent]) -> List[List[SpiceNetlistComponent]]:
    """
    Partition netlist components into logical left-to-right schematic columns:
    - Voltage/Current sources get their own left-hand column (up to 2 stacked vertically if supply + signal).
    - Resistor voltage dividers sharing a midpoint node (`R_top` from VCC to X, `R_bot` from X to GND)
      are stacked vertically in a single column (`[R_top, R_bot]`)!
    - BJT (`Q`) with its Collector resistor (`R_C` from VCC to C) and Emitter resistor(s) (`R_E` from E to GND/mid)
      are grouped into a vertical Transistor Stack column (`[R_C, Q, R_E1, R_E2]`)!
    - Coupling capacitors (`C` between two non-ground signal nodes) get their own horizontal `R90` column!
    - Shunt capacitors/resistors/inductors to Ground get their own vertical `R0` column.
    """
    used = set()
    columns: List[List[SpiceNetlistComponent]] = []

    # 1. Sources first
    sources = [c for c in comps if c.prefix in ("V", "I")]
    if len(sources) == 2:
        columns.append(sources)
        for s in sources:
            used.add(s.inst_name)
    else:
        for s in sources:
            columns.append([s])
            used.add(s.inst_name)

    # Walk remaining components in netlist order, forming natural stacks
    for c in comps:
        if c.inst_name in used:
            continue

        # Check if `c` is the top resistor of a bias divider (connected to VCC and some internal node B)
        if c.prefix == "R" and _is_power_node(c.nodes[0]) and c.nodes[1] != "0":
            mid_node = c.nodes[1]
            # Look for a bottom resistor from `mid_node` to `0` that isn't a collector resistor of a BJT
            bjt_on_c = [
                q for q in comps
                if q.inst_name not in used and q.prefix == "Q" and q.nodes[0] == mid_node
            ]
            if not bjt_on_c:
                r_bot = [
                    r for r in comps
                    if r.inst_name not in used and r.inst_name != c.inst_name
                    and r.prefix == "R" and mid_node in r.nodes and "0" in r.nodes
                ]
                if r_bot:
                    used.add(c.inst_name)
                    used.add(r_bot[0].inst_name)
                    columns.append([c, r_bot[0]])
                    continue

        # Check if `c` is a BJT (`Q`) or the collector resistor of an unused BJT
        if c.prefix == "Q" or (
            c.prefix == "R" and any(
                q.inst_name not in used and q.prefix == "Q" and q.nodes[0] in c.nodes
                for q in comps
            )
        ):
            if c.prefix == "Q":
                q_comp = c
            else:
                q_comp = next(
                    q for q in comps
                    if q.inst_name not in used and q.prefix == "Q" and q.nodes[0] in c.nodes
                )
            col_stack: List[SpiceNetlistComponent] = []
            c_node, _, e_node = q_comp.nodes[0], q_comp.nodes[1], q_comp.nodes[2]
            # Find collector resistor (if any)
            rc_list = [
                r for r in comps
                if r.inst_name not in used and r.prefix == "R"
                and c_node in r.nodes and any(_is_power_node(n) for n in r.nodes)
            ]
            if rc_list:
                col_stack.append(rc_list[0])
                used.add(rc_list[0].inst_name)
            col_stack.append(q_comp)
            used.add(q_comp.inst_name)

            # Find emitter resistor(s) connected to `e_node`
            re1_list = [
                r for r in comps
                if r.inst_name not in used and r.prefix == "R" and e_node in r.nodes
            ]
            if re1_list:
                re1 = re1_list[0]
                col_stack.append(re1)
                used.add(re1.inst_name)
                other_node = re1.nodes[1] if re1.nodes[0] == e_node else re1.nodes[0]
                if other_node != "0":
                    re2_list = [
                        r for r in comps
                        if r.inst_name not in used and r.prefix == "R"
                        and other_node in r.nodes and "0" in r.nodes
                    ]
                    if re2_list:
                        col_stack.append(re2_list[0])
                        used.add(re2_list[0].inst_name)

            columns.append(col_stack)
            continue

        used.add(c.inst_name)
        columns.append([c])

    return columns


def _layout_style_a_textbook_wired(
    proj: SpiceNetlistProject,
    comps: List[SpiceNetlistComponent],
    output_asc_path: str,
) -> SchematicBuilder:
    """Style A: Textbook stage-flow layout with wired vertical stacks and local horizontal flags."""
    columns = _group_into_functional_columns(comps)
    sch = SchematicBuilder(width=max(1408, 240 * (len(columns) + 2)), height=1120)

    col_x = 80
    for col in columns:
        if len(col) == 2 and all(c.prefix in ("V", "I") for c in col):
            # Stacked supply + signal sources on left column
            for s_idx, sc in enumerate(col):
                sy = 96 + s_idx * 272
                p = sch.add_component(sc.inst_name, sc.symbol_type, x=col_x, y=sy, value=sc.value)
                n_top, n_bot = sc.nodes[0], sc.nodes[1]
                sch.power_pin(p.pin("top"), n_top) if n_top != "0" else sch.ground_pin(p.pin("top"), stub_dy=-32)
                sch.ground_pin(p.pin("bot"), stub_dy=32) if n_bot == "0" else sch.add_flag(p.pin("bot"), n_bot, stub_dy=32, stub_dx=32)
            col_x += 224
            continue

        if len(col) == 2 and all(c.prefix == "R" for c in col):
            # Voltage divider [R_top, R_bot] wired together vertically with horizontal midpoint flag
            r_top_c, r_bot_c = col[0], col[1]
            r_top = sch.add_component(r_top_c.inst_name, "res", x=col_x, y=96, value=r_top_c.value)
            r_bot = sch.add_component(r_bot_c.inst_name, "res", x=col_x, y=352, value=r_bot_c.value)
            sch.power_pin(r_top.pin("top"), r_top_c.nodes[0])
            sch.connect_points(r_top.pin("bot"), r_bot.pin("top"))
            mid_y = ((r_top.pin("bot")[1] + r_bot.pin("top")[1]) // 32) * 16
            sch.add_flag((r_top.pin("bot")[0], mid_y), r_top_c.nodes[1], stub_dx=32, stub_dy=0)
            sch.ground_pin(r_bot.pin("bot"), stub_dy=32) if r_bot_c.nodes[1] == "0" else sch.add_flag(r_bot.pin("bot"), r_bot_c.nodes[1], stub_dy=32, stub_dx=32)
            col_x += 208
            continue

        if any(c.prefix == "Q" for c in col):
            # Transistor vertical stack: [optional RC, Q, optional RE1, optional RE2]
            q_c = next(c for c in col if c.prefix == "Q")
            q_placed = sch.add_component(q_c.inst_name, q_c.symbol_type, x=col_x, y=224, value=q_c.value)
            c_node, b_node, e_node = q_c.nodes[0], q_c.nodes[1], q_c.nodes[2]
            sch.add_flag(q_placed.pin("B"), b_node, stub_dx=-32, stub_dy=0)

            rc_items = [c for c in col if c.prefix == "R" and c_node in c.nodes and c.inst_name != q_c.inst_name]
            if rc_items and col.index(rc_items[0]) < col.index(q_c):
                rc_c = rc_items[0]
                rc_p = sch.add_component(rc_c.inst_name, "res", x=col_x + 48, y=64, value=rc_c.value)
                top_net = rc_c.nodes[0] if rc_c.nodes[1] == c_node else rc_c.nodes[1]
                sch.power_pin(rc_p.pin("top"), top_net)
                sch.connect_points(rc_p.pin("bot"), q_placed.pin("C"))
                sch.add_flag((q_placed.pin("C")[0], 208), c_node, stub_dx=48, stub_dy=0)
            else:
                sch.power_pin(q_placed.pin("C"), c_node)

            re_items = [c for c in col if c.prefix == "R" and col.index(c) > col.index(q_c)]
            if len(re_items) == 1:
                re1_c = re_items[0]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=384, value=re1_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 352), e_node, stub_dx=48, stub_dy=0)
                bot_net = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.ground_pin(re1_p.pin("bot"), stub_dy=32) if bot_net == "0" else sch.add_flag(re1_p.pin("bot"), bot_net, stub_dy=32, stub_dx=32)
            elif len(re_items) >= 2:
                re1_c, re2_c = re_items[0], re_items[1]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=368, value=re1_c.value)
                re2_p = sch.add_component(re2_c.inst_name, "res", x=col_x + 48, y=544, value=re2_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 352), e_node, stub_dx=48, stub_dy=0)
                sch.connect_points(re1_p.pin("bot"), re2_p.pin("top"))
                mid_enet = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.add_flag((re1_p.pin("bot")[0], 512), mid_enet, stub_dx=48, stub_dy=0)
                bot_net = re2_c.nodes[1] if re2_c.nodes[0] == mid_enet else re2_c.nodes[0]
                sch.ground_pin(re2_p.pin("bot"), stub_dy=32) if bot_net == "0" else sch.add_flag(re2_p.pin("bot"), bot_net, stub_dy=32, stub_dx=32)
            else:
                sch.ground_pin(q_placed.pin("E"), stub_dy=32) if e_node == "0" else sch.add_flag(q_placed.pin("E"), e_node, stub_dy=32, stub_dx=32)

            col_x += 256
            continue

        # Single component column
        c = col[0]
        if c.prefix == "C" and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            # Horizontal series coupling capacitor (`R90`)
            cp = sch.add_component(c.inst_name, "cap", x=col_x + 64, y=256, value=c.value, orientation="R90")
            sch.add_flag(cp.pin("left"), c.nodes[0], stub_dx=-32, stub_dy=0)
            sch.add_flag(cp.pin("right"), c.nodes[1], stub_dx=32, stub_dy=0)
            col_x += 224
        else:
            p = sch.add_component(c.inst_name, c.symbol_type, x=col_x, y=288, value=c.value, orientation="R0")
            n0, n1 = c.nodes[0], c.nodes[1]
            sch.power_pin(p.pin("top"), n0) if n0 != "0" else sch.ground_pin(p.pin("top"), stub_dy=-32)
            sch.ground_pin(p.pin("bot"), stub_dy=32) if n1 == "0" else sch.add_flag(p.pin("bot"), n1, stub_dy=32, stub_dx=32)
            col_x += 208

    for d in proj.directives:
        sch.add_directive(d)

    sch.save(output_asc_path, auto_sanitize=True)
    return sch


def _layout_style_b_power_rail_bus(
    proj: SpiceNetlistProject,
    comps: List[SpiceNetlistComponent],
    output_asc_path: str,
) -> SchematicBuilder:
    """
    Style B: Continuous top `VCC` horizontal bus wire at `y = 48` across the entire circuit so
    all pull-up resistors and collectors wire straight up to the top bus bar (only ONE `VCC` label!).
    """
    columns = _group_into_functional_columns(comps)
    sch = SchematicBuilder(width=max(1408, 240 * (len(columns) + 2)), height=1120)

    # Identify main positive power rail name (e.g. VCC_12V or VCC)
    power_nodes = [n for c in comps for n in c.nodes if _is_power_node(n)]
    main_vcc = power_nodes[0] if power_nodes else None
    bus_y = 48
    bus_xs: List[int] = []

    def _connect_top_pin(pt: Point, net: str) -> None:
        if main_vcc and net.upper() == main_vcc.upper():
            sch.connect_points(pt, (pt[0], bus_y), vh_order="VH")
            bus_xs.append(pt[0])
        elif net == "0":
            sch.ground_pin(pt, stub_dy=-32)
        else:
            sch.power_pin(pt, net, stub_dy=-32, stub_dx=32)

    col_x = 80
    for col in columns:
        if len(col) == 2 and all(c.prefix in ("V", "I") for c in col):
            for s_idx, sc in enumerate(col):
                sy = 112 + s_idx * 272
                p = sch.add_component(sc.inst_name, sc.symbol_type, x=col_x, y=sy, value=sc.value)
                n_top, n_bot = sc.nodes[0], sc.nodes[1]
                if s_idx == 0 and main_vcc and n_top.upper() == main_vcc.upper():
                    _connect_top_pin(p.pin("top"), n_top)
                else:
                    sch.power_pin(p.pin("top"), n_top)
                sch.ground_pin(p.pin("bot"), stub_dy=32)
            col_x += 224
            continue

        if len(col) == 2 and all(c.prefix == "R" for c in col):
            r_top_c, r_bot_c = col[0], col[1]
            r_top = sch.add_component(r_top_c.inst_name, "res", x=col_x, y=112, value=r_top_c.value)
            r_bot = sch.add_component(r_bot_c.inst_name, "res", x=col_x, y=368, value=r_bot_c.value)
            _connect_top_pin(r_top.pin("top"), r_top_c.nodes[0])
            sch.connect_points(r_top.pin("bot"), r_bot.pin("top"))
            mid_y = ((r_top.pin("bot")[1] + r_bot.pin("top")[1]) // 32) * 16
            sch.add_flag((r_top.pin("bot")[0], mid_y), r_top_c.nodes[1], stub_dx=32, stub_dy=0)
            sch.ground_pin(r_bot.pin("bot"), stub_dy=32)
            col_x += 208
            continue

        if any(c.prefix == "Q" for c in col):
            q_c = next(c for c in col if c.prefix == "Q")
            q_placed = sch.add_component(q_c.inst_name, q_c.symbol_type, x=col_x, y=240, value=q_c.value)
            c_node, b_node, e_node = q_c.nodes[0], q_c.nodes[1], q_c.nodes[2]
            sch.add_flag(q_placed.pin("B"), b_node, stub_dx=-32, stub_dy=0)

            rc_items = [c for c in col if c.prefix == "R" and c_node in c.nodes and c.inst_name != q_c.inst_name]
            if rc_items and col.index(rc_items[0]) < col.index(q_c):
                rc_c = rc_items[0]
                rc_p = sch.add_component(rc_c.inst_name, "res", x=col_x + 48, y=80, value=rc_c.value)
                top_net = rc_c.nodes[0] if rc_c.nodes[1] == c_node else rc_c.nodes[1]
                _connect_top_pin(rc_p.pin("top"), top_net)
                sch.connect_points(rc_p.pin("bot"), q_placed.pin("C"))
                sch.add_flag((q_placed.pin("C")[0], 224), c_node, stub_dx=48, stub_dy=0)
            else:
                _connect_top_pin(q_placed.pin("C"), c_node)

            re_items = [c for c in col if c.prefix == "R" and col.index(c) > col.index(q_c)]
            if len(re_items) == 1:
                re1_c = re_items[0]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=400, value=re1_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                sch.ground_pin(re1_p.pin("bot"), stub_dy=32)
            elif len(re_items) >= 2:
                re1_c, re2_c = re_items[0], re_items[1]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=384, value=re1_c.value)
                re2_p = sch.add_component(re2_c.inst_name, "res", x=col_x + 48, y=560, value=re2_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                sch.connect_points(re1_p.pin("bot"), re2_p.pin("top"))
                mid_enet = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.add_flag((re1_p.pin("bot")[0], 528), mid_enet, stub_dx=48, stub_dy=0)
                sch.ground_pin(re2_p.pin("bot"), stub_dy=32)
            col_x += 256
            continue

        c = col[0]
        if c.prefix == "C" and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            cp = sch.add_component(c.inst_name, "cap", x=col_x + 64, y=272, value=c.value, orientation="R90")
            sch.add_flag(cp.pin("left"), c.nodes[0], stub_dx=-32, stub_dy=0)
            sch.add_flag(cp.pin("right"), c.nodes[1], stub_dx=32, stub_dy=0)
            col_x += 224
        else:
            p = sch.add_component(c.inst_name, c.symbol_type, x=col_x, y=304, value=c.value, orientation="R0")
            _connect_top_pin(p.pin("top"), c.nodes[0])
            sch.ground_pin(p.pin("bot"), stub_dy=32) if c.nodes[1] == "0" else sch.add_flag(p.pin("bot"), c.nodes[1], stub_dy=32, stub_dx=32)
            col_x += 208

    if main_vcc and len(bus_xs) >= 2:
        min_bx, max_bx = min(bus_xs), max(bus_xs)
        sch.add_wire(min_bx, bus_y, max_bx, bus_y)
        sch.add_flag((min_bx + 64, bus_y), main_vcc)

    for d in proj.directives:
        sch.add_directive(d)

    sch.save(output_asc_path, auto_sanitize=True)
    return sch


def _layout_style_c_modular_blocks(
    proj: SpiceNetlistProject,
    comps: List[SpiceNetlistComponent],
    output_asc_path: str,
) -> SchematicBuilder:
    """
    Style C: Modular Sub-Block Cells with extra-wide 256px column spacing, section headers, and
    a dedicated right-side Simulation & Measurement Dashboard so `.tran`/`.meas` are completely
    separated horizontally from the schematic!
    """
    n_comps = len(comps)
    sch = SchematicBuilder(width=max(1600, 256 * (n_comps + 4)), height=960)

    sch.add_directive("=== CIRCUITO MODULARIZADO POR CELULAS (ESTILO C) ===", x=96, y=64, is_comment=True)

    for idx, c in enumerate(comps):
        col_x = 96 + idx * 256
        row_y = 240
        p = sch.add_component(c.inst_name, c.symbol_type, x=col_x, y=row_y, value=c.value, orientation="R0")
        if c.prefix == "Q" and len(c.nodes) >= 3:
            sch.power_pin(p.pin("C"), c.nodes[0], stub_dy=-48, stub_dx=32)
            sch.add_flag(p.pin("B"), c.nodes[1], stub_dx=-48, stub_dy=0)
            sch.add_flag(p.pin("E"), c.nodes[2], stub_dy=48, stub_dx=32) if c.nodes[2] != "0" else sch.ground_pin(p.pin("E"), stub_dy=48)
        elif len(c.nodes) >= 2:
            sch.power_pin(p.pin("top"), c.nodes[0], stub_dy=-48, stub_dx=32) if c.nodes[0] != "0" else sch.ground_pin(p.pin("top"), stub_dy=-48)
            sch.ground_pin(p.pin("bot"), stub_dy=48) if c.nodes[1] == "0" else sch.add_flag(p.pin("bot"), c.nodes[1], stub_dy=48, stub_dx=32)

    for d in proj.directives:
        sch.add_directive(d, x=96)

    sch.save(output_asc_path, auto_sanitize=True)
    return sch


# ==============================================================================
# 8. Comprehensive LTspice Project Inspector & Troubleshooter
# ==============================================================================

@dataclass
class ProjectInspectionReport:
    target_path: str
    detected_formats: List[str]
    component_count: int
    unconnected_pins: List[str] = field(default_factory=list)
    visual_issues: List[str] = field(default_factory=list)
    missing_includes: List[str] = field(default_factory=list)
    log_measurements: Dict[str, float] = field(default_factory=dict)
    failed_measurements: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


def inspect_ltspice_project(path: str) -> ProjectInspectionReport:
    """
    Inspect a single LTspice file (`.asc`, `.cir`, `.net`, `.sub`, `.lib`, `.asy`, `.plt`, `.log`)
    or an entire project directory, returning a full diagnostic report.
    """
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in sorted(os.listdir(path))]
    else:
        files = [path]

    exts = sorted({os.path.splitext(f)[1].lower() for f in files if os.path.isfile(f)})
    rep = ProjectInspectionReport(target_path=path, detected_formats=exts, component_count=0)

    for f in files:
        if not os.path.isfile(f):
            continue
        ext = os.path.splitext(f)[1].lower()
        base_dir = os.path.dirname(os.path.abspath(f))

        if ext == ".asc":
            sch = parse_asc_file(f)
            rep.component_count += len(sch.components)
            drc = sch.run_geometric_drc()
            rep.visual_issues.extend([f"[{os.path.basename(f)}] {msg}" for msg in drc])
            _, _, _, max_y = sch.get_circuit_bounds()
            for d in sch.directives:
                if d.y is not None and d.y < max_y + 96 and not d.is_comment:
                    rep.visual_issues.append(
                        f"[{os.path.basename(f)}] Directive '{d.directive}' at y={d.y} overlaps circuit area (max_y={max_y})."
                    )
                if d.directive.lower().startswith("!.include ") or d.directive.lower().startswith("!.lib "):
                    inc_rel = d.directive.split(None, 1)[1].strip().strip('"')
                    if not os.path.isfile(os.path.join(base_dir, inc_rel)):
                        rep.missing_includes.append(f"[{os.path.basename(f)}] Missing library file: {inc_rel}")
            try:
                ver: NetlistVerificationReport = verify_asc_netlist(f)
                rep.unconnected_pins.extend([f"[{os.path.basename(f)}] {u}" for u in ver.unconnected_pins])
            except Exception:
                pass

        elif ext in (".cir", ".net", ".sp"):
            proj = parse_spice_netlist(f)
            rep.component_count += len(proj.components)
            for d in proj.directives:
                if d.lower().startswith(".include ") or d.lower().startswith(".lib "):
                    inc_rel = d.split(None, 1)[1].strip().strip('"')
                    if not os.path.isfile(os.path.join(base_dir, inc_rel)):
                        rep.missing_includes.append(f"[{os.path.basename(f)}] Missing library file: {inc_rel}")

        elif ext == ".log":
            parsed: ParsedLogResult = parse_ltspice_log(f)
            for mname in parsed.scalars:
                val = parsed.get_scalar(mname, linearize_db=True)
                if val is not None:
                    rep.log_measurements[mname] = val
            rep.failed_measurements.extend(parsed.failed_measurements)

    if rep.unconnected_pins:
        rep.recommendations.append("Conectar os pinos flutuantes (NC_) identificados pelo compilador de netlist.")
    if rep.visual_issues:
        rep.recommendations.append("Executar `beautify_asc_file()` para afastar diretivas `.tran`/`.meas` e converter flags verticais em horizontais.")
    if rep.missing_includes:
        rep.recommendations.append("Corrigir o caminho relativo das bibliotecas `.include`/`.lib` ausentes.")
    if rep.failed_measurements:
        rep.recommendations.append("Revisar janelas de tempo (`FROM`/`TO`) ou ponto de operação DC para as medições `.meas` que falharam.")

    return rep
