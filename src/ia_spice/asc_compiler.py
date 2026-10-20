"""
16-Pixel Grid-Aligned LTspice Schematic (`.asc`) Compiler & Geometric DRC (`ia_spice.asc_compiler`).

Why this module exists:
-----------------------
When LLMs attempt to write LTspice `.asc` files directly as raw text, they almost always fail in three ways:
1. Off-grid pin placement: LTspice native symbols (`res`, `cap`, `ind`, `npn`, `pnp`, `diode`, `voltage`,
   `Opamps\\LT1360`, `Opamps\\opamp2`, etc.) have non-trivial internal pin offsets relative to their `(x, y)`
   insertion origin that change under rotation/mirroring (`R0`, `R90`, `R180`, `R270`, `M0`, `M180`).
   Guessing coordinates leaves floating pins (`NC_01`, `NC_02`).
2. Accidental wire-to-pin short circuits: Routing a wire segment across a component body or another pin
   silently merges nets in LTspice.
3. Disconnected label-islands or overlapping text: Placing components without physical signal wires or
   routing horizontal stubs straight across vertical pins overlaps `InstName`/`Value` text.

How `SchematicBuilder` solves this:
-----------------------------------
- Stores the exact, calibrated LTspice pin-offset table (`SYMBOL_PIN_OFFSETS`) for standard symbols in
  all orientations (`R0`, `R90`, `M180`, etc.), plus dynamic `.asy` library resolution.
- Connects signal paths with real orthogonal Manhattan wires (`WIRE`) while keeping power rails (`VCC`,
  `VEE`, `VDD`, `VSS`) and Ground (`0`) on clean local flags/symbols.
- Runs `sanitize_visual_layout()` automatically so no wire stub or flag ever collides with component text
  (`VIN`, `10u`, `1N4148`, `U1`, `LT1360`), and places SPICE directives in a clean footer `>= 160 px`
  below the circuit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

Point = Tuple[int, int]


def _transform_offset(dx: int, dy: int, orientation: str) -> Point:
    """Apply LTspice symbol rotation/mirror transform to a raw `R0` `(dx, dy)` pin offset."""
    ori = orientation.upper()
    if ori == "R0":
        return (dx, dy)
    if ori == "R90":
        return (-dy, dx)
    if ori == "R180":
        return (-dx, -dy)
    if ori == "R270":
        return (dy, -dx)
    if ori == "M0":
        return (-dx, dy)
    if ori == "M180":
        return (dx, -dy)
    if ori == "M90":
        return (dy, dx)
    if ori == "M270":
        return (-dy, -dx)
    return (dx, dy)


# Calibrated against LTspice 24 (`LTspice.exe -netlist`) and native `.asy` files
SYMBOL_PIN_OFFSETS: Dict[Tuple[str, str], Dict[str, Point]] = {
    # Resistor
    ("res", "R0"): {"1": (16, 16), "2": (16, 96), "top": (16, 16), "bot": (16, 96)},
    ("res", "R90"): {"1": (-16, 16), "2": (-96, 16), "right": (-16, 16), "left": (-96, 16)},
    # Capacitor
    ("cap", "R0"): {"1": (16, 0), "2": (16, 64), "top": (16, 0), "bot": (16, 64)},
    ("cap", "R90"): {"1": (0, 16), "2": (-64, 16), "right": (0, 16), "left": (-64, 16)},
    # Inductor
    ("ind", "R0"): {"1": (16, 16), "2": (16, 96), "top": (16, 16), "bot": (16, 96)},
    ("ind", "R90"): {"1": (-16, 16), "2": (-96, 16), "right": (-16, 16), "left": (-96, 16)},
    # Diode / Varactor / Zener / Schottky / LED
    ("diode", "R0"): {"A": (16, 0), "K": (16, 64), "top": (16, 0), "bot": (16, 64), "1": (16, 0), "2": (16, 64)},
    ("diode", "R90"): {"A": (0, 16), "K": (-64, 16), "right": (0, 16), "left": (-64, 16), "1": (0, 16), "2": (-64, 16)},
    ("zener", "R0"): {"A": (16, 0), "K": (16, 64), "top": (16, 0), "bot": (16, 64), "1": (16, 0), "2": (16, 64)},
    ("schottky", "R0"): {"A": (16, 0), "K": (16, 64), "top": (16, 0), "bot": (16, 64), "1": (16, 0), "2": (16, 64)},
    ("led", "R0"): {"A": (16, 0), "K": (16, 64), "top": (16, 0), "bot": (16, 64), "1": (16, 0), "2": (16, 64)},
    ("varactor", "R0"): {"A": (16, 0), "K": (16, 64), "top": (16, 0), "bot": (16, 64), "1": (16, 0), "2": (16, 64)},
    # Voltage Source
    ("voltage", "R0"): {"+": (0, 16), "-": (0, 96), "top": (0, 16), "bot": (0, 96), "1": (0, 16), "2": (0, 96)},
    # Current Source
    ("current", "R0"): {"+": (0, 0), "-": (0, 80), "top": (0, 0), "bot": (0, 80), "1": (0, 0), "2": (0, 80)},
    # BJT NPN
    ("npn", "R0"): {
        "C": (64, 0), "B": (0, 48), "E": (64, 96),
        "col": (64, 0), "base": (0, 48), "emit": (64, 96),
        "1": (64, 0), "2": (0, 48), "3": (64, 96),
    },
    # BJT PNP (R0 has C at top (64,0) and E at bottom (64,96) in LTspice's pnp.asy;
    # M180 flips vertically so E is at top (64,-96) and C is at bottom (64,0) for push-pull stages!)
    ("pnp", "R0"): {
        "C": (64, 0), "B": (0, 48), "E": (64, 96),
        "col": (64, 0), "base": (0, 48), "emit": (64, 96),
        "1": (64, 0), "2": (0, 48), "3": (64, 96),
    },
    ("pnp", "M180"): {
        "E": (64, -96), "B": (0, -48), "C": (64, 0),
        "emit": (64, -96), "base": (0, -48), "col": (64, 0),
        "1": (64, 0), "2": (0, -48), "3": (64, -96),
    },
    # MOSFET NMOS & PMOS (3-pin and 4-pin native LTspice symbols)
    ("nmos", "R0"): {
        "D": (48, 0), "G": (0, 80), "S": (48, 96),
        "C": (48, 0), "B": (0, 80), "E": (48, 96),
        "1": (48, 0), "2": (0, 80), "3": (48, 96),
    },
    ("pmos", "R0"): {
        "D": (48, 0), "G": (0, 80), "S": (48, 96),
        "C": (48, 0), "B": (0, 80), "E": (48, 96),
        "1": (48, 0), "2": (0, 80), "3": (48, 96),
    },
    ("pmos", "M180"): {
        "S": (48, -96), "G": (0, -80), "D": (48, 0),
        "E": (48, -96), "B": (0, -80), "C": (48, 0),
        "1": (48, 0), "2": (0, -80), "3": (48, -96),
    },
    ("nmos4", "R0"): {
        "D": (48, 0), "G": (0, 80), "S": (48, 96), "B": (48, 48),
        "1": (48, 0), "2": (0, 80), "3": (48, 96), "4": (48, 48),
    },
    ("pmos4", "R0"): {
        "D": (48, 0), "G": (0, 80), "S": (48, 96), "B": (48, 48),
        "1": (48, 0), "2": (0, 80), "3": (48, 96), "4": (48, 48),
    },
    # JFET NJF & PJF
    ("njf", "R0"): {
        "D": (48, 0), "G": (0, 64), "S": (48, 96),
        "C": (48, 0), "B": (0, 64), "E": (48, 96),
        "1": (48, 0), "2": (0, 64), "3": (48, 96),
    },
    ("pjf", "R0"): {
        "D": (48, 0), "G": (0, 64), "S": (48, 96),
        "C": (48, 0), "B": (0, 64), "E": (48, 96),
        "1": (48, 0), "2": (0, 64), "3": (48, 96),
    },
    # 5-Pin Op-Amps centered at origin (e.g. Opamps\LT1360, Opamps\UniversalOpamp2)
    ("opamps\\lt1360", "R0"): {
        "in+": (-32, 16), "in-": (-32, -16), "v+": (0, -32), "v-": (0, 32), "out": (32, 0),
        "1": (-32, 16), "2": (-32, -16), "3": (0, -32), "4": (0, 32), "5": (32, 0),
    },
    ("lt1360", "R0"): {
        "in+": (-32, 16), "in-": (-32, -16), "v+": (0, -32), "v-": (0, 32), "out": (32, 0),
        "1": (-32, 16), "2": (-32, -16), "3": (0, -32), "4": (0, 32), "5": (32, 0),
    },
    ("opamps\\universalopamp2", "R0"): {
        "in+": (-32, 16), "in-": (-32, -16), "v+": (0, -32), "v-": (0, 32), "out": (32, 0),
        "1": (-32, 16), "2": (-32, -16), "3": (0, -32), "4": (0, 32), "5": (32, 0),
    },
    ("universalopamp2", "R0"): {
        "in+": (-32, 16), "in-": (-32, -16), "v+": (0, -32), "v-": (0, 32), "out": (32, 0),
        "1": (-32, 16), "2": (-32, -16), "3": (0, -32), "4": (0, 32), "5": (32, 0),
    },
    # 5-Pin Generic Third-Party Op-Amp Symbol (Opamps\opamp2)
    ("opamps\\opamp2", "R0"): {
        "in+": (-32, 80), "in-": (-32, 48), "v+": (0, 32), "v-": (0, 96), "out": (32, 64),
        "1": (-32, 80), "2": (-32, 48), "3": (0, 32), "4": (0, 96), "5": (32, 64),
    },
    ("opamp2", "R0"): {
        "in+": (-32, 80), "in-": (-32, 48), "v+": (0, 32), "v-": (0, 96), "out": (32, 64),
        "1": (-32, 80), "2": (-32, 48), "3": (0, 32), "4": (0, 96), "5": (32, 64),
    },
}


def resolve_symbol_pin_map(symbol_type: str, orientation: str = "R0") -> Optional[Dict[str, Point]]:
    """
    Return the pin offset map `{pin_name: (dx, dy)}` for `(symbol_type, orientation)`.
    Checks `SYMBOL_PIN_OFFSETS` first, then transforms `R0` if available, or inspects
    LTspice's local `lib/sym` `.asy` library automatically.
    """
    sym_norm = symbol_type.strip().lower().replace("/", "\\")
    ori_norm = orientation.strip().upper()
    if (sym_norm, ori_norm) in SYMBOL_PIN_OFFSETS:
        return SYMBOL_PIN_OFFSETS[(sym_norm, ori_norm)]
    if (sym_norm, "R0") in SYMBOL_PIN_OFFSETS:
        base_map = SYMBOL_PIN_OFFSETS[(sym_norm, "R0")]
        transformed = {k: _transform_offset(dx, dy, ori_norm) for k, (dx, dy) in base_map.items()}
        SYMBOL_PIN_OFFSETS[(sym_norm, ori_norm)] = transformed
        return transformed

    # Fallback: check LTspice's native .asy library on disk
    local_app = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    sym_root = os.path.join(local_app, "LTspice", "lib", "sym")
    candidates = [
        os.path.join(sym_root, f"{symbol_type}.asy"),
        os.path.join(sym_root, "Opamps", f"{ os.path.basename(symbol_type) }.asy"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            pin_map: Dict[str, Point] = {}
            cur_pt: Optional[Point] = None
            cur_name: Optional[str] = None
            cur_order: Optional[str] = None
            with open(cand, "r", encoding="latin-1", errors="replace") as f:
                for raw in f:
                    line = raw.strip()
                    if line.startswith("PIN "):
                        parts = line.split()
                        if len(parts) >= 3:
                            cur_pt = _transform_offset(int(parts[1]), int(parts[2]), ori_norm)
                            cur_name = None
                            cur_order = None
                    elif line.startswith("PINATTR PinName ") and cur_pt is not None:
                        cur_name = line.split("PINATTR PinName ", 1)[1].strip()
                        pin_map[cur_name] = cur_pt
                        pin_map[cur_name.lower()] = cur_pt
                    elif line.startswith("PINATTR SpiceOrder ") and cur_pt is not None:
                        cur_order = line.split("PINATTR SpiceOrder ", 1)[1].strip()
                        pin_map[cur_order] = cur_pt
            if pin_map:
                SYMBOL_PIN_OFFSETS[(sym_norm, ori_norm)] = pin_map
                return pin_map
    return None


@dataclass
class PlacedComponent:
    """A component instance placed on a `SchematicBuilder` canvas."""
    inst_name: str
    symbol_type: str
    x: int
    y: int
    orientation: str = "R0"
    value: str = ""
    spice_line: Optional[str] = None
    prefix: Optional[str] = None
    extra_attrs: Dict[str, str] = field(default_factory=dict)
    custom_windows: List[str] = field(default_factory=list)

    def pin(self, pin_name: str) -> Point:
        """Return the absolute `(x, y)` grid coordinates of `pin_name`."""
        pin_map = resolve_symbol_pin_map(self.symbol_type, self.orientation)
        if not pin_map:
            raise ValueError(f"Unsupported symbol/orientation combination: {(self.symbol_type, self.orientation)}")
        if pin_name in pin_map:
            dx, dy = pin_map[pin_name]
            return (self.x + dx, self.y + dy)
        if pin_name.lower() in pin_map:
            dx, dy = pin_map[pin_name.lower()]
            return (self.x + dx, self.y + dy)
        raise ValueError(
            f"Unknown pin '{pin_name}' for {self.inst_name} ({self.symbol_type} {self.orientation}). "
            f"Available pins: {list(pin_map.keys())}"
        )

    def unique_pin_points(self) -> List[Point]:
        """Return deduplicated absolute coordinates of all physical pins of this component."""
        pin_map = resolve_symbol_pin_map(self.symbol_type, self.orientation)
        if not pin_map:
            return []
        pts = {(self.x + dx, self.y + dy) for (dx, dy) in pin_map.values()}
        return sorted(pts)


@dataclass
class WireSegment:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class FlagLabel:
    x: int
    y: int
    net_name: str


@dataclass
class SpiceDirectiveText:
    x: int
    y: Optional[int]
    directive: str
    is_comment: bool = False


class SchematicBuilder:
    """
    Declarative 16-pixel grid schematic compiler for LTspice `.asc` files.
    Includes automatic engine-level visual sanitization (`sanitize_visual_layout`) so that:
    1. Net labels (`FLAG`) never sit on vertical-only pins (`top`/`bot`/`C`/`E`/`V+`/`V-`) where LTspice
       would rotate them 90 deg vertically, nor on immediate horizontal stubs cutting across component text.
    2. Ground symbols (`FLAG 0`) never sit directly on a bottom component pin where they would overlap value text.
    3. SPICE directives (`.tran`, `.meas`, `.model`, `.include`) are always placed in a clean footer >=160px
       below the lowest circuit element, spaced 48px apart vertically.
    """

    def __init__(self, width: int = 1200, height: int = 800):
        self.width = width
        self.height = height
        self.components: Dict[str, PlacedComponent] = {}
        self.wires: List[WireSegment] = []
        self.flags: List[FlagLabel] = []
        self.directives: List[SpiceDirectiveText] = []

    @staticmethod
    def assert_on_grid(x: int, y: int, context: str = "") -> None:
        if x % 16 != 0 or y % 16 != 0:
            raise ValueError(
                f"Off-grid coordinate ({x}, {y}) in {context}. All LTspice coordinates must be multiples of 16."
            )

    def add_component(
        self,
        inst_name: str,
        symbol_type: str,
        x: int,
        y: int,
        value: str,
        orientation: str = "R0",
        spice_line: Optional[str] = None,
        prefix: Optional[str] = None,
        allow_custom_symbol: bool = False,
    ) -> PlacedComponent:
        """Place a component at grid coordinates `(x, y)`."""
        self.assert_on_grid(x, y, f"component '{inst_name}'")
        pin_map = resolve_symbol_pin_map(symbol_type, orientation)
        if not allow_custom_symbol and not pin_map:
            raise ValueError(f"Unsupported symbol/orientation: {(symbol_type, orientation)}")
        if inst_name in self.components:
            raise ValueError(f"Duplicate component instance name: '{inst_name}'")

        preserve_case = "\\" in symbol_type or "/" in symbol_type or allow_custom_symbol
        comp = PlacedComponent(
            inst_name=inst_name,
            symbol_type=symbol_type if preserve_case else symbol_type.lower(),
            x=x,
            y=y,
            orientation=orientation.upper(),
            value=value,
            spice_line=spice_line,
            prefix=prefix,
        )
        self.components[inst_name] = comp
        return comp

    def add_wire(self, x1: int, y1: int, x2: int, y2: int) -> None:
        """Add an orthogonal (horizontal or vertical) wire segment."""
        self.assert_on_grid(x1, y1, "wire start")
        self.assert_on_grid(x2, y2, "wire end")
        if x1 == x2 and y1 == y2:
            return
        if x1 != x2 and y1 != y2:
            raise ValueError(
                f"Diagonal wire ({x1},{y1})->({x2},{y2}) is forbidden. Use Manhattan routing (`connect_points`)."
            )
        seg = WireSegment(x1, y1, x2, y2)
        if seg not in self.wires and WireSegment(x2, y2, x1, y1) not in self.wires:
            self.wires.append(seg)

    def connect_points(self, p1: Point, p2: Point, vh_order: str = "HV") -> None:
        """
        Connect two grid points `p1` and `p2` using 1 or 2 orthogonal wire segments.
        `vh_order="HV"` routes horizontally first, then vertically; `"VH"` routes vertically first.
        """
        x1, y1 = p1
        x2, y2 = p2
        if x1 == x2 or y1 == y2:
            self.add_wire(x1, y1, x2, y2)
        elif vh_order.upper() == "HV":
            self.add_wire(x1, y1, x2, y1)
            self.add_wire(x2, y1, x2, y2)
        else:
            self.add_wire(x1, y1, x1, y2)
            self.add_wire(x1, y2, x2, y2)

    def add_flag(self, pt: Point, net_name: str, stub_dx: int = 0, stub_dy: int = 0) -> None:
        """Attach a net label (`FLAG x y net_name`) directly at `pt` or via a short orthogonal stub."""
        x, y = pt
        self.assert_on_grid(x, y, f"flag '{net_name}'")
        if stub_dx != 0 or stub_dy != 0:
            fx, fy = x + stub_dx, y + stub_dy
            self.connect_points((x, y), (fx, fy), vh_order="VH" if stub_dy != 0 else "HV")
            self.flags.append(FlagLabel(fx, fy, net_name))
        else:
            self.flags.append(FlagLabel(x, y, net_name))

    def ground_pin(self, pt: Point, stub_dy: int = 32) -> None:
        """
        Attach LTspice Ground (`FLAG 0`) to a component pin via a clean vertical wire stub
        (`stub_dy=32` by default so the GND triangle never collides with component value labels).
        """
        self.add_flag(pt, "0", stub_dy=stub_dy)

    def power_pin(self, pt: Point, net_name: str = "VCC", stub_dy: int = -32, stub_dx: int = 32) -> None:
        """
        Attach a supply rail label (`VCC`, `VEE`, etc.) above/below a pin using a vertical rise/drop
        (`stub_dy`) and a short horizontal tab (`stub_dx=32`) so LTspice renders the label
        horizontally and away from component text.
        """
        self.add_flag(pt, net_name, stub_dx=stub_dx, stub_dy=stub_dy)

    def add_directive(self, directive: str, x: int = 64, y: Optional[int] = None, is_comment: bool = False) -> None:
        """
        Add a SPICE directive (`.tran`, `.ac`, `.meas`, `.include`, `.model`, `.step`) or comment to the sheet.
        When `y=None` (default), `to_asc_string()` automatically places all directives in a clean footer
        at least 160 pixels below the lowest circuit element, spaced 48 pixels apart vertically.
        """
        clean = directive.strip()
        if is_comment:
            if not clean.startswith(";"):
                clean = ";" + clean.lstrip("!;")
        else:
            if not clean.startswith("!") and not clean.startswith(";"):
                clean = "!" + clean
        self.directives.append(SpiceDirectiveText(x=x, y=y, directive=clean, is_comment=is_comment))

    def _point_on_horizontal_wire(self, x: int, y: int) -> bool:
        for w in self.wires:
            if w.y1 == w.y2 == y and min(w.x1, w.x2) <= x <= max(w.x1, w.x2):
                return True
        return False

    def _point_on_vertical_wire(self, x: int, y: int) -> bool:
        for w in self.wires:
            if w.x1 == w.x2 == x and min(w.y1, w.y2) <= y <= max(w.y1, w.y2):
                return True
        return False

    def _classify_pin_at(self, pt: Point) -> Optional[str]:
        """Return `'top'`, `'bot'`, `'left'`, or `'right'` if `pt` is a component pin."""
        for comp in self.components.values():
            pin_map = resolve_symbol_pin_map(comp.symbol_type, comp.orientation)
            if not pin_map:
                continue
            for pname, (dx, dy) in pin_map.items():
                if (comp.x + dx, comp.y + dy) == pt:
                    pl = pname.lower()
                    if comp.orientation == "M180" and comp.symbol_type.lower() == "pnp":
                        if pl in ("e", "emit", "3"):
                            return "top"
                        if pl in ("c", "col", "1"):
                            return "bot"
                        return "left"
                    if pl in ("top", "c", "col", "v+", "3") and dy <= 16:
                        return "top"
                    if pl in ("bot", "e", "emit", "v-", "4") and dy >= 32:
                        return "bot"
                    if comp.orientation == "R0" and pl in ("1", "+", "a"):
                        return "top"
                    if comp.orientation == "R0" and pl in ("2", "-", "k"):
                        return "bot"
                    if pl in ("b", "base", "left", "in+", "in-") or (comp.orientation == "R90" and pl in ("2", "k")):
                        return "left"
                    return "right"
        return None

    def sanitize_visual_layout(self) -> None:
        """
        Engine-level visual layout sanitizer:
        1. Fixes horizontal stubs drawn directly from a vertical pin (`top`, `bot`, `V+`, `V-`) across
           the right-hand text area of a component (`VIN`, `CIN`, `D1`, `U1`, etc.) by adding a
           `-32 px` (top) or `+32 px` (bottom) vertical clearance riser first.
        2. Converts any net `FLAG` sitting directly on a vertical pin (`top`/`bot`/`C`/`E`) or vertical-only
           wire endpoint into a horizontal flag tab so LTspice renders 100% of net labels horizontally (`0 deg`).
        3. Adds a `32 px` vertical wire stub to any Ground (`FLAG 0`) sitting directly on a bottom component pin.
        """
        all_occupied_pts: Set[Point] = {pt for c in self.components.values() for pt in c.unique_pin_points()}

        # Step 1: Fix horizontal-only wire stubs starting directly on a vertical pin (`top`/`bot`) and ending at a FLAG
        for fl in self.flags:
            if fl.net_name == "0":
                continue
            for w in list(self.wires):
                if w.y1 == w.y2 == fl.y and ((w.x2 == fl.x and w.x1 < w.x2) or (w.x1 == fl.x and w.x2 < w.x1)):
                    pin_x = min(w.x1, w.x2)
                    pin_y = w.y1
                    pdir = self._classify_pin_at((pin_x, pin_y))
                    if pdir in ("top", "bot") and not self._point_on_vertical_wire(pin_x, pin_y):
                        step_y = -32 if pdir == "top" else 32
                        new_y = max(16, pin_y + step_y)
                        if (pin_x, new_y) not in all_occupied_pts and (fl.x, new_y) not in all_occupied_pts:
                            self.wires.remove(w)
                            self.add_wire(pin_x, pin_y, pin_x, new_y)
                            self.add_wire(pin_x, new_y, fl.x, new_y)
                            fl.y = new_y
                            break

        # Step 2: Ensure all flags on vertical pins or vertical wire stubs have horizontal tabs
        for fl in self.flags:
            x, y = fl.x, fl.y
            pin_dir = self._classify_pin_at((x, y))
            on_h = self._point_on_horizontal_wire(x, y)
            on_v = self._point_on_vertical_wire(x, y)

            if fl.net_name == "0":
                if pin_dir == "bot" and not on_v and (x, y + 32) not in all_occupied_pts:
                    self.add_wire(x, y, x, y + 32)
                    fl.y = y + 32
                continue

            if on_h:
                continue

            if pin_dir == "top" and not on_v:
                ny = max(16, y - 32)
                nx = x + 32
                if (x, ny) not in all_occupied_pts and (nx, ny) not in all_occupied_pts:
                    self.add_wire(x, y, x, ny)
                    self.add_wire(x, ny, nx, ny)
                    fl.x, fl.y = nx, ny
            elif pin_dir == "bot" and not on_v:
                ny = y + 32
                nx = x + 32
                if (x, ny) not in all_occupied_pts and (nx, ny) not in all_occupied_pts:
                    self.add_wire(x, y, x, ny)
                    self.add_wire(x, ny, nx, ny)
                    fl.x, fl.y = nx, ny
            elif pin_dir == "left" and not on_h:
                nx = max(16, x - 32)
                if (nx, y) not in all_occupied_pts:
                    self.add_wire(x, y, nx, y)
                    fl.x = nx
            elif on_v and not on_h and pin_dir is None:
                nx = x + 32
                if (nx, y) not in all_occupied_pts:
                    self.add_wire(x, y, nx, y)
                    fl.x = nx

    def get_circuit_bounds(self) -> Tuple[int, int, int, int]:
        """Return `(min_x, min_y, max_x, max_y)` across all placed components, wires, and flags."""
        xs: List[int] = [64]
        ys: List[int] = [64]
        for comp in self.components.values():
            xs.extend([comp.x, comp.x + 96])
            ys.extend([comp.y, comp.y + 112])
            for px, py in comp.unique_pin_points():
                xs.append(px)
                ys.append(py)
        for w in self.wires:
            xs.extend([w.x1, w.x2])
            ys.extend([w.y1, w.y2])
        for fl in self.flags:
            xs.append(fl.x)
            ys.append(fl.y)
        return (min(xs), min(ys), max(xs), max(ys))

    def run_geometric_drc(self) -> List[str]:
        """
        Check for geometric and visual layout bugs:
        - Off-grid pins or flags
        - Crowded components whose labels/bodies would collide visually
        """
        issues: List[str] = []
        comp_list = list(self.components.values())
        for comp in comp_list:
            for px, py in comp.unique_pin_points():
                if px % 16 != 0 or py % 16 != 0:
                    issues.append(f"Off-grid pin on {comp.inst_name} at ({px}, {py})")

        for i in range(len(comp_list)):
            for j in range(i + 1, len(comp_list)):
                c1, c2 = comp_list[i], comp_list[j]
                dx = abs(c1.x - c2.x)
                dy = abs(c1.y - c2.y)
                if dx < 96 and dy < 112:
                    issues.append(
                        f"Crowded components '{c1.inst_name}' at ({c1.x},{c1.y}) and "
                        f"'{c2.inst_name}' at ({c2.x},{c2.y}): dx={dx}, dy={dy} causes text overlap."
                    )
        return issues

    def to_asc_string(self, auto_sanitize: bool = True) -> str:
        """
        Render the complete LTspice Version 4 `.asc` file content with automatic visual sanitization
        and directive footer spacing (`>= 160 px` below the lowest circuit element).
        """
        if auto_sanitize:
            self.sanitize_visual_layout()

        _, _, max_x, max_y = self.get_circuit_bounds()
        min_safe_directive_y = ((max_y + 160) // 16) * 16
        auto_y = min_safe_directive_y

        resolved_directives: List[Tuple[int, int, str]] = []
        for d in self.directives:
            if d.is_comment and d.y is not None:
                resolved_directives.append((d.x, d.y, d.directive))
            elif d.y is not None and d.y >= min_safe_directive_y:
                dy = d.y
                auto_y = max(auto_y, dy + 48)
                resolved_directives.append((d.x, dy, d.directive))
            else:
                dy = auto_y
                auto_y += 48
                resolved_directives.append((d.x, dy, d.directive))

        sheet_w = max(self.width, ((max_x + 224) // 16) * 16)
        sheet_h = max(self.height, ((auto_y + 96) // 16) * 16)

        lines: List[str] = [
            "Version 4",
            f"SHEET 1 {sheet_w} {sheet_h}",
        ]
        for w in self.wires:
            lines.append(f"WIRE {w.x1} {w.y1} {w.x2} {w.y2}")
        for fl in self.flags:
            lines.append(f"FLAG {fl.x} {fl.y} {fl.net_name}")
        for comp in self.components.values():
            lines.append(f"SYMBOL {comp.symbol_type} {comp.x} {comp.y} {comp.orientation}")
            if comp.custom_windows:
                for win_line in comp.custom_windows:
                    lines.append(win_line)
            else:
                if comp.orientation == "R90" and comp.symbol_type in ("res", "ind"):
                    lines.append("WINDOW 0 0 56 VBottom 2")
                    lines.append("WINDOW 3 32 56 VTop 2")
                elif comp.orientation == "R90" and comp.symbol_type == "cap":
                    lines.append("WINDOW 0 0 32 VBottom 2")
                    lines.append("WINDOW 3 32 32 VTop 2")
                elif comp.symbol_type == "voltage":
                    lines.append("WINDOW 3 24 96 Left 2")
            lines.append(f"SYMATTR InstName {comp.inst_name}")
            if comp.value != "":
                lines.append(f"SYMATTR Value {comp.value}")
            if comp.prefix:
                lines.append(f"SYMATTR Prefix {comp.prefix}")
            if comp.spice_line:
                lines.append(f"SYMATTR SpiceLine {comp.spice_line}")
            for attr_k, attr_v in comp.extra_attrs.items():
                lines.append(f"SYMATTR {attr_k} {attr_v}")
        for dx, dy, text_cmd in resolved_directives:
            lines.append(f"TEXT {dx} {dy} Left 2 {text_cmd}")
        return "\n".join(lines) + "\n"

    def save(self, asc_path: str, auto_sanitize: bool = True) -> str:
        """Write the `.asc` file to disk."""
        content = self.to_asc_string(auto_sanitize=auto_sanitize)
        with open(asc_path, "w", encoding="utf-8") as f:
            f.write(content)
        return asc_path
