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
    spice_line: Optional[str] = None

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
        if p == "M":
            vu = self.value.upper()
            pmos_prefixes = ("PMOS", "BS250", "IRF9", "FQP27P", "AO3401", "SI2301", "2SJ", "VP")
            return "pmos" if any(vu.startswith(px) or px in vu for px in pmos_prefixes) else "nmos"
        if p == "J":
            vu = self.value.upper()
            pjf_prefixes = ("PJF", "J17", "2N546")
            return "pjf" if any(vu.startswith(px) or px in vu for px in pjf_prefixes) else "njf"
        if p == "X" and len(self.nodes) == 5:
            local_app = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
            op_asy = os.path.join(local_app, "LTspice", "lib", "sym", "Opamps", f"{self.value}.asy")
            if os.path.isfile(op_asy) or self.value.lower() in ("lt1360", "universalopamp2", "opamp2"):
                return f"Opamps\\{self.value}"
            return "Opamps\\opamp2"
        if p == "X" and len(self.nodes) == 2:
            return "diode"
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
        extra = f" {comp.spice_line}" if comp.spice_line else ""
        comp.raw_line = f"{comp.inst_name} {' '.join(comp.nodes)} {comp.value}{extra}"
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
            extra = f" {c.spice_line}" if c.spice_line else ""
            lines.append(f"{c.inst_name} {' '.join(c.nodes)} {c.value}{extra}")
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
        spice_line: Optional[str] = None

        if prefix in ("R", "C", "L", "D") and len(tokens) >= 4:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:3]]
            if len(tokens) > 4 and any("=" in t for t in tokens[4:]):
                val = tokens[3]
                spice_line = " ".join(tokens[4:])
            else:
                val = " ".join(tokens[3:])
        elif prefix in ("V", "I") and len(tokens) >= 4:
            nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:3]]
            val = " ".join(tokens[3:])
        elif prefix in ("Q", "J") and len(tokens) >= 5:
            # Handle both 3-terminal (Q C B E model) and 4-terminal substrate (Q C B E sub model)
            if len(tokens) >= 6 and not any("=" in t for t in tokens[4:5]):
                nodes = ["0" if n.upper() == "GND" else n for n in tokens[1:4]]
                val = " ".join(tokens[5:])
            else:
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
            spice_line=spice_line,
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
        if _is_power_node(fl.net_name):
            body_boxes.append((fl.x - 16, fl.y - 32, fl.x + 80, fl.y + 8, {(fl.x, fl.y)}))

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
                for y in list(range(16, min(max_cy + 160, max(y1, y2) + 560), 16))
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

        if best_2 is not None and best_2[0] <= manhattan_d + 800:
            return best_2[1]

        # 3-bend fallback via escaped x and top/bottom track
        best_3: Optional[Tuple[int, List[Point]]] = best_2
        for dx1 in (0, 32, -32, 48, -48, 64, -64):
            for dx2 in (0, 32, -32, 48, -48, 64, -64):
                xe1, xe2 = x1 + dx1, x2 + dx2
                if xe1 < 16 or xe2 < 16:
                    continue
                for ym in cand_ys:
                    chain = [p1, (xe1, y1), (xe1, ym), (xe2, ym), (xe2, y2), p2]
                    valid_all = True
                    cost_3 = 0
                    for idx_s in range(len(chain) - 1):
                        if chain[idx_s] == chain[idx_s + 1]:
                            continue
                        ok_s, c_s = _seg_valid(chain[idx_s], chain[idx_s + 1], net)
                        if not ok_s:
                            valid_all = False
                            break
                        cost_3 += c_s
                    if valid_all:
                        if best_3 is None or cost_3 < best_3[0]:
                            best_3 = (cost_3, chain)
                        if cost_3 <= manhattan_d + 160:
                            return chain
        if best_3 is not None:
            return best_3[1]
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
            # Keep only ONE flag label on this continuous wired signal net (preferring the midpoint/load pin
            # rather than the left-most coupling cap pin so labels never crowd R90 capacitors!)
            keep_idx = len(fl_sorted) // 2
            for idx_f, extra_fl in enumerate(fl_sorted):
                if idx_f != keep_idx and extra_fl in sch.flags:
                    sch.flags.remove(extra_fl)

    return wired_count


def compile_netlist_to_asc(
    netlist_or_path: str,
    output_asc_path: str,
    style: str = "A_textbook_wired",
) -> SchematicBuilder:
    """
    Automatically compile any SPICE netlist (`.cir`/`.net`) into a clean, non-overlapping 16px-grid
    LTspice `.asc` schematic where:
    - Functional stages are organized left-to-right (Input Stimulus & Conditioning -> Modulation/Filter/Tuning ->
      Oscillator/Amplifier/Op-Amp Stages -> Output Buffer & Load -> Dedicated Power & Decoupling Bank).
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


def _check_bridge_rectifier(diodes: List[SpiceNetlistComponent]):
    """
    Check if 4 diodes form a Full-Wave Bridge Rectifier (2 AC nodes, 2 DC nodes).
    Returns (leg1, leg2, dc_pos, dc_neg, ac1, ac2) or None.
    """
    if len(diodes) != 4:
        return None
    node_counts: Dict[str, int] = {}
    for d in diodes:
        for n in d.nodes:
            node_counts[n] = node_counts.get(n, 0) + 1
    if len(node_counts) != 4 or not all(c == 2 for c in node_counts.values()):
        return None
    anodes = [d.nodes[0] for d in diodes]
    cathodes = [d.nodes[1] for d in diodes]
    dc_pos = [n for n in set(cathodes) if cathodes.count(n) == 2]
    dc_neg = [n for n in set(anodes) if anodes.count(n) == 2]
    if len(dc_pos) != 1 or len(dc_neg) != 1:
        return None
    p_node, n_node = dc_pos[0], dc_neg[0]
    ac_nodes = list(set(node_counts.keys()) - {p_node, n_node})
    if len(ac_nodes) != 2:
        return None
    ac1, ac2 = ac_nodes[0], ac_nodes[1]
    leg1 = [d for d in diodes if ac1 in d.nodes]
    leg2 = [d for d in diodes if ac2 in d.nodes]
    if len(leg1) != 2 or len(leg2) != 2:
        return None
    top1_cands = [d for d in leg1 if d.nodes[1] == p_node]
    bot1_cands = [d for d in leg1 if d.nodes[0] == n_node]
    top2_cands = [d for d in leg2 if d.nodes[1] == p_node]
    bot2_cands = [d for d in leg2 if d.nodes[0] == n_node]
    if not (top1_cands and bot1_cands and top2_cands and bot2_cands):
        return None
    return (
        [top1_cands[0], bot1_cands[0]],
        [top2_cands[0], bot2_cands[0]],
        p_node, n_node, ac1, ac2
    )


def _find_series_diode_chain(diodes: List[SpiceNetlistComponent]) -> Optional[List[SpiceNetlistComponent]]:
    """Return ordered list of diodes in a true head-to-tail series chain, or None."""
    if len(diodes) < 2:
        return None
    cathodes = {d.nodes[1] for d in diodes}
    heads = [d for d in diodes if d.nodes[0] not in cathodes]
    if len(heads) != 1:
        return None
    chain = [heads[0]]
    curr = heads[0]
    while len(chain) < len(diodes):
        nxt = [d for d in diodes if d not in chain and d.nodes[0] == curr.nodes[1]]
        if len(nxt) != 1:
            return None
        chain.append(nxt[0])
        curr = nxt[0]
    return chain


def _group_into_functional_columns(comps: List[SpiceNetlistComponent]) -> List[List[SpiceNetlistComponent]]:
    """
    Partition netlist components into logical left-to-right functional stage columns:
    1. Signal/control stimulus (`VIN`, `VAUDIO`, `VCTRL`) + input conditioning/coupling/filter chain.
    2. Op-Amp stages (`XU1`, `XU2`, ...) stacked with their local feedback & compensation passives directly below.
    3. Diode bias chain (`RBP`, `D1`, `D2`, `RBN`) stacked vertically in a single bias column.
    4. Complementary push-pull BJT stack (`Q1` NPN, `RE1`, `RE2`, `Q2` PNP) or BJT stage (`RC`, `Q`, `RE`)
       with 2- or 3-resistor voltage dividers (`[R1, R2]` or `[RB4a, RB4b, RTRIM]`) and Colpitts capacitive
       dividers (`[C1, C2]`).
    5. Output load (`RLOAD`) and Zobel / RC filter series-shunt branches (`[RFILT, CFILT]`, `[RZ, CZ]`).
    6. Dedicated Power Supply & Decoupling Bank (`VCC`, `VEE`, `VOSC`, `VAUX`, `C_DEC*`) at the far right.
    """
    used: Set[str] = set()
    columns: List[List[SpiceNetlistComponent]] = []

    q_connected_nodes = {n for c in comps if c.prefix == "Q" for n in c.nodes}
    opamp_connected_nodes = {n for c in comps if c.prefix == "X" and len(c.nodes) == 5 for n in c.nodes}
    active_nodes = q_connected_nodes | opamp_connected_nodes

    node_degree: Dict[str, int] = {}
    for c in comps:
        for n in c.nodes:
            node_degree[n] = node_degree.get(n, 0) + 1

    # Separate Power Supply & Decoupling components for the final right-hand block on multi-stage / decoupled circuits
    power_supplies = [c for c in comps if _is_dc_power_supply(c)]
    decoupling_caps = [c for c in comps if _is_decoupling_cap(c)]
    has_power_bank = (
        bool(decoupling_caps)
        or any(c.prefix == "X" for c in comps)
        or len([c for c in comps if c.prefix == "Q"]) >= 2
        or len(comps) >= 8
    )

    if has_power_bank and (power_supplies or decoupling_caps):
        for pc in power_supplies + decoupling_caps:
            used.add(pc.inst_name)
    else:
        sources = [c for c in comps if c.prefix in ("V", "I")]
        if len(sources) == 2:
            columns.append(sources)
            for s in sources:
                used.add(s.inst_name)
        else:
            for s in sources:
                columns.append([s])
                used.add(s.inst_name)

    # Signal / control sources (if not already placed) + follow passive input chain up to the first active stage
    for s in [c for c in comps if c.prefix in ("V", "I") and c.inst_name not in used]:
        columns.append([s])
        used.add(s.inst_name)
        cur_node = s.nodes[0] if s.nodes[1] == "0" else s.nodes[1]
        for _ in range(5):
            if cur_node == "0" or _is_power_node(cur_node):
                break
            if cur_node in active_nodes:
                # Place any unplaced shunt passive on this active input node (e.g. C2 in Sallen-Key or input termination)
                if cur_node in opamp_connected_nodes:
                    shunts_here = [
                        sh for sh in comps
                        if sh.inst_name not in used and len(sh.nodes) == 2
                        and cur_node in sh.nodes and "0" in sh.nodes
                    ]
                    for sh in shunts_here:
                        columns.append([sh])
                        used.add(sh.inst_name)
                break
            # Check for unplaced shunt elements on cur_node (excluding bias dividers that also connect to power)
            has_power_pullup = any(
                r.inst_name not in used and r.prefix == "R" and cur_node in r.nodes and any(_is_power_node(n) for n in r.nodes)
                for r in comps
            )
            shunts_here = [
                sh for sh in comps
                if sh.inst_name not in used and len(sh.nodes) == 2
                and cur_node in sh.nodes and "0" in sh.nodes and not has_power_pullup
            ]
            series_here = [
                sr for sr in comps
                if sr.inst_name not in used and sr.prefix in ("R", "C", "L") and len(sr.nodes) == 2
                and cur_node in sr.nodes and "0" not in sr.nodes
                and not any(_is_power_node(n) for n in sr.nodes)
            ]
            if not series_here:
                # If cur_node is an active device node (e.g. Emitter in Common Base), don't consume its shunt here;
                # let the transistor stage group RE directly under Q!
                if cur_node in active_nodes:
                    break
                for sh in shunts_here:
                    columns.append([sh])
                    used.add(sh.inst_name)
                break

            # Place any shunt on cur_node before advancing across series element (unless series R + shunt C on next node)
            for sh in shunts_here:
                columns.append([sh])
                used.add(sh.inst_name)

            sr = series_here[0]
            nxt_node = sr.nodes[1] if sr.nodes[0] == cur_node else sr.nodes[0]
            # Check if `sr` (R) + a shunt `C` on `nxt_node` form an RC low-pass filter (`[RFILT, CFILT]`)
            if sr.prefix == "R" and nxt_node not in active_nodes:
                c_shunt_nxt = [
                    cs for cs in comps
                    if cs.inst_name not in used and cs.prefix == "C"
                    and nxt_node in cs.nodes and "0" in cs.nodes
                ]
                if c_shunt_nxt:
                    used.add(sr.inst_name)
                    used.add(c_shunt_nxt[0].inst_name)
                    columns.append([sr, c_shunt_nxt[0]])
                    cur_node = nxt_node
                    continue

            used.add(sr.inst_name)
            columns.append([sr])
            cur_node = nxt_node

            # If cur_node now has a pure input termination resistor to ground (not a bias divider), place it
            has_pullup_nxt = any(
                r.inst_name not in used and r.prefix == "R" and cur_node in r.nodes and any(_is_power_node(n) for n in r.nodes)
                for r in comps
            )
            if not has_pullup_nxt:
                r_in = [
                    r for r in comps
                    if r.inst_name not in used and r.prefix == "R" and cur_node in r.nodes and "0" in r.nodes
                    and cur_node in opamp_connected_nodes
                ]
                if r_in:
                    columns.append([r_in[0]])
                    used.add(r_in[0].inst_name)

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

    # Diode structures: Full-Wave Bridge Rectifier, Antiparallel clipper pair, or Series Bias Chain
    diodes = [c for c in comps if c.prefix == "D" and c.inst_name not in used]
    bridge_info = _check_bridge_rectifier(diodes)
    if bridge_info:
        leg1, leg2, _, _, _, _ = bridge_info
        columns.append(leg1)
        for d in leg1:
            used.add(d.inst_name)
        columns.append(leg2)
        for d in leg2:
            used.add(d.inst_name)
    else:
        s_chain = _find_series_diode_chain(diodes)
        if s_chain:
            d_nodes = {n for d in s_chain for n in d.nodes}
            bias_res = [
                r for r in comps
                if r.inst_name not in used and r.prefix == "R"
                and any(n in d_nodes for n in r.nodes)
                and any(_is_power_node(n) for n in r.nodes)
            ]
            r_top_b = [r for r in bias_res if any(n.upper().startswith(("VCC", "VDD", "VP", "+")) for n in r.nodes)]
            r_bot_b = [r for r in bias_res if r not in r_top_b]
            if r_top_b or r_bot_b:
                bias_col: List[SpiceNetlistComponent] = []
                for r in r_top_b:
                    bias_col.append(r)
                    used.add(r.inst_name)
                for d in s_chain:
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

    # Track nodes already placed in columns so shunt components on an active node come before series coupling out of it
    placed_nodes: Set[str] = {n for col in columns for item in col for n in item.nodes if n != "0" and not _is_power_node(n)}

    def _record_col(col_items: List[SpiceNetlistComponent]) -> None:
        columns.append(col_items)
        for item in col_items:
            used.add(item.inst_name)
            for n in item.nodes:
                if n != "0" and not _is_power_node(n):
                    placed_nodes.add(n)

    # Identify circuit input signal source nodes (excluding DC power rails and ground)
    signal_source_nodes: Set[str] = {
        n for c in comps
        if c.prefix in ("V", "I") and not _is_dc_power_supply(c)
        for n in c.nodes if n != "0" and not _is_power_node(n)
    }
    if not signal_source_nodes:
        signal_source_nodes = {"IN"}

    def _can_reach_source_without_node(target_node: str, forbidden_node: str) -> bool:
        visited = {forbidden_node, "0"}
        queue = [target_node]
        visited.add(target_node)
        while queue:
            curr = queue.pop(0)
            if curr in signal_source_nodes:
                return True
            for other in comps:
                if curr in other.nodes:
                    for nxt in other.nodes:
                        if nxt not in visited and not _is_power_node(nxt):
                            visited.add(nxt)
                            queue.append(nxt)
        return False

    def _has_unplaced_driver_to_node(node: str, exclude_inst: str) -> bool:
        for other in comps:
            if other.inst_name in used or other.inst_name == exclude_inst:
                continue
            if node not in other.nodes:
                continue
            if other.prefix in ("Q", "M", "J") and len(other.nodes) >= 3 and node in (other.nodes[0], other.nodes[2]):
                return True
            if other.prefix == "X" and len(other.nodes) == 5 and node == other.nodes[4]:
                return True
            if len(other.nodes) == 2 and "0" not in other.nodes and not any(_is_power_node(n) for n in other.nodes):
                other_n = other.nodes[0] if other.nodes[1] == node else other.nodes[1]
                if _can_reach_source_without_node(other_n, node):
                    return True
        return False

    # Walk remaining signal components, forming 2- or 3-resistor dividers, BJT stacks, Colpitts tanks, or RC/Zobel branches
    for c in comps:
        if c.inst_name in used:
            continue

        # Antiparallel Diode Clipping Pair (`[D1, D2]`) placed at its signal node
        if c.prefix == "D":
            other_d = [
                d for d in comps
                if d.inst_name not in used and d.inst_name != c.inst_name and d.prefix == "D"
                and set(d.nodes) == set(c.nodes)
            ]
            if other_d:
                _record_col([c, other_d[0]])
                continue

        # Defer emitter resistors of unplaced transistors so they are placed directly in the transistor stack
        if c.prefix == "R" and any(
            q.inst_name not in used and q.prefix in ("Q", "M", "J")
            and len(q.nodes) >= 3 and q.nodes[2] in c.nodes
            and not any(_is_power_node(n) for n in c.nodes)
            for q in comps
        ):
            continue

        # 2- or 3-Resistor Voltage Divider (`R_top` from VCC -> mid -> [optional R_mid -> trim] -> R_bot -> 0)
        if c.prefix == "R" and (_is_power_node(c.nodes[0]) or _is_power_node(c.nodes[1])) and "0" not in c.nodes:
            mid_node = c.nodes[1] if _is_power_node(c.nodes[0]) else c.nodes[0]
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
                    # Check for bypass capacitor on mid_node (e.g. `CB BIAS 0 10n`)
                    cb_list = [
                        cb for cb in comps
                        if cb.inst_name not in used and cb.prefix == "C" and mid_node in cb.nodes and "0" in cb.nodes
                    ]
                    if cb_list:
                        _record_col([c, r_bot[0]] + cb_list)
                    else:
                        _record_col([c, r_bot[0]])
                    continue

                # Check for 3-resistor divider chain (`RB4a` -> `RB4b` -> `RTRIM` -> `0`)
                r_mid_cand = [
                    r for r in comps
                    if r.inst_name not in used and r.inst_name != c.inst_name
                    and r.prefix == "R" and mid_node in r.nodes and "0" not in r.nodes
                    and not any(_is_power_node(n) for n in r.nodes)
                ]
                for rm in r_mid_cand:
                    trim_node = rm.nodes[1] if rm.nodes[0] == mid_node else rm.nodes[0]
                    r_bot3 = [
                        rb for rb in comps
                        if rb.inst_name not in used and rb.inst_name not in (c.inst_name, rm.inst_name)
                        and rb.prefix == "R" and trim_node in rb.nodes and "0" in rb.nodes
                    ]
                    if r_bot3:
                        _record_col([c, rm, r_bot3[0]])
                        break
                if c.inst_name in used:
                    continue

        # Standard BJT / MOSFET / JFET stack (`[optional RC/RD, Q/M/J, optional RE1/RS1, optional RE2/RS2]`)
        if c.prefix in ("Q", "M", "J") or (
            c.prefix == "R" and any(
                q.inst_name not in used and q.prefix in ("Q", "M", "J")
                and not _is_power_node(q.nodes[0]) and q.nodes[0] != "0"
                and q.nodes[0] in c.nodes and any(_is_power_node(n) for n in c.nodes)
                for q in comps
            )
        ):
            q_comp = c if c.prefix in ("Q", "M", "J") else next(
                q for q in comps
                if q.inst_name not in used and q.prefix in ("Q", "M", "J")
                and not _is_power_node(q.nodes[0]) and q.nodes[0] != "0"
                and q.nodes[0] in c.nodes
            )
            col_stack: List[SpiceNetlistComponent] = []
            c_node, b_node, e_node = q_comp.nodes[0], q_comp.nodes[1], q_comp.nodes[2]
            rc_list = [
                r for r in comps
                if not _is_power_node(c_node) and c_node != "0"
                and r.inst_name not in used and r.prefix == "R"
                and c_node in r.nodes and any(_is_power_node(n) for n in r.nodes)
            ]
            if rc_list:
                col_stack.append(rc_list[0])
                used.add(rc_list[0].inst_name)
            col_stack.append(q_comp)
            used.add(q_comp.inst_name)

            mid_enet: Optional[str] = None
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
                    mid_enet = other_node
                    re2_list = [
                        r for r in comps
                        if r.inst_name not in used and r.prefix == "R"
                        and other_node in r.nodes and "0" in r.nodes
                    ]
                    if re2_list:
                        col_stack.append(re2_list[0])
                        used.add(re2_list[0].inst_name)
            # Check for Colpitts capacitive voltage divider (`C1` between `B` and `E`, `C2` between `E` and `0`)
            c1_colp = [
                cc for cc in comps
                if cc.inst_name not in used and cc.prefix == "C"
                and b_node in cc.nodes and e_node in cc.nodes
            ]
            c2_colp = [
                cc for cc in comps
                if cc.inst_name not in used and cc.prefix == "C"
                and e_node in cc.nodes and "0" in cc.nodes
            ]
            if c1_colp and c2_colp:
                _record_col(col_stack)
                _record_col([c1_colp[0], c2_colp[0]])
                continue

            # Check for Emitter Bypass Capacitor (`C_BYPASS` on `e_node` or `mid_enet` to `0`)
            for en_cand in ([mid_enet, e_node] if mid_enet else [e_node]):
                if not en_cand:
                    continue
                c_byp = [
                    cb for cb in comps
                    if cb.inst_name not in used and cb.prefix == "C"
                    and en_cand in cb.nodes and "0" in cb.nodes
                ]
                for cb in c_byp:
                    col_stack.append(cb)
                    used.add(cb.inst_name)
                    placed_nodes.add(en_cand)
            _record_col(col_stack)
            continue

        # Series R + C to Ground (e.g. RC filter `[RFILT, CFILT]` or Zobel network `[RZ, CZ]`)
        if c.prefix == "R" and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            n_a, n_b = c.nodes[0], c.nodes[1]
            c_zob = [
                cz for cz in comps
                if cz.inst_name not in used and cz.prefix == "C"
                and "0" in cz.nodes and (n_b in cz.nodes or n_a in cz.nodes)
            ]
            if c_zob:
                _record_col([c, c_zob[0]])
                continue

        # Defer load resistors / termination shunts if there are still unplaced series/active components driving this node
        if len(c.nodes) == 2 and "0" in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            live_n = c.nodes[0] if c.nodes[1] == "0" else c.nodes[1]
            if _has_unplaced_driver_to_node(live_n, c.inst_name):
                continue

        # Before placing a series passive (`CCV B KVAR`), place any unplaced shunt component (`X1 KVAR 0`) on the already-visited node!
        if len(c.nodes) == 2 and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            vis_n = [n for n in c.nodes if n in placed_nodes]
            for vn in vis_n:
                shunts_vn = [
                    sh for sh in comps
                    if sh.inst_name not in used and sh.inst_name != c.inst_name
                    and len(sh.nodes) == 2 and vn in sh.nodes and "0" in sh.nodes
                    and not _has_unplaced_driver_to_node(vn, sh.inst_name)
                ]
                for sh in shunts_vn:
                    _record_col([sh])

        _record_col([c])

    # Final pass: place any deferred loads/shunts whose drivers have now been placed
    for c in comps:
        if c.inst_name not in used:
            _record_col([c])

    # Append dedicated Power Supply & Decoupling column(s) at the end if separated earlier
    if has_power_bank and (power_supplies or decoupling_caps):
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

    node_degree: Dict[str, int] = {}
    for c in comps:
        for n in c.nodes:
            node_degree[n] = node_degree.get(n, 0) + 1

    visited_nets: Set[str] = set()
    net_y_hint: Dict[str, int] = {}

    def _mark_visited(col_items: List[SpiceNetlistComponent]) -> None:
        for item in col_items:
            for n in item.nodes:
                if n != "0" and not _is_power_node(n):
                    visited_nets.add(n)

    col_x = 96
    power_bank_x: Optional[int] = None
    power_bank_idx = 0
    for col_idx, col in enumerate(columns):
        # Case 1: Power Rail Bank (`[V_supply, optional C_dec1, C_dec2, ...]`) — stack multiple rails vertically at the right!
        if (
            len(col) >= 1
            and _is_dc_power_supply(col[0])
            and all(_is_decoupling_cap(c) for c in col[1:])
            and (len(col) >= 2 or col_idx >= len(columns) - 3)
        ):
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
                p = sch.add_component(pc.inst_name, pc.symbol_type, x=px, y=by_base, value=pc.value, spice_line=pc.spice_line)
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
            _mark_visited(col)
            continue

        # Case 2: Stacked 2 sources on left column
        if len(col) == 2 and all(c.prefix in ("V", "I") for c in col):
            for s_idx, sc in enumerate(col):
                sy = 144 + s_idx * 288
                p = sch.add_component(sc.inst_name, sc.symbol_type, x=col_x, y=sy, value=sc.value, spice_line=sc.spice_line)
                n_top, n_bot = sc.nodes[0], sc.nodes[1]
                sch.power_pin(p.pin("top"), n_top, stub_dy=-32, stub_dx=32) if n_top != "0" else sch.ground_pin(p.pin("top"), stub_dy=-32)
                sch.ground_pin(p.pin("bot"), stub_dy=32) if n_bot == "0" else sch.add_flag(p.pin("bot"), n_bot, stub_dy=32, stub_dx=32)
            col_x += 240
            _mark_visited(col)
            continue

        # Case 3: Op-Amp Stage (`XU1` + structured feedback divider & compensation sub-columns below)
        if col[0].prefix == "X" and len(col[0].nodes) == 5:
            op_c = col[0]
            op_name = op_c.inst_name[1:] if op_c.inst_name.upper().startswith("XU") else op_c.inst_name
            sch.add_directive(f"=== ESTAGIO AMP-OP ({op_name}) ===", x=col_x, y=48, is_comment=True)
            is_opamp2 = op_c.symbol_type.lower().endswith("opamp2") and not op_c.symbol_type.lower().endswith("universalopamp2")
            op_y = 128 if is_opamp2 else 192
            op_p = sch.add_component(
                op_name,
                op_c.symbol_type,
                x=col_x + 144,
                y=op_y,
                value="" if op_c.symbol_type.lower().endswith("universalopamp2") else op_c.value,
                prefix="X",
                allow_custom_symbol=True,
            )
            if not is_opamp2 and op_c.value.lower() != "universalopamp2":
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
                dp = sch.add_component(dc.inst_name, dc.symbol_type, x=col_x + 144, y=dy, value=dc.value, orientation="R0", spice_line=dc.spice_line)
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
                rp = sch.add_component(rc.inst_name, rc.symbol_type, x=rx, y=ry, value=rc.value, orientation="R0", spice_line=rc.spice_line)
                rn0, rn1 = rc.nodes[0], rc.nodes[1]
                sch.add_flag(rp.pin("top"), rn0, stub_dy=-32, stub_dx=32) if rn0 != "0" else sch.ground_pin(rp.pin("top"), stub_dy=-32)
                sch.ground_pin(rp.pin("bot"), stub_dy=32) if rn1 == "0" else sch.add_flag(rp.pin("bot"), rn1, stub_dy=32, stub_dx=32)

            col_x += 432 if rem_fb else 320
            _mark_visited(col)
            continue

        # Case 4a: Full-Wave Diode Bridge Rectifier Leg (`[D_top, D_bot]`)
        if len(col) == 2 and all(c.prefix == "D" for c in col) and len(set(col[0].nodes) & set(col[1].nodes)) == 1:
            shared_set = set(col[0].nodes) & set(col[1].nodes)
            ac_node = list(shared_set)[0]
            d_cand_pos = [d for d in col if d.nodes[1] != ac_node]
            d_cand_neg = [d for d in col if d.nodes[0] != ac_node]
            if d_cand_pos and d_cand_neg:
                d_top_c = d_cand_pos[0]
                d_bot_c = d_cand_neg[0]
                dc_pos = d_top_c.nodes[1]
                dc_neg = d_bot_c.nodes[0]
                d_top_p = sch.add_component(d_top_c.inst_name, "diode", x=col_x, y=240, value=d_top_c.value, orientation="R180", spice_line=d_top_c.spice_line)
                d_bot_p = sch.add_component(d_bot_c.inst_name, "diode", x=col_x, y=400, value=d_bot_c.value, orientation="R180", spice_line=d_bot_c.spice_line)
                sch.connect_points(d_top_p.pin("A"), d_bot_p.pin("K"))
                sch.add_flag(d_top_p.pin("A"), ac_node, stub_dx=-32, stub_dy=0)
                sch.add_flag(d_top_p.pin("K"), dc_pos, stub_dy=-32, stub_dx=32)
                if dc_neg == "0":
                    sch.ground_pin(d_bot_p.pin("A"), stub_dy=32)
                else:
                    sch.add_flag(d_bot_p.pin("A"), dc_neg, stub_dy=32, stub_dx=32)
                col_x += 208
                _mark_visited(col)
                continue

        # Case 4b: Antiparallel Diode Clipping Pair (`[D1, D2]`)
        if len(col) == 2 and all(c.prefix == "D" for c in col) and len(set(col[0].nodes) & set(col[1].nodes)) == 2:
            d1_c, d2_c = col[0], col[1]
            if "0" in d1_c.nodes:
                n_bot = "0"
                n_top = d1_c.nodes[0] if d1_c.nodes[1] == "0" else d1_c.nodes[1]
            else:
                n_top, n_bot = d1_c.nodes[0], d1_c.nodes[1]

            if d1_c.nodes[0] == n_top:
                d_top_a, d_bot_a = d1_c, d2_c
            else:
                d_top_a, d_bot_a = d2_c, d1_c

            d1_p = sch.add_component(d_top_a.inst_name, "diode", x=col_x, y=288, value=d_top_a.value, orientation="R0", spice_line=d_top_a.spice_line)
            d2_p = sch.add_component(d_bot_a.inst_name, "diode", x=col_x + 96, y=352, value=d_bot_a.value, orientation="R180", spice_line=d_bot_a.spice_line)
            sch.connect_points(d1_p.pin("A"), d2_p.pin("K"))
            sch.connect_points(d1_p.pin("K"), d2_p.pin("A"))
            sch.add_flag(d1_p.pin("A"), n_top, stub_dy=-32, stub_dx=32) if n_top != "0" else sch.ground_pin(d1_p.pin("A"), stub_dy=-32)
            sch.ground_pin(d1_p.pin("K"), stub_dy=32) if n_bot == "0" else sch.add_flag(d1_p.pin("K"), n_bot, stub_dy=32, stub_dx=32)
            col_x += 256
            _mark_visited(col)
            continue

        # Case 4: Diode Bias Column (`[RBP, D1, D2, RBN]`) wired vertically top-to-bottom!
        if len(col) >= 3 and any(c.prefix == "D" for c in col):
            sch.add_directive("=== POLARIZACAO ===", x=col_x, y=64, is_comment=True)
            cur_y = 112
            placed_chain: List[Tuple[SpiceNetlistComponent, PlacedComponent]] = []
            for bc in col:
                bp = sch.add_component(bc.inst_name, bc.symbol_type, x=col_x, y=cur_y, value=bc.value, orientation="R0", spice_line=bc.spice_line)
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
            _mark_visited(col)
            continue

        # Case 5: Complementary Push-Pull Output Stage (`[Q1_npn, RE1, RE2, Q2_pnp]`) wired top-to-bottom!
        if len(col) == 4 and col[0].prefix == "Q" and col[3].prefix == "Q" and col[1].prefix == "R" and col[2].prefix == "R":
            q1_c, re1_c, re2_c, q2_c = col[0], col[1], col[2], col[3]
            sch.add_directive("=== SAIDA PUSH-PULL CLASSE AB ===", x=col_x, y=64, is_comment=True)
            q1_p = sch.add_component(q1_c.inst_name, "npn", x=col_x, y=144, value=q1_c.value, orientation="R0")
            re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=304, value=re1_c.value, orientation="R0", spice_line=re1_c.spice_line)
            re2_p = sch.add_component(re2_c.inst_name, "res", x=col_x + 48, y=464, value=re2_c.value, orientation="R0", spice_line=re2_c.spice_line)
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
            _mark_visited(col)
            continue

        # Case 6: Series R + Shunt C to Ground (In-line RC Low-Pass Filter `[RFILT, CFILT]` OR dead-end Zobel `[RZ, CZ]`)
        if len(col) == 2 and col[0].prefix == "R" and col[1].prefix == "C" and "0" in col[1].nodes:
            rz_c, cz_c = col[0], col[1]
            mid_net = cz_c.nodes[0] if cz_c.nodes[1] == "0" else cz_c.nodes[1]
            top_net = rz_c.nodes[0] if rz_c.nodes[1] == mid_net else rz_c.nodes[1]
            if node_degree.get(mid_net, 0) >= 3:
                # In-line L-section RC filter: horizontal series R (`R90`) + vertical shunt C (`R0`)
                rz_p = sch.add_component(rz_c.inst_name, "res", x=col_x + 112, y=224, value=rz_c.value, orientation="R90", spice_line=rz_c.spice_line)
                cz_p = sch.add_component(cz_c.inst_name, "cap", x=col_x + 80, y=336, value=cz_c.value, orientation="R0", spice_line=cz_c.spice_line)
                sch.add_flag(rz_p.pin("left"), top_net, stub_dx=-32, stub_dy=0)
                sch.connect_points(rz_p.pin("right"), cz_p.pin("top"), vh_order="VH")
                sch.add_flag(rz_p.pin("right"), mid_net, stub_dx=32, stub_dy=0)
                sch.ground_pin(cz_p.pin("bot"), stub_dy=32)
                net_y_hint[mid_net] = 240
                col_x += 240
            else:
                # Shunt Zobel branch: vertical R (`R0`) stacked above vertical C (`R0`)
                rz_p = sch.add_component(rz_c.inst_name, "res", x=col_x, y=288, value=rz_c.value, orientation="R0", spice_line=rz_c.spice_line)
                cz_p = sch.add_component(cz_c.inst_name, "cap", x=col_x, y=464, value=cz_c.value, orientation="R0", spice_line=cz_c.spice_line)
                sch.add_flag(rz_p.pin("top"), top_net, stub_dy=-32, stub_dx=32)
                sch.connect_points(rz_p.pin("bot"), cz_p.pin("top"), vh_order="VH")
                sch.add_flag((rz_p.pin("bot")[0], 416), mid_net, stub_dx=32, stub_dy=0)
                sch.ground_pin(cz_p.pin("bot"), stub_dy=32)
                col_x += 224
            _mark_visited(col)
            continue

        # Case 7a: 2- or 3-Resistor Voltage Divider (`[R_top, R_bot]` or `[RB4a, RB4b, RTRIM]`), optionally with parallel CB
        r_comps = [c for c in col if c.prefix == "R"]
        c_byp_cb = [c for c in col if c.prefix == "C"]
        if (len(r_comps) in (2, 3) and len(col) == len(r_comps) + len(c_byp_cb) and len(c_byp_cb) <= 1):
            ys_div = [128, 384] if len(r_comps) == 2 else [128, 320, 496]
            placed_rs: List[Tuple[SpiceNetlistComponent, PlacedComponent]] = []
            for idx_r, rc_item in enumerate(r_comps):
                rp = sch.add_component(rc_item.inst_name, "res", x=col_x, y=ys_div[idx_r], value=rc_item.value, spice_line=rc_item.spice_line)
                placed_rs.append((rc_item, rp))
            r0_c, r0_p = placed_rs[0]
            top_pwr = r0_c.nodes[0] if _is_power_node(r0_c.nodes[0]) else r0_c.nodes[1]
            cur_net = r0_c.nodes[1] if top_pwr == r0_c.nodes[0] else r0_c.nodes[0]
            sch.power_pin(r0_p.pin("top"), top_pwr, stub_dy=-32, stub_dx=32)
            for idx_r in range(len(placed_rs) - 1):
                _, p_up = placed_rs[idx_r]
                c_dn, p_dn = placed_rs[idx_r + 1]
                sch.connect_points(p_up.pin("bot"), p_dn.pin("top"))
                # Place the primary bias tap at y=288 so it connects straight across to Q.Base (y=288)!
                tap_y = 288 if idx_r == 0 else ((p_up.pin("bot")[1] + p_dn.pin("top")[1]) // 32) * 16
                sch.add_flag((p_up.pin("bot")[0], tap_y), cur_net, stub_dx=32, stub_dy=0)
                net_y_hint[cur_net] = tap_y
                cur_net = c_dn.nodes[1] if c_dn.nodes[0] == cur_net else c_dn.nodes[0]
            _, p_last = placed_rs[-1]
            sch.ground_pin(p_last.pin("bot"), stub_dy=32) if cur_net == "0" else sch.add_flag(p_last.pin("bot"), cur_net, stub_dy=32, stub_dx=32)

            if c_byp_cb:
                cb_c = c_byp_cb[0]
                cb_p = sch.add_component(cb_c.inst_name, "cap", x=col_x + 96, y=400, value=cb_c.value, orientation="R0", spice_line=cb_c.spice_line)
                sch.connect_points((p_last.pin("top")[0], 400), cb_p.pin("top"), vh_order="HV")
                sch.ground_pin(cb_p.pin("bot"), stub_dy=32)
                col_x += 256
            else:
                col_x += 224
            _mark_visited(col)
            continue

        # Case 7b: 2-Capacitor Colpitts Capacitive Divider (`[C1, C2]`) wired vertically
        if len(col) == 2 and all(c.prefix == "C" for c in col):
            c_top_c, c_bot_c = col[0], col[1]
            mid_cnet = c_bot_c.nodes[0] if c_bot_c.nodes[1] == "0" else c_bot_c.nodes[1]
            top_cnet = c_top_c.nodes[0] if c_top_c.nodes[1] == mid_cnet else c_top_c.nodes[1]
            bot_cnet = c_bot_c.nodes[1] if c_bot_c.nodes[0] == mid_cnet else c_bot_c.nodes[0]
            c_top_p = sch.add_component(c_top_c.inst_name, "cap", x=col_x, y=208, value=c_top_c.value, orientation="R0", spice_line=c_top_c.spice_line)
            c_bot_p = sch.add_component(c_bot_c.inst_name, "cap", x=col_x, y=400, value=c_bot_c.value, orientation="R0", spice_line=c_bot_c.spice_line)
            sch.add_flag(c_top_p.pin("top"), top_cnet, stub_dy=-32, stub_dx=32)
            sch.connect_points(c_top_p.pin("bot"), c_bot_p.pin("top"))
            # Place capacitive tap at y=368 so it wires straight across to Q.Emitter (y=368)!
            sch.add_flag((c_top_p.pin("bot")[0], 368), mid_cnet, stub_dx=32, stub_dy=0)
            net_y_hint[mid_cnet] = 368
            sch.ground_pin(c_bot_p.pin("bot"), stub_dy=32) if bot_cnet == "0" else sch.add_flag(c_bot_p.pin("bot"), bot_cnet, stub_dy=32, stub_dx=32)
            col_x += 224
            _mark_visited(col)
            continue

        # Case 8: Single BJT / MOSFET / JFET vertical stack `[optional RC/RD, Q/M/J, optional RE1/RS1, optional RE2/RS2, optional CE]`
        if any(c.prefix in ("Q", "M", "J") for c in col):
            q_c = next(c for c in col if c.prefix in ("Q", "M", "J"))
            rx_off = 48 if q_c.prefix == "Q" else 32
            q_placed = sch.add_component(q_c.inst_name, q_c.symbol_type, x=col_x, y=240, value=q_c.value)
            c_node, b_node, e_node = q_c.nodes[0], q_c.nodes[1], q_c.nodes[2]
            sch.add_flag(q_placed.pin("B"), b_node, stub_dx=-32, stub_dy=0)
            net_y_hint[b_node] = q_placed.pin("B")[1]

            rc_items = [c for c in col if c.prefix == "R" and c_node in c.nodes and c.inst_name != q_c.inst_name]
            if rc_items and col.index(rc_items[0]) < col.index(q_c):
                rc_c = rc_items[0]
                rc_p = sch.add_component(rc_c.inst_name, "res", x=col_x + rx_off, y=80, value=rc_c.value, spice_line=rc_c.spice_line)
                top_net = rc_c.nodes[0] if rc_c.nodes[1] == c_node else rc_c.nodes[1]
                sch.power_pin(rc_p.pin("top"), top_net, stub_dy=-32, stub_dx=32)
                sch.connect_points(rc_p.pin("bot"), q_placed.pin("C"))
                sch.add_flag((q_placed.pin("C")[0], 224), c_node, stub_dx=48, stub_dy=0)
                net_y_hint[c_node] = 224
            else:
                sch.power_pin(q_placed.pin("C"), c_node, stub_dy=-32, stub_dx=32)

            re_items = [c for c in col if c.prefix == "R" and col.index(c) > col.index(q_c)]
            ce_items = [c for c in col if c.prefix == "C"]

            if len(re_items) == 1:
                re1_c = re_items[0]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + rx_off, y=400, value=re1_c.value, spice_line=re1_c.spice_line)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                net_y_hint[e_node] = 368
                bot_net = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.ground_pin(re1_p.pin("bot"), stub_dy=32) if bot_net == "0" else sch.add_flag(re1_p.pin("bot"), bot_net, stub_dy=32, stub_dx=32)

                if ce_items:
                    ce_c = ce_items[0]
                    ce_p = sch.add_component(ce_c.inst_name, "cap", x=col_x + rx_off + 96, y=416, value=ce_c.value, orientation="R0", spice_line=ce_c.spice_line)
                    sch.add_wire(re1_p.pin("top")[0], 416, ce_p.pin("top")[0], 416)
                    sch.ground_pin(ce_p.pin("bot"), stub_dy=32)
                    col_x += 304
                else:
                    col_x += 256
            elif len(re_items) >= 2:
                re1_c, re2_c = re_items[0], re_items[1]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + rx_off, y=384, value=re1_c.value, spice_line=re1_c.spice_line)
                re2_p = sch.add_component(re2_c.inst_name, "res", x=col_x + rx_off, y=560, value=re2_c.value, spice_line=re2_c.spice_line)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                net_y_hint[e_node] = 368
                sch.connect_points(re1_p.pin("bot"), re2_p.pin("top"))
                mid_enet = re1_c.nodes[1] if re1_c.nodes[0] == e_node else re1_c.nodes[0]
                sch.add_flag((re1_p.pin("bot")[0], 528), mid_enet, stub_dx=48, stub_dy=0)
                net_y_hint[mid_enet] = 528
                bot_net = re2_c.nodes[1] if re2_c.nodes[0] == mid_enet else re2_c.nodes[0]
                sch.ground_pin(re2_p.pin("bot"), stub_dy=32) if bot_net == "0" else sch.add_flag(re2_p.pin("bot"), bot_net, stub_dy=32, stub_dx=32)

                if ce_items:
                    ce_c = ce_items[0]
                    ce_p = sch.add_component(ce_c.inst_name, "cap", x=col_x + rx_off + 96, y=576, value=ce_c.value, orientation="R0", spice_line=ce_c.spice_line)
                    sch.add_wire(re2_p.pin("top")[0], 576, ce_p.pin("top")[0], 576)
                    sch.ground_pin(ce_p.pin("bot"), stub_dy=32)
                    col_x += 304
                else:
                    col_x += 256
            else:
                sch.ground_pin(q_placed.pin("E"), stub_dy=32) if e_node == "0" else sch.add_flag(q_placed.pin("E"), e_node, stub_dy=32, stub_dx=32)
                col_x += 256
            _mark_visited(col)
            continue

        # Case 9: Single component column (Horizontal `R90` for series R/L/C between signal nodes; Vertical `R0` for shunts/sources)
        c = col[0]
        if c.prefix in ("R", "C", "L") and "0" not in c.nodes and not any(_is_power_node(n) for n in c.nodes):
            # Orient left/right pins monotonically along left-to-right signal flow!
            if c.nodes[1] in visited_nets and c.nodes[0] not in visited_nets:
                left_net, right_net = c.nodes[1], c.nodes[0]
            else:
                left_net, right_net = c.nodes[0], c.nodes[1]

            hint_l = net_y_hint.get(left_net)
            hint_r = net_y_hint.get(right_net)
            if hint_l is None and hint_r is None:
                # Lookahead to downstream transistor terminals
                if any(q.prefix in ("Q", "M", "J") and len(q.nodes) >= 3 and q.nodes[1] == right_net for q in comps):
                    hint_r = 288
                elif any(q.prefix in ("Q", "M", "J") and len(q.nodes) >= 3 and q.nodes[2] == right_net for q in comps):
                    hint_r = 368
                elif any(q.prefix in ("Q", "M", "J") and len(q.nodes) >= 3 and q.nodes[0] == left_net for q in comps):
                    hint_l = 224
                elif any(q.prefix in ("Q", "M", "J") and len(q.nodes) >= 3 and q.nodes[2] == left_net for q in comps):
                    hint_l = 368

            if hint_l == 224 or hint_r == 224:
                y_ser = 208  # Aligns R90 cap pins at y=224 with Collector line!
            elif hint_l == 368 or hint_r == 368:
                y_ser = 352  # Aligns R90 cap pins at y=368 with Emitter line!
            elif hint_l == 288 or hint_r == 288:
                y_ser = 272  # Aligns R90 cap pins at y=288 with Base line!
            elif hint_l == 240 or hint_r == 240:
                y_ser = 224  # Aligns RISO pins at y=240 with RFILT (240)!
            else:
                y_ser = 192  # Standard coupling height (pins at y=208)

            x_off = 64 if c.symbol_type == "cap" else 96
            cp = sch.add_component(
                c.inst_name,
                c.symbol_type,
                x=col_x + x_off,
                y=y_ser,
                value=c.value,
                orientation="R90",
                spice_line=c.spice_line,
            )
            sch.add_flag(cp.pin("left"), left_net, stub_dx=-32, stub_dy=0)
            sch.add_flag(cp.pin("right"), right_net, stub_dx=32, stub_dy=0)
            net_y_hint[right_net] = y_ser + 16
            net_y_hint[left_net] = y_ser + 16
            col_x += 224
        else:
            # Ensure ground is always at the bottom (`n1 == "0"`) for shunt passives
            if c.nodes[0] == "0" and c.nodes[1] != "0" and c.prefix in ("R", "C", "L"):
                n0, n1 = c.nodes[1], "0"
            else:
                n0, n1 = c.nodes[0], c.nodes[1]

            hint_y = net_y_hint.get(n0)
            if c.prefix == "V":
                # Lookahead if VIN drives CIN which connects to Base (288) or Emitter (368)
                cin_cand = [cp for cp in comps if cp.prefix == "C" and n0 in cp.nodes]
                is_to_base = cin_cand and any(
                    q.prefix in ("Q", "M", "J") and len(q.nodes) >= 3 and q.nodes[1] in cin_cand[0].nodes
                    for q in comps
                )
                is_to_emit = cin_cand and any(
                    q.prefix in ("Q", "M", "J") and len(q.nodes) >= 3 and q.nodes[2] in cin_cand[0].nodes
                    for q in comps
                )
                if is_to_emit or hint_y == 368:
                    y_shunt = 352
                    net_y_hint[n0] = 368
                else:
                    y_shunt = 272
                    net_y_hint[n0] = 288
            elif hint_y == 224:
                y_shunt = 208  # Aligns RLOAD top pin at 208 + 16 = 224 (Collector line!)
            elif hint_y == 368:
                y_shunt = 352  # Aligns RLOAD top pin at 352 + 16 = 368 (Emitter line!)
            elif hint_y == 288:
                y_shunt = 272  # Aligns shunt top pin at 272 + 16 = 288
            elif c.prefix == "C" and n1 == "0" and hint_y == 528:
                y_shunt = 560  # Aligns C_BYPASS top riser (-32) directly with E2_MID at y=528!
            else:
                y_shunt = 288

            p = sch.add_component(
                c.inst_name,
                c.symbol_type,
                x=col_x,
                y=y_shunt,
                value=c.value,
                orientation="R0",
                spice_line=c.spice_line,
                prefix="X" if c.prefix == "X" else None,
            )
            if c.prefix == "V" and n0 != "0":
                sch.add_flag(p.pin("top"), n0, stub_dx=32, stub_dy=0)
            else:
                sch.power_pin(p.pin("top"), n0, stub_dy=-32, stub_dx=32) if n0 != "0" else sch.ground_pin(p.pin("top"), stub_dy=-32)
            sch.ground_pin(p.pin("bot"), stub_dy=32) if n1 == "0" else sch.add_flag(p.pin("bot"), n1, stub_dy=32, stub_dx=32)
            col_x += 208
        _mark_visited(col)

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

        r_divs = [c for c in col if c.prefix == "R"]
        c_divs = [c for c in col if c.prefix == "C"]
        if len(r_divs) == 2 and len(col) == 2 + len(c_divs) and len(c_divs) <= 1:
            r_top_c, r_bot_c = r_divs[0], r_divs[1]
            r_top = sch.add_component(r_top_c.inst_name, "res", x=col_x, y=112, value=r_top_c.value)
            r_bot = sch.add_component(r_bot_c.inst_name, "res", x=col_x, y=368, value=r_bot_c.value)
            _connect_top_pin(r_top.pin("top"), r_top_c.nodes[0])
            sch.connect_points(r_top.pin("bot"), r_bot.pin("top"))
            mid_y = ((r_top.pin("bot")[1] + r_bot.pin("top")[1]) // 32) * 16
            sch.add_flag((r_top.pin("bot")[0], mid_y), r_top_c.nodes[1], stub_dx=32, stub_dy=0)
            sch.ground_pin(r_bot.pin("bot"), stub_dy=32)
            if c_divs:
                cb_c = c_divs[0]
                cb_p = sch.add_component(cb_c.inst_name, "cap", x=col_x + 96, y=384, value=cb_c.value, orientation="R0")
                sch.connect_points((r_bot.pin("top")[0], 384), cb_p.pin("top"), vh_order="HV")
                sch.ground_pin(cb_p.pin("bot"), stub_dy=32)
                col_x += 256
            else:
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
            ce_items = [c for c in col if c.prefix == "C"]
            if len(re_items) == 1:
                re1_c = re_items[0]
                re1_p = sch.add_component(re1_c.inst_name, "res", x=col_x + 48, y=400, value=re1_c.value)
                sch.connect_points(q_placed.pin("E"), re1_p.pin("top"))
                sch.add_flag((q_placed.pin("E")[0], 368), e_node, stub_dx=48, stub_dy=0)
                sch.ground_pin(re1_p.pin("bot"), stub_dy=32)
                if ce_items:
                    ce_c = ce_items[0]
                    ce_p = sch.add_component(ce_c.inst_name, "cap", x=col_x + 48 + 96, y=416, value=ce_c.value, orientation="R0")
                    sch.add_wire(re1_p.pin("top")[0], 416, ce_p.pin("top")[0], 416)
                    sch.ground_pin(ce_p.pin("bot"), stub_dy=32)
                    col_x += 304
                else:
                    col_x += 256
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
                if ce_items:
                    ce_c = ce_items[0]
                    ce_p = sch.add_component(ce_c.inst_name, "cap", x=col_x + 48 + 96, y=576, value=ce_c.value, orientation="R0")
                    sch.add_wire(re2_p.pin("top")[0], 576, ce_p.pin("top")[0], 576)
                    sch.ground_pin(ce_p.pin("bot"), stub_dy=32)
                    col_x += 304
                else:
                    col_x += 256
            else:
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
