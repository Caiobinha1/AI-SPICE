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

    wired_nets = wire_signal_nets_on_schematic(sch)
    if wired_nets > 0:
        fixes.append(f"Physically wired {wired_nets} signal nets with orthogonal Manhattan wires.")

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
    symbol_override: Optional[str] = None

    @property
    def symbol_type(self) -> str:
        if self.symbol_override:
            return self.symbol_override
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
            vu = self.value.upper()
            pnp_prefixes = ("PNP", "2N3906", "2N2907", "BC55", "FZT9", "TIP30", "TIP32", "TIP42", "BD136", "BD138", "BD140", "MJE2955", "2SA", "2SB")
            return "pnp" if any(vu.startswith(px) or px in vu for px in pnp_prefixes) else "npn"
        if p == "X" and len(self.nodes) == 5:
            return f"Opamps\\{self.value}"
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
        base_dir = os.path.dirname(os.path.abspath(path_or_text))
    else:
        text = path_or_text
        src_path = None
        base_dir = None

    raw_lines = text.splitlines()
    title = raw_lines[0].strip() if raw_lines else "* SPICE Netlist"
    proj = SpiceNetlistProject(title=title, source_path=src_path)

    pnp_models = set()
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
        if ";" in line:
            line = line.split(";", 1)[0].strip()
        upper = line.upper()
        if upper == ".END" or upper == ".BACKANNO":
            continue
        if upper.startswith(".MODEL "):
            parts = upper.split()
            if len(parts) >= 3 and "PNP" in parts[2]:
                pnp_models.add(parts[1])
        if base_dir and (upper.startswith(".INCLUDE ") or upper.startswith(".LIB ")):
            inc_file = line.split(None, 1)[1].strip().strip('"')
            inc_path = os.path.join(base_dir, inc_file)
            if os.path.isfile(inc_path):
                try:
                    inc_txt = _read_text_auto_encoding(inc_path)
                    for iline in inc_txt.splitlines():
                        iu = iline.strip().upper()
                        if iu.startswith(".MODEL "):
                            iparts = iu.split()
                            if len(iparts) >= 3 and "PNP" in iparts[2]:
                                pnp_models.add(iparts[1])
                except Exception:
                    pass
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

    for comp in proj.components.values():
        if comp.prefix == "Q" and comp.value.upper() in pnp_models:
            comp.symbol_override = "pnp"

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

    wire_signal_nets_on_schematic(sch)
    sch.save(output_path or asc_path, auto_sanitize=True)
    return sch


# ==============================================================================
# 7. Orthogonal Signal Wire Router & Functional Stage Layout Engine
# ==============================================================================

def _is_power_node(node: str) -> bool:
    u = node.upper()
    return (
        u.startswith("VCC")
        or u.startswith("VDD")
        or u.startswith("VEE")
        or u.startswith("VSS")
        or u in ("VP", "VN", "V+", "V-", "+12V", "-12V", "+5V", "-5V", "+15V", "-15V")
    )


def wire_signal_nets_on_schematic(sch: SchematicBuilder) -> int:
    """
    Connect all non-power signal nets (`net not in ('0', 'GND')` and not `_is_power_node(net)`)
    using physical orthogonal Manhattan `WIRE` segments on the 16-pixel grid, while keeping
    power rails (`VCC`, `VEE`, `VDD`, `VSS`) and Ground (`0`) on local flags.

    Guarantees:
    - No routed wire ever touches a component pin, flag, or wire endpoint belonging to another net.
    - No routed wire ever overlaps collinearly with a wire segment of another net.
    - No routed wire cuts across the interior body/label box of a component.
    - Redundant duplicate `FLAG` labels on wired signal nets are removed so only ONE probe
      label remains per continuous signal wire.
    Returns the number of signal nets wired.
    """
    sch.sanitize_visual_layout()

    # 1. Build initial connectivity clusters to know which net owns each existing wire/pin/flag
    adj: Dict[Point, Set[Point]] = {}

    def _add_edge(a: Point, b: Point) -> None:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for w in sch.wires:
        if w.x1 == w.x2:
            y_lo, y_hi = min(w.y1, w.y2), max(w.y1, w.y2)
            pts = [(w.x1, y) for y in range(y_lo, y_hi + 16, 16)]
        else:
            x_lo, x_hi = min(w.x1, w.x2), max(w.x1, w.x2)
            pts = [(x, w.y1) for x in range(x_lo, x_hi + 16, 16)]
        for i in range(len(pts) - 1):
            _add_edge(pts[i], pts[i + 1])

    pin_pts: Set[Point] = set()
    body_boxes: List[Tuple[int, int, int, int, Set[Point]]] = []
    for comp in sch.components.values():
        c_pins = set(comp.unique_pin_points())
        pin_pts.update(c_pins)
        for p in c_pins:
            adj.setdefault(p, set())
        # Protective bounding box around component symbol & right-side text labels
        sym_l = comp.symbol_type.lower()
        if "opamp" in sym_l or "lt1360" in sym_l:
            bx1, by1, bx2, by2 = comp.x - 32, comp.y - 32, comp.x + 96, comp.y + 48
        elif sym_l in ("npn", "pnp"):
            if comp.orientation == "M180":
                bx1, by1, bx2, by2 = comp.x, comp.y - 96, comp.x + 112, comp.y
            else:
                bx1, by1, bx2, by2 = comp.x, comp.y, comp.x + 112, comp.y + 96
        elif comp.orientation == "R90":
            bx1, by1, bx2, by2 = comp.x - 96, comp.y - 16, comp.x + 16, comp.y + 48
        else:
            bx1, by1, bx2, by2 = comp.x - 16, comp.y, comp.x + 96, comp.y + 96
        body_boxes.append((bx1, by1, bx2, by2, c_pins))

    for fl in sch.flags:
        adj.setdefault((fl.x, fl.y), set())

    # Connected components BFS
    visited: Set[Point] = set()
    point_to_net: Dict[Point, str] = {}
    horiz_segs: List[Tuple[int, int, int, str]] = []  # (y, x_min, x_max, net)
    vert_segs: List[Tuple[int, int, int, str]] = []   # (x, y_min, y_max, net)

    flag_at_pt: Dict[Point, str] = {(fl.x, fl.y): fl.net_name for fl in sch.flags}
    anon_idx = 0
    for start_pt in list(adj.keys()):
        if start_pt in visited:
            continue
        comp_pts: Set[Point] = set()
        queue = [start_pt]
        visited.add(start_pt)
        net_label: Optional[str] = None
        while queue:
            cur = queue.pop()
            comp_pts.add(cur)
            if cur in flag_at_pt:
                net_label = flag_at_pt[cur]
            for nxt in adj.get(cur, ()):
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        if net_label is None:
            anon_idx += 1
            net_label = f"__ANON_{anon_idx}"
        for pt in comp_pts:
            point_to_net[pt] = net_label

    for w in sch.wires:
        wnet = point_to_net.get((w.x1, w.y1), "")
        if w.y1 == w.y2:
            horiz_segs.append((w.y1, min(w.x1, w.x2), max(w.x1, w.x2), wnet))
        else:
            vert_segs.append((w.x1, min(w.y1, w.y2), max(w.y1, w.y2), wnet))

    # Critical points that a wire of another net may NEVER touch (even when crossing perpendicularly):
    # component pins, flags, and wire endpoints/corners!
    critical_pts_by_net: Dict[Point, str] = {}
    for p in pin_pts:
        critical_pts_by_net[p] = point_to_net.get(p, "")
    for fl in sch.flags:
        critical_pts_by_net[(fl.x, fl.y)] = fl.net_name
    for w in sch.wires:
        critical_pts_by_net[(w.x1, w.y1)] = point_to_net.get((w.x1, w.y1), "")
        critical_pts_by_net[(w.x2, w.y2)] = point_to_net.get((w.x2, w.y2), "")

    def _seg_valid(p1: Point, p2: Point, net: str) -> Tuple[bool, int]:
        """Return `(is_valid, penalty_cost)` for an orthogonal segment `p1 -> p2` of `net`."""
        x1, y1 = p1
        x2, y2 = p2
        if x1 != x2 and y1 != y2:
            return (False, 999999)
        if x1 == x2 and y1 == y2:
            return (True, 0)

        # Endpoint check: endpoints must not lie on any wire/pin of another net
        for ep in (p1, p2):
            owner = point_to_net.get(ep)
            if owner is not None and owner != net:
                return (False, 999999)

        penalty = abs(x2 - x1) + abs(y2 - y1)

        if y1 == y2:
            y = y1
            x_lo, x_hi = min(x1, x2), max(x1, x2)
            # Check collinear overlap with horizontal segments of other nets
            for hy, hx1, hx2, hnet in horiz_segs:
                if hy == y and hnet != net and max(x_lo, hx1) <= min(x_hi, hx2):
                    return (False, 999999)
            # Check critical points & component boxes along the segment
            for x in range(x_lo, x_hi + 16, 16):
                pt = (x, y)
                c_owner = critical_pts_by_net.get(pt)
                if c_owner is not None and c_owner != net:
                    return (False, 999999)
                if pt not in (p1, p2):
                    for bx1, by1, bx2, by2, c_pins in body_boxes:
                        if bx1 < x < bx2 and by1 < y < by2 and pt not in c_pins:
                            return (False, 999999)
                # High penalty for perpendicular wire crossover so global feedback loops route cleanly around stages
                p_owner = point_to_net.get(pt)
                if p_owner is not None and p_owner != net:
                    penalty += 1400
        else:
            x = x1
            y_lo, y_hi = min(y1, y2), max(y1, y2)
            for vx, vy1, vy2, vnet in vert_segs:
                if vx == x and vnet != net and max(y_lo, vy1) <= min(y_hi, vy2):
                    return (False, 999999)
            for y in range(y_lo, y_hi + 16, 16):
                pt = (x, y)
                c_owner = critical_pts_by_net.get(pt)
                if c_owner is not None and c_owner != net:
                    return (False, 999999)
                if pt not in (p1, p2):
                    for bx1, by1, bx2, by2, c_pins in body_boxes:
                        if bx1 < x < bx2 and by1 < y < by2 and pt not in c_pins:
                            return (False, 999999)
                p_owner = point_to_net.get(pt)
                if p_owner is not None and p_owner != net:
                    penalty += 1400

        return (True, penalty)

    def _commit_path(pts_chain: List[Point], net: str) -> None:
        for i in range(len(pts_chain) - 1):
            a, b = pts_chain[i], pts_chain[i + 1]
            if a == b:
                continue
            sch.add_wire(a[0], a[1], b[0], b[1])
            critical_pts_by_net[a] = net
            critical_pts_by_net[b] = net
            if a[1] == b[1]:
                x_lo, x_hi = min(a[0], b[0]), max(a[0], b[0])
                horiz_segs.append((a[1], x_lo, x_hi, net))
                for x in range(x_lo, x_hi + 16, 16):
                    point_to_net[(x, a[1])] = net
            else:
                y_lo, y_hi = min(a[1], b[1]), max(a[1], b[1])
                vert_segs.append((a[0], y_lo, y_hi, net))
                for y in range(y_lo, y_hi + 16, 16):
                    point_to_net[(a[0], y)] = net

    def _find_ortho_route(p1: Point, p2: Point, net: str, track_idx: int) -> Optional[List[Point]]:
        x1, y1 = p1
        x2, y2 = p2
        manhattan_d = abs(x2 - x1) + abs(y2 - y1)
        # 0-bend
        if x1 == x2 or y1 == y2:
            ok, c0 = _seg_valid(p1, p2, net)
            if ok and c0 <= manhattan_d:
                return [p1, p2]
        # 1-bend L-route
        best_1: Optional[Tuple[int, List[Point]]] = None
        for corner in ((x2, y1), (x1, y2)):
            if point_to_net.get(corner) not in (None, net):
                continue
            ok1, c1 = _seg_valid(p1, corner, net)
            ok2, c2 = _seg_valid(corner, p2, net)
            if ok1 and ok2:
                cost = c1 + c2
                if best_1 is None or cost < best_1[0]:
                    best_1 = (cost, [p1, corner, p2])
        # Return 1-bend immediately only if it has ZERO wire crossovers
        if best_1 is not None and best_1[0] <= manhattan_d:
            return best_1[1]

        # 2-bend Z/U-route via candidate x_mid or y_mid
        best_2: Optional[Tuple[int, List[Point]]] = best_1
        mid_x = ((x1 + x2) // 32) * 16
        cand_xs = sorted(
            {x for x in range(max(16, min(x1, x2) - 160), max(x1, x2) + 176, 16)},
            key=lambda x: abs(x - mid_x),
        )
        for xm in cand_xs:
            c_a, c_b = (xm, y1), (xm, y2)
            if point_to_net.get(c_a) not in (None, net) or point_to_net.get(c_b) not in (None, net):
                continue
            ok1, k1 = _seg_valid(p1, c_a, net)
            if not ok1:
                continue
            ok2, k2 = _seg_valid(c_a, c_b, net)
            if not ok2:
                continue
            ok3, k3 = _seg_valid(c_b, p2, net)
            if ok3:
                cost = k1 + k2 + k3
                if best_2 is None or cost < best_2[0]:
                    best_2 = (cost, [p1, c_a, c_b, p2])
                if cost <= manhattan_d + 64:
                    break

        mid_y = ((y1 + y2) // 32) * 16
        _, min_cy, _, max_cy = sch.get_circuit_bounds()
        cand_ys = sorted(
            {
                y
                for y in list(range(max(16, min(y1, y2) - 160), min(max_cy + 160, max(y1, y2) + 560), 16))
                + [max(16, min_cy - 32 - track_idx * 16), ((max_cy + 48 + track_idx * 16) // 16) * 16]
                if y >= 16
            },
            key=lambda y: abs(y - mid_y),
        )
        for ym in cand_ys:
            c_a, c_b = (x1, ym), (x2, ym)
            if point_to_net.get(c_a) not in (None, net) or point_to_net.get(c_b) not in (None, net):
                continue
            ok1, k1 = _seg_valid(p1, c_a, net)
            if not ok1:
                continue
            ok2, k2 = _seg_valid(c_a, c_b, net)
            if not ok2:
                continue
            ok3, k3 = _seg_valid(c_b, p2, net)
            if ok3:
                cost = k1 + k2 + k3
                if best_2 is None or cost < best_2[0]:
                    best_2 = (cost, [p1, c_a, c_b, p2])
                if cost <= manhattan_d + 64:
                    break

        if best_2 is not None:
            return best_2[1]

        # 3-bend fallback via escaped x and top/bottom track
        for dx1 in (0, 32, -32, 48, -48, 64, -64):
            for dx2 in (0, 32, -32, 48, -48, 64, -64):
                xe1, xe2 = x1 + dx1, x2 + dx2
                if xe1 < 16 or xe2 < 16:
                    continue
                for ym in cand_ys[:20]:
                    chain = [p1, (xe1, y1), (xe1, ym), (xe2, ym), (xe2, y2), p2]
                    valid_all = True
                    for idx_s in range(len(chain) - 1):
                        if chain[idx_s] == chain[idx_s + 1]:
                            continue
                        ok_s, _ = _seg_valid(chain[idx_s], chain[idx_s + 1], net)
                        if not ok_s:
                            valid_all = False
                            break
                    if valid_all:
                        return chain
        return None

    # Group flags by signal net
    flags_by_net: Dict[str, List[FlagLabel]] = {}
    for fl in sch.flags:
        if fl.net_name == "0" or _is_power_node(fl.net_name):
            continue
        flags_by_net.setdefault(fl.net_name, []).append(fl)

    # Sort signal nets by bounding-box span (local nets first, global feedback nets last)
    def _net_span(item: Tuple[str, List[FlagLabel]]) -> int:
        fls = item[1]
        if len(fls) < 2:
            return 0
        xs = [f.x for f in fls]
        ys = [f.y for f in fls]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    wired_count = 0
    for track_idx, (net_name, fl_list) in enumerate(sorted(flags_by_net.items(), key=_net_span)):
        if len(fl_list) < 2:
            continue
        # Order flags left-to-right, top-to-bottom
        fl_sorted = sorted(fl_list, key=lambda f: (f.x, f.y))
        all_connected = True
        for i in range(len(fl_sorted) - 1):
            p1 = (fl_sorted[i].x, fl_sorted[i].y)
            p2 = (fl_sorted[i + 1].x, fl_sorted[i + 1].y)
            route = _find_ortho_route(p1, p2, net_name, track_idx)
            if route is not None:
                _commit_path(route, net_name)
            else:
                all_connected = False
        if all_connected:
            wired_count += 1
            # Keep only ONE flag label on this continuous wired signal net!
            keep_fl = fl_sorted[0]
            for extra_fl in fl_sorted[1:]:
                if extra_fl in sch.flags:
                    sch.flags.remove(extra_fl)
            keep_fl.x, keep_fl.y = fl_sorted[0].x, fl_sorted[0].y

    return wired_count


def compile_netlist_to_asc(
    netlist_or_path: str,
    output_asc_path: str,
    style: str = "A_textbook_wired",
) -> SchematicBuilder:
    """
    Automatically compile any SPICE netlist (`.cir`/`.net`) into a clean, non-overlapping 16px-grid
    LTspice `.asc` schematic where:
    - Functional stages are organized left-to-right (Input AC Coupling -> Amplifier/Op-Amp Stages with
      local feedback networks below -> Bias & Push-Pull Output Stage -> Output Load & Zobel -> Dedicated
      Power & Decoupling Block).
    - ALL signal, bias, and feedback nets are connected by physical orthogonal `WIRE` segments (`NC_ = 0`),
      with a single net label per signal wire.
    - ONLY power supply rails (`VCC`, `VEE`, `VDD`, `VSS`) and Ground (`0`) use local flags.
    """
    proj = parse_spice_netlist(netlist_or_path)
    comps = list(proj.components.values())
    style_key = style.upper()

    if style_key.startswith("C"):
        return _layout_style_c_modular_blocks(proj, comps, output_asc_path)
    if style_key.startswith("B"):
        return _layout_style_b_power_rail_bus(proj, comps, output_asc_path)
    return _layout_style_a_textbook_wired(proj, comps, output_asc_path)


def _is_dc_power_supply(comp: SpiceNetlistComponent) -> bool:
    if comp.prefix != "V" or len(comp.nodes) < 2:
        return False
    n0, n1 = comp.nodes[0], comp.nodes[1]
    uval = comp.value.upper()
    if "SINE" in uval or "PULSE" in uval or "AC " in uval or "PWL" in uval:
        return False
    return (_is_power_node(n0) and n1 == "0") or (_is_power_node(n1) and n0 == "0")


def _is_decoupling_cap(comp: SpiceNetlistComponent) -> bool:
    if comp.prefix != "C" or len(comp.nodes) < 2:
        return False
    n0, n1 = comp.nodes[0], comp.nodes[1]
    return (_is_power_node(n0) and n1 == "0") or (_is_power_node(n1) and n0 == "0")


def _group_into_functional_columns(comps: List[SpiceNetlistComponent]) -> List[List[SpiceNetlistComponent]]:
    """
    Partition netlist components into logical left-to-right functional stage columns:
    1. Signal stimulus (`VIN`) + series coupling (`CIN`) + input termination (`RIN`).
    2. Op-Amp stages (`XU1`, `XU2`, ...) stacked with their local feedback & compensation passives directly below!
    3. Diode bias chain (`RBP`, `D1`, `D2`, `RBN`) stacked vertically in a single bias column!
    4. Complementary push-pull BJT stack (`Q1` NPN, `RE1`, `RE2`, `Q2` PNP) or single BJT stage (`RC`, `Q`, `RE`)!
    5. Output load (`RLOAD`) and Zobel / RC filter series-shunt branches (`RZ` + `CZ`)!
    6. Dedicated Power Supply & Decoupling Bank (`VCC`, `VEE`, `CDP`, `CDN`, `CBP`, `CBN`) at the far right!
    """
    used: Set[str] = set()
    columns: List[List[SpiceNetlistComponent]] = []

    # Separate Power Supply & Decoupling components for the final right-hand block
    power_supplies = [c for c in comps if _is_dc_power_supply(c)]
    decoupling_caps = [c for c in comps if _is_decoupling_cap(c)]
    has_active_opamps_or_pushpull = any(c.prefix == "X" for c in comps) or len([c for c in comps if c.prefix == "Q"]) >= 2

    if has_active_opamps_or_pushpull and (power_supplies or decoupling_caps):
        for pc in power_supplies + decoupling_caps:
            used.add(pc.inst_name)
    else:
        # For small 1-2 transistor or RLC circuits without op-amps/push-pull, keep sources on the left
        sources = [c for c in comps if c.prefix in ("V", "I")]
        if len(sources) == 2:
            columns.append(sources)
            for s in sources:
                used.add(s.inst_name)
        else:
            for s in sources:
                columns.append([s])
                used.add(s.inst_name)

    # Signal sources (if not already placed)
    for s in [c for c in comps if c.prefix in ("V", "I") and c.inst_name not in used]:
        columns.append([s])
        used.add(s.inst_name)
        # Check for series coupling cap + shunt input resistor right after VIN
        sig_node = s.nodes[0] if s.nodes[1] == "0" else s.nodes[1]
        c_in = [
            c for c in comps
            if c.inst_name not in used and c.prefix == "C" and sig_node in c.nodes and "0" not in c.nodes
        ]
        if c_in:
            cap_c = c_in[0]
            columns.append([cap_c])
            used.add(cap_c.inst_name)
            nin_node = cap_c.nodes[1] if cap_c.nodes[0] == sig_node else cap_c.nodes[0]
            r_in = [
                r for r in comps
                if r.inst_name not in used and r.prefix == "R" and nin_node in r.nodes and "0" in r.nodes
                and not any(r2 for r2 in comps if r2.inst_name not in used and r2.prefix == "R" and nin_node in r2.nodes and any(_is_power_node(n) for n in r2.nodes))
            ]
            if r_in:
                columns.append([r_in[0]])
                used.add(r_in[0].inst_name)

    # Nodes connected to transistors (Q) or >=4 components are stage/global nodes, not internal feedback midpoints
    q_connected_nodes = {n for c in comps if c.prefix == "Q" for n in c.nodes}
    node_degree: Dict[str, int] = {}
    for c in comps:
        for n in c.nodes:
            node_degree[n] = node_degree.get(n, 0) + 1

    def _is_internal_fb_node(n: str, out_n: str, in_p: str) -> bool:
        if n == "0" or _is_power_node(n) or n == out_n or n == in_p:
            return False
        if n.upper() in ("OUT", "IN") or n in q_connected_nodes or node_degree.get(n, 0) >= 4:
            return False
        return True

    # Op-Amp stages (`X` with 5 pins) + their feedback/compensation components directly below
    opamps = [c for c in comps if c.prefix == "X" and len(c.nodes) == 5 and c.inst_name not in used]
    for op in opamps:
        used.add(op.inst_name)
        in_p, in_m, _, _, out_n = op.nodes
        fb_col: List[SpiceNetlistComponent] = [op]
        direct_fb = [
            c for c in comps
            if c.inst_name not in used and c.prefix in ("R", "C")
            and in_m in c.nodes
        ]
        mid_fb_nodes: Set[str] = {in_m}
        for df in direct_fb:
            fb_col.append(df)
            used.add(df.inst_name)
            for n in df.nodes:
                if _is_internal_fb_node(n, out_n, in_p):
                    mid_fb_nodes.add(n)
        for _ in range(2):
            sec_fb = [
                c for c in comps
                if c.inst_name not in used and c.prefix in ("R", "C")
                and any(n in mid_fb_nodes for n in c.nodes)
                and not any(_is_power_node(n) for n in c.nodes)
                and not any(n in q_connected_nodes for n in c.nodes)
            ]
            for sf in sec_fb:
                fb_col.append(sf)
                used.add(sf.inst_name)
                for n in sf.nodes:
                    if _is_internal_fb_node(n, out_n, in_p):
                        mid_fb_nodes.add(n)
        columns.append(fb_col)

    # Diode Bias Column (`RBP`, `D1`, `D2`, `RBN`) if diodes are present
    diodes = [c for c in comps if c.prefix == "D" and c.inst_name not in used]
    if len(diodes) >= 2:
        d_nodes = {n for d in diodes for n in d.nodes}
        bias_res = [
            r for r in comps
            if r.inst_name not in used and r.prefix == "R"
            and any(n in d_nodes for n in r.nodes)
            and any(_is_power_node(n) for n in r.nodes)
        ]
        r_top_b = [r for r in bias_res if any(n.upper().startswith(("VCC", "VDD", "VP", "+")) for n in r.nodes)]
        r_bot_b = [r for r in bias_res if r not in r_top_b]
        bias_col: List[SpiceNetlistComponent] = []
        for r in r_top_b:
            bias_col.append(r)
            used.add(r.inst_name)
        for d in diodes:
            bias_col.append(d)
            used.add(d.inst_name)
        for r in r_bot_b:
            bias_col.append(r)
            used.add(r.inst_name)
        columns.append(bias_col)

    # Complementary Push-Pull BJT Column (`Q_npn`, `RE1`, `RE2`, `Q_pnp`) if both NPN and PNP exist
    rem_qs = [c for c in comps if c.prefix == "Q" and c.inst_name not in used]
    npns = [q for q in rem_qs if q.symbol_type == "npn"]
    pnps = [q for q in rem_qs if q.symbol_type == "pnp"]
    if len(npns) == 1 and len(pnps) == 1:
        q_n, q_p = npns[0], pnps[0]
        en_node, ep_node = q_n.nodes[2], q_p.nodes[2]
        re_n = [r for r in comps if r.inst_name not in used and r.prefix == "R" and en_node in r.nodes]
        re_p = [r for r in comps if r.inst_name not in used and r.prefix == "R" and ep_node in r.nodes]
        pp_col: List[SpiceNetlistComponent] = [q_n]
        used.add(q_n.inst_name)
        for r in re_n:
            pp_col.append(r)
            used.add(r.inst_name)
        for r in re_p:
            if r.inst_name not in used:
                pp_col.append(r)
                used.add(r.inst_name)
        pp_col.append(q_p)
        used.add(q_p.inst_name)
        columns.append(pp_col)

    # Walk remaining signal components, forming natural dividers, BJT stacks, or series-shunt RC/Zobel branches
    for c in comps:
        if c.inst_name in used:
            continue

        # Resistor divider (`R_top` from VCC to mid, `R_bot` from mid to 0)
        if c.prefix == "R" and _is_power_node(c.nodes[0]) and c.nodes[1] != "0":
            mid_node = c.nodes[1]
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

        # Standard BJT stack (`[RC, Q, RE1, RE2]`)
        if c.prefix == "Q" or (
            c.prefix == "R" and any(
                q.inst_name not in used and q.prefix == "Q" and q.nodes[0] in c.nodes
                for q in comps
            )
        ):
            q_comp = c if c.prefix == "Q" else next(
                q for q in comps if q.inst_name not in used and q.prefix == "Q" and q.nodes[0] in c.nodes
            )
            col_stack: List[SpiceNetlistComponent] = []
            c_node, _, e_node = q_comp.nodes[0], q_comp.nodes[1], q_comp.nodes[2]
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

        # Series R + C to Ground (e.g. Zobel network `RZ` from `OUT` to `ZOBEL` + `CZ` from `ZOBEL` to `0`)
        if c.prefix == "R" and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            n_a, n_b = c.nodes[0], c.nodes[1]
            c_zob = [
                cz for cz in comps
                if cz.inst_name not in used and cz.prefix == "C"
                and "0" in cz.nodes and (n_b in cz.nodes or n_a in cz.nodes)
            ]
            if c_zob:
                used.add(c.inst_name)
                used.add(c_zob[0].inst_name)
                columns.append([c, c_zob[0]])
                continue

        used.add(c.inst_name)
        columns.append([c])

    # Append dedicated Power Supply & Decoupling column(s) at the end if separated earlier
    if has_active_opamps_or_pushpull and (power_supplies or decoupling_caps):
        for ps in power_supplies:
            rail = ps.nodes[0] if ps.nodes[1] == "0" else ps.nodes[1]
            rail_caps = [dc for dc in decoupling_caps if rail in dc.nodes]
            columns.append([ps] + rail_caps)

    return columns


def _layout_style_a_textbook_wired(
    proj: SpiceNetlistProject,
    comps: List[SpiceNetlistComponent],
    output_asc_path: Optional[str] = None,
) -> SchematicBuilder:
    """
    Style A (Default): Functional stage-flow layout with FULL orthogonal signal wiring (`WIRE`)
    between components and stages, keeping ONLY power rails (`VCC`, `VEE`, `VDD`, `VSS`) and
    Ground (`0`) on wireless flags.
    """
    columns = _group_into_functional_columns(comps)
    sch = SchematicBuilder(width=max(1600, 288 * (len(columns) + 2)), height=1280)

    col_x = 96
    power_bank_x: Optional[int] = None
    power_bank_idx = 0
    for col in columns:
        # Case 1: Power Rail Bank (`[V_supply, C_dec1, C_dec2, ...]`) — stack dual rails (VCC / VEE) vertically!
        if len(col) >= 2 and _is_dc_power_supply(col[0]) and all(_is_decoupling_cap(c) for c in col[1:]):
            if power_bank_x is None:
                power_bank_x = col_x + 32
            bx_start = power_bank_x
            by_base = 176 + power_bank_idx * 368
            vs = col[0]
            rail_name = vs.nodes[0] if vs.nodes[1] == "0" else vs.nodes[1]
            sch.add_directive(
                f"=== ALIMENTACAO E DESACOPLAMENTO ({rail_name}) ===",
                x=bx_start,
                y=by_base - 96,
                is_comment=True,
            )
            rail_y = by_base - 48
            rail_xs: List[int] = []
            for idx_p, pc in enumerate(col):
                px = bx_start + idx_p * 160
                p = sch.add_component(pc.inst_name, pc.symbol_type, x=px, y=by_base, value=pc.value)
                top_pt = p.pin("top")
                bot_pt = p.pin("bot")
                sch.connect_points(top_pt, (top_pt[0], rail_y), vh_order="VH")
                rail_xs.append(top_pt[0])
                sch.ground_pin(bot_pt, stub_dy=32)
            if len(rail_xs) >= 2:
                sch.add_wire(min(rail_xs), rail_y, max(rail_xs), rail_y)
            sch.add_flag((rail_xs[0], rail_y), rail_name, stub_dx=32, stub_dy=0)
            power_bank_idx += 1
            col_x = max(col_x, bx_start + 160 * len(col) + 64)
            continue

        # Case 2: Stacked 2 sources on left column
        if len(col) == 2 and all(c.prefix in ("V", "I") for c in col):
            for s_idx, sc in enumerate(col):
                sy = 144 + s_idx * 288
                p = sch.add_component(sc.inst_name, sc.symbol_type, x=col_x, y=sy, value=sc.value)
                n_top, n_bot = sc.nodes[0], sc.nodes[1]
                sch.power_pin(p.pin("top"), n_top, stub_dy=-32, stub_dx=32) if n_top != "0" else sch.ground_pin(p.pin("top"), stub_dy=-32)
                sch.ground_pin(p.pin("bot"), stub_dy=32) if n_bot == "0" else sch.add_flag(p.pin("bot"), n_bot, stub_dy=32, stub_dx=32)
            col_x += 240
            continue

        # Case 3: Op-Amp Stage (`XU1` + structured feedback divider & compensation sub-columns below)
        if col[0].prefix == "X" and len(col[0].nodes) == 5:
            op_c = col[0]
            op_name = op_c.inst_name[1:] if op_c.inst_name.upper().startswith("XU") else op_c.inst_name
            sch.add_directive(f"=== ESTAGIO AMP-OP ({op_name}) ===", x=col_x, y=48, is_comment=True)
            op_p = sch.add_component(
                op_name,
                op_c.symbol_type,
                x=col_x + 144,
                y=192,
                value=op_c.value,
                prefix="X",
                allow_custom_symbol=True,
            )
            op_p.extra_attrs["SpiceModel"] = f"{op_c.value}.lib"
            op_p.extra_attrs["Value2"] = op_c.value

            in_p, in_m, v_pos, v_neg, out_n = op_c.nodes
            sch.add_flag(op_p.pin("1"), in_p, stub_dx=-48, stub_dy=0)
            sch.add_flag(op_p.pin("2"), in_m, stub_dx=-48, stub_dy=0)
            sch.power_pin(op_p.pin("3"), v_pos, stub_dy=-48, stub_dx=32)
            sch.power_pin(op_p.pin("4"), v_neg, stub_dy=48, stub_dx=32)
            sch.add_flag(op_p.pin("5"), out_n, stub_dx=48, stub_dy=0)

            fb_passives = list(col[1:])
            gnd_r = [c for c in fb_passives if c.prefix == "R" and "0" in c.nodes]
            div_chain: List[Tuple[SpiceNetlistComponent, str, str]] = []
            used_fb: Set[str] = set()
            if gnd_r:
                rg = gnd_r[0]
                used_fb.add(rg.inst_name)
                fb_junction = rg.nodes[0] if rg.nodes[1] == "0" else rg.nodes[1]
                div_chain.insert(0, (rg, fb_junction, "0"))
                cur_top_node = fb_junction
                for _ in range(2):
                    up_r = [
                        c for c in fb_passives
                        if c.inst_name not in used_fb and c.prefix == "R"
                        and cur_top_node in c.nodes and in_m not in c.nodes
                    ]
                    if not up_r and cur_top_node == in_m:
                        up_r = [
                            c for c in fb_passives
                            if c.inst_name not in used_fb and c.prefix == "R" and cur_top_node in c.nodes
                        ]
                    if up_r:
                        ur = up_r[0]
                        used_fb.add(ur.inst_name)
                        nxt_top = ur.nodes[0] if ur.nodes[1] == cur_top_node else ur.nodes[1]
                        div_chain.insert(0, (ur, nxt_top, cur_top_node))
                        cur_top_node = nxt_top
                    else:
                        break

            # Place main vertical feedback divider chain at `x = col_x + 144` and wire straight top-to-bottom!
            div_placed: List[Tuple[SpiceNetlistComponent, PlacedComponent, str, str]] = []
            for d_idx, (dc, n_top_d, n_bot_d) in enumerate(div_chain):
                dy = 352 + d_idx * 176
                dp = sch.add_component(dc.inst_name, dc.symbol_type, x=col_x + 144, y=dy, value=dc.value, orientation="R0")
                div_placed.append((dc, dp, n_top_d, n_bot_d))

            if div_placed:
                _, p_top_d, n_top_first, _ = div_placed[0]
                sch.add_flag(p_top_d.pin("top"), n_top_first, stub_dy=-32, stub_dx=32) if n_top_first != "0" else sch.ground_pin(p_top_d.pin("top"), stub_dy=-32)
                for idx_d in range(len(div_placed) - 1):
                    _, p_up, _, shared_dnet = div_placed[idx_d]
                    _, p_dn, _, _ = div_placed[idx_d + 1]
                    b_pt = p_up.pin("bot")
                    t_pt = p_dn.pin("top")
                    sch.connect_points(b_pt, t_pt, vh_order="VH")
                    mid_dy = ((b_pt[1] + t_pt[1]) // 32) * 16
                    sch.add_flag((b_pt[0], mid_dy), shared_dnet, stub_dx=32, stub_dy=0)
                _, p_bot_d, _, n_bot_last = div_placed[-1]
                sch.ground_pin(p_bot_d.pin("bot"), stub_dy=32) if n_bot_last == "0" else sch.add_flag(p_bot_d.pin("bot"), n_bot_last, stub_dy=32, stub_dx=32)

            rem_fb = [c for c in fb_passives if c.inst_name not in used_fb]
            for r_idx, rc in enumerate(rem_fb):
                if in_m in rc.nodes and rc.prefix == "R":
                    rx = col_x
                    ry = 352 + r_idx * 176
                else:
                    rx = col_x + 288
                    ry = 352 + r_idx * 176
                rp = sch.add_component(rc.inst_name, rc.symbol_type, x=rx, y=ry, value=rc.value, orientation="R0")
                rn0, rn1 = rc.nodes[0], rc.nodes[1]
                sch.add_flag(rp.pin("top"), rn0, stub_dy=-32, stub_dx=32) if rn0 != "0" else sch.ground_pin(rp.pin("top"), stub_dy=-32)
                sch.ground_pin(rp.pin("bot"), stub_dy=32) if rn1 == "0" else sch.add_flag(rp.pin("bot"), rn1, stub_dy=32, stub_dx=32)

            col_x += 432 if rem_fb else 320
            continue

        # Case 4: Diode Bias Column (`[RBP, D1, D2, RBN]`) wired vertically top-to-bottom!
        if len(col) >= 3 and any(c.prefix == "D" for c in col):
            sch.add_directive("=== POLARIZACAO ===", x=col_x, y=64, is_comment=True)
            cur_y = 112
            placed_chain: List[Tuple[SpiceNetlistComponent, PlacedComponent]] = []
            for bc in col:
                bp = sch.add_component(bc.inst_name, bc.symbol_type, x=col_x, y=cur_y, value=bc.value, orientation="R0")
                placed_chain.append((bc, bp))
                cur_y += 176

            c_first, p_first = placed_chain[0]
            sch.power_pin(p_first.pin("top"), c_first.nodes[0], stub_dy=-32, stub_dx=32)

            for idx_b in range(len(placed_chain) - 1):
                c_up, p_up = placed_chain[idx_b]
                c_dn, p_dn = placed_chain[idx_b + 1]
                shared_net = c_up.nodes[1]
                bot_pt = p_up.pin("bot")
                top_pt = p_dn.pin("top")
                sch.connect_points(bot_pt, top_pt, vh_order="VH")
                mid_y = ((bot_pt[1] + top_pt[1]) // 32) * 16
                sch.add_flag((bot_pt[0], mid_y), shared_net, stub_dx=32, stub_dy=0)

            c_last, p_last = placed_chain[-1]
            last_net = c_last.nodes[1]
            if last_net == "0":
                sch.ground_pin(p_last.pin("bot"), stub_dy=32)
            else:
                sch.power_pin(p_last.pin("bot"), last_net, stub_dy=32, stub_dx=32)

            col_x += 256
            continue

        # Case 5: Complementary Push-Pull Output Stage (`[Q1_npn, RE1, RE2, Q2_pnp]`) wired top-to-bottom!
        if len(col) == 4 and col[0].prefix == "Q" and col[3].prefix == "Q" and col[1].prefix == "R" and col[2].prefix == "R":
            q1_c, re1_c, re2_c, q2_c = col[0], col[1], col[2], col[3]
            sch.add_directive("=== SAIDA PUSH-PULL CLASSE AB ===", x=col_x, y=64, is_comment=True)
            q1_p = sch.add_component(q1_c.inst_name, "npn", x=col_x, y=144, value=q1_c.value, orientation="R0")
            re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=304, value=re1_c.value, orientation="R0")
            re2_p = sch.add_component(re2_c.inst_name, "res", x=col_x + 48, y=464, value=re2_c.value, orientation="R0")
            q2_p = sch.add_component(q2_c.inst_name, "pnp", x=col_x, y=720, value=q2_c.value, orientation="M180")

            sch.power_pin(q1_p.pin("C"), q1_c.nodes[0], stub_dy=-32, stub_dx=32)
            sch.add_flag(q1_p.pin("B"), q1_c.nodes[1], stub_dx=-48, stub_dy=0)

            sch.connect_points(q1_p.pin("E"), re1_p.pin("top"), vh_order="VH")
            sch.add_flag((q1_p.pin("E")[0], 288), q1_c.nodes[2], stub_dx=48, stub_dy=0)

            out_net = re1_c.nodes[1] if re1_c.nodes[0] == q1_c.nodes[2] else re1_c.nodes[0]
            sch.connect_points(re1_p.pin("bot"), re2_p.pin("top"), vh_order="VH")
            sch.add_flag((re1_p.pin("bot")[0], 432), out_net, stub_dx=48, stub_dy=0)

            sch.connect_points(re2_p.pin("bot"), q2_p.pin("E"), vh_order="VH")
            sch.add_flag((re2_p.pin("bot")[0], 592), q2_c.nodes[2], stub_dx=48, stub_dy=0)

            sch.add_flag(q2_p.pin("B"), q2_c.nodes[1], stub_dx=-48, stub_dy=0)
            sch.power_pin(q2_p.pin("C"), q2_c.nodes[0], stub_dy=32, stub_dx=32)

            col_x += 288
            continue

        # Case 6: Series R + Shunt C to Ground (e.g. Zobel network `[RZ, CZ]`) wired vertically!
        if len(col) == 2 and col[0].prefix == "R" and col[1].prefix == "C" and "0" in col[1].nodes:
            rz_c, cz_c = col[0], col[1]
            rz_p = sch.add_component(rz_c.inst_name, "res", x=col_x, y=288, value=rz_c.value, orientation="R0")
            cz_p = sch.add_component(cz_c.inst_name, "cap", x=col_x, y=464, value=cz_c.value, orientation="R0")
            mid_net = cz_c.nodes[0] if cz_c.nodes[1] == "0" else cz_c.nodes[1]
            top_net = rz_c.nodes[0] if rz_c.nodes[1] == mid_net else rz_c.nodes[1]
            sch.add_flag(rz_p.pin("top"), top_net, stub_dy=-32, stub_dx=32)
            sch.connect_points(rz_p.pin("bot"), cz_p.pin("top"), vh_order="VH")
            sch.add_flag((rz_p.pin("bot")[0], 416), mid_net, stub_dx=32, stub_dy=0)
            sch.ground_pin(cz_p.pin("bot"), stub_dy=32)
            col_x += 224
            continue

        # Case 7: Voltage divider `[R_top, R_bot]` wired vertically
        if len(col) == 2 and all(c.prefix == "R" for c in col):
            r_top_c, r_bot_c = col[0], col[1]
            r_top = sch.add_component(r_top_c.inst_name, "res", x=col_x, y=128, value=r_top_c.value)
            r_bot = sch.add_component(r_bot_c.inst_name, "res", x=col_x, y=384, value=r_bot_c.value)
            sch.power_pin(r_top.pin("top"), r_top_c.nodes[0], stub_dy=-32, stub_dx=32)
            sch.connect_points(r_top.pin("bot"), r_bot.pin("top"))
            mid_y = ((r_top.pin("bot")[1] + r_bot.pin("top")[1]) // 32) * 16
            sch.add_flag((r_top.pin("bot")[0], mid_y), r_top_c.nodes[1], stub_dx=32, stub_dy=0)
            sch.ground_pin(r_bot.pin("bot"), stub_dy=32) if r_bot_c.nodes[1] == "0" else sch.add_flag(r_bot.pin("bot"), r_bot_c.nodes[1], stub_dy=32, stub_dx=32)
            col_x += 224
            continue

        # Case 8: Single BJT vertical stack `[optional RC, Q, optional RE1, optional RE2]`
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
                sch.power_pin(rc_p.pin("top"), top_net, stub_dy=-32, stub_dx=32)
                sch.connect_points(rc_p.pin("bot"), q_placed.pin("C"))
                sch.add_flag((q_placed.pin("C")[0], 224), c_node, stub_dx=48, stub_dy=0)
            else:
                sch.power_pin(q_placed.pin("C"), c_node, stub_dy=-32, stub_dx=32)

            re_items = [c for c in col if c.prefix == "R" and col.index(c) > col.index(q_c)]
            if len(re_items) == 1:
                re1_c = re_items[0]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=400, value=re1_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                bot_net = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.ground_pin(re1_p.pin("bot"), stub_dy=32) if bot_net == "0" else sch.add_flag(re1_p.pin("bot"), bot_net, stub_dy=32, stub_dx=32)
            elif len(re_items) >= 2:
                re1_c, re2_c = re_items[0], re_items[1]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=384, value=re1_c.value)
                re2_p = sch.add_component(re2_c.inst_name, "res", x=col_x + 48, y=560, value=re2_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                sch.connect_points(re1_p.pin("bot"), re2_p.pin("top"))
                mid_enet = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.add_flag((re1_p.pin("bot")[0], 528), mid_enet, stub_dx=48, stub_dy=0)
                bot_net = re2_c.nodes[1] if re2_c.nodes[0] == mid_enet else re2_c.nodes[0]
                sch.ground_pin(re2_p.pin("bot"), stub_dy=32) if bot_net == "0" else sch.add_flag(re2_p.pin("bot"), bot_net, stub_dy=32, stub_dx=32)
            else:
                sch.ground_pin(q_placed.pin("E"), stub_dy=32) if e_node == "0" else sch.add_flag(q_placed.pin("E"), e_node, stub_dy=32, stub_dx=32)

            col_x += 256
            continue

        # Case 9: Single component column
        c = col[0]
        if c.prefix == "C" and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            cp = sch.add_component(c.inst_name, "cap", x=col_x + 64, y=192, value=c.value, orientation="R90")
            sch.add_flag(cp.pin("left"), c.nodes[0], stub_dx=-32, stub_dy=0)
            sch.add_flag(cp.pin("right"), c.nodes[1], stub_dx=32, stub_dy=0)
            col_x += 224
        else:
            p = sch.add_component(c.inst_name, c.symbol_type, x=col_x, y=288, value=c.value, orientation="R0")
            n0, n1 = c.nodes[0], c.nodes[1]
            sch.power_pin(p.pin("top"), n0, stub_dy=-32, stub_dx=32) if n0 != "0" else sch.ground_pin(p.pin("top"), stub_dy=-32)
            sch.ground_pin(p.pin("bot"), stub_dy=32) if n1 == "0" else sch.add_flag(p.pin("bot"), n1, stub_dy=32, stub_dx=32)
            col_x += 208

    # Wire all non-power signal nets orthogonally across stages!
    wire_signal_nets_on_schematic(sch)

    for d in proj.directives:
        sch.add_directive(d)

    if output_asc_path:
        sch.save(output_asc_path, auto_sanitize=True)
    else:
        sch.sanitize_visual_layout()
    return sch


def _layout_style_b_power_rail_bus(
    proj: SpiceNetlistProject,
    comps: List[SpiceNetlistComponent],
    output_asc_path: str,
) -> SchematicBuilder:
    """
    Style B: Continuous top `VCC` horizontal bus wire at `y = 48` across the entire circuit so
    all pull-up resistors and collectors wire straight up to the top bus bar, plus full orthogonal
    signal net wiring!
    """
    columns = _group_into_functional_columns(comps)
    sch = SchematicBuilder(width=max(1408, 240 * (len(columns) + 2)), height=1120)

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

    wire_signal_nets_on_schematic(sch)

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
    full orthogonal signal-net wiring!
    """
    n_comps = len(comps)
    sch = SchematicBuilder(width=max(1600, 256 * (n_comps + 4)), height=960)

    sch.add_directive("=== CIRCUITO MODULARIZADO POR CELULAS (ESTILO C) ===", x=96, y=48, is_comment=True)

    for idx, c in enumerate(comps):
        col_x = 96 + idx * 256
        row_y = 240
        p = sch.add_component(
            c.inst_name[1:] if c.prefix == "X" and c.inst_name.upper().startswith("XU") else c.inst_name,
            c.symbol_type,
            x=col_x,
            y=row_y,
            value=c.value,
            orientation="R0",
            prefix="X" if c.prefix == "X" else None,
            allow_custom_symbol=(c.prefix == "X"),
        )
        if c.prefix == "X" and len(c.nodes) == 5:
            p.extra_attrs["SpiceModel"] = f"{c.value}.lib"
            p.extra_attrs["Value2"] = c.value
            sch.add_flag(p.pin("1"), c.nodes[0], stub_dx=-48, stub_dy=0)
            sch.add_flag(p.pin("2"), c.nodes[1], stub_dx=-48, stub_dy=0)
            sch.power_pin(p.pin("3"), c.nodes[2], stub_dy=-48, stub_dx=32)
            sch.power_pin(p.pin("4"), c.nodes[3], stub_dy=48, stub_dx=32)
            sch.add_flag(p.pin("5"), c.nodes[4], stub_dx=48, stub_dy=0)
        elif c.prefix == "Q" and len(c.nodes) >= 3:
            sch.power_pin(p.pin("C"), c.nodes[0], stub_dy=-48, stub_dx=32)
            sch.add_flag(p.pin("B"), c.nodes[1], stub_dx=-48, stub_dy=0)
            sch.add_flag(p.pin("E"), c.nodes[2], stub_dy=48, stub_dx=32) if c.nodes[2] != "0" else sch.ground_pin(p.pin("E"), stub_dy=48)
        elif len(c.nodes) >= 2:
            sch.power_pin(p.pin("top"), c.nodes[0], stub_dy=-48, stub_dx=32) if c.nodes[0] != "0" else sch.ground_pin(p.pin("top"), stub_dy=-48)
            sch.ground_pin(p.pin("bot"), stub_dy=48) if c.nodes[1] == "0" else sch.add_flag(p.pin("bot"), c.nodes[1], stub_dy=48, stub_dx=32)

    wire_signal_nets_on_schematic(sch)

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
