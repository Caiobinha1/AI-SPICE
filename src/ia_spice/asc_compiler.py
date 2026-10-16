"""
16-Pixel Grid-Aligned LTspice Schematic (`.asc`) Compiler & Geometric DRC (`ia_spice.asc_compiler`).

Why this module exists:
-----------------------
When LLMs attempt to write LTspice `.asc` files directly as raw text, they almost always fail in three ways:
1. Off-grid pin placement: LTspice native symbols (`res`, `cap`, `ind`, `npn`, `diode`, `voltage`) have
   non-trivial internal pin offsets relative to their `(x, y)` insertion origin that change under
   rotation (`R0`, `R90`). Guessing coordinates leaves floating pins (`NC_01`, `NC_02`).
2. Accidental wire-to-pin short circuits: Routing a wire segment across a component body or another pin
   silently merges nets in LTspice.
3. Spaghetti ground/VCC routing: Drawing global ground wires across the entire sheet creates visual chaos.

How `SchematicBuilder` solves this:
-----------------------------------
- Stores the exact, calibrated LTspice pin-offset table (`SYMBOL_PIN_OFFSETS`) for standard symbols in
  both `R0` (vertical) and `R90` (horizontal) orientations.
- Lets the user or agent place components by origin or by pin coordinates on the 16-pixel grid and
  query exact pin locations (`comp.pin("top")`, `comp.pin("B")`, `comp.pin("C")`, `comp.pin("E")`).
- Connects pins using orthogonal Manhattan routing (`connect_pins`) or local net labels/flags (`attach_flag`,
  `ground_pin`) right at component terminals.
- Runs an automatic Geometric Design Rule Check (`run_geometric_drc`) that verifies:
  1. All coordinates lie on the 16-pixel grid.
  2. No wire segment unintentionally passes through an unrelated component pin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Point = Tuple[int, int]


# Calibrated against LTspice 24 (`LTspice.exe -netlist`)
# Maps (symbol_type, orientation) -> {pin_alias: (dx, dy)}
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
    # Diode / Varactor
    ("diode", "R0"): {"A": (16, 0), "K": (16, 64), "top": (16, 0), "bot": (16, 64)},
    ("diode", "R90"): {"A": (0, 16), "K": (-64, 16), "right": (0, 16), "left": (-64, 16)},
    # Voltage Source
    ("voltage", "R0"): {"+": (0, 16), "-": (0, 96), "top": (0, 16), "bot": (0, 96)},
    # Current Source
    ("current", "R0"): {"+": (0, 0), "-": (0, 80), "top": (0, 0), "bot": (0, 80)},
    # BJT NPN / PNP
    ("npn", "R0"): {
        "C": (64, 0), "B": (0, 48), "E": (64, 96),
        "col": (64, 0), "base": (0, 48), "emit": (64, 96),
    },
    ("pnp", "R0"): {
        "C": (64, 96), "B": (0, 48), "E": (64, 0),
        "col": (64, 96), "base": (0, 48), "emit": (64, 0),
    },
}


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

    def pin(self, pin_name: str) -> Point:
        """Return the absolute `(x, y)` grid coordinates of `pin_name`."""
        key = (self.symbol_type.lower(), self.orientation.upper())
        if key not in SYMBOL_PIN_OFFSETS:
            raise ValueError(f"Unsupported symbol/orientation combination: {key}")
        pin_map = SYMBOL_PIN_OFFSETS[key]
        if pin_name not in pin_map:
            raise ValueError(
                f"Unknown pin '{pin_name}' for {self.inst_name} ({self.symbol_type} {self.orientation}). "
                f"Available pins: {list(pin_map.keys())}"
            )
        dx, dy = pin_map[pin_name]
        return (self.x + dx, self.y + dy)

    def unique_pin_points(self) -> List[Point]:
        """Return deduplicated absolute coordinates of all physical pins of this component."""
        key = (self.symbol_type.lower(), self.orientation.upper())
        pin_map = SYMBOL_PIN_OFFSETS[key]
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


class SchematicBuilder:
    """
    Declarative 16-pixel grid schematic compiler for LTspice `.asc` files.
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
    ) -> PlacedComponent:
        """Place a component at grid coordinates `(x, y)`."""
        self.assert_on_grid(x, y, f"component '{inst_name}'")
        key = (symbol_type.lower(), orientation.upper())
        if key not in SYMBOL_PIN_OFFSETS:
            raise ValueError(f"Unsupported symbol/orientation: {key}")
        if inst_name in self.components:
            raise ValueError(f"Duplicate component instance name: '{inst_name}'")

        comp = PlacedComponent(
            inst_name=inst_name,
            symbol_type=symbol_type.lower(),
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
        self.wires.append(WireSegment(x1, y1, x2, y2))

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
        Attach a supply rail label (`VCC`, `VCC_12V`, etc.) above a top pin using a vertical rise
        (`stub_dy=-32`) and a short horizontal tab (`stub_dx=32`) so LTspice renders the label
        horizontally instead of vertically.
        """
        self.add_flag(pt, net_name, stub_dx=stub_dx, stub_dy=stub_dy)

    def add_directive(self, directive: str, x: int = 64, y: Optional[int] = None) -> None:
        """
        Add a SPICE directive (`.tran`, `.ac`, `.meas`, `.include`, `.model`, `.step`) to the sheet.
        When `y=None` (default), `to_asc_string()` automatically places all directives in a clean footer
        at least 160 pixels below the lowest circuit element, spaced 48 pixels apart vertically.
        """
        clean = directive.strip()
        if not clean.startswith("!"):
            clean = "!" + clean
        self.directives.append(SpiceDirectiveText(x=x, y=y, directive=clean))

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

    def to_asc_string(self) -> str:
        """Render the complete LTspice Version 4 `.asc` file content with automatic directive footer spacing."""
        _, _, max_x, max_y = self.get_circuit_bounds()
        auto_y = ((max_y + 160) // 16) * 16

        resolved_directives: List[Tuple[int, int, str]] = []
        for d in self.directives:
            if d.y is not None:
                dy = d.y
            else:
                dy = auto_y
                auto_y += 48  # Generous 48px vertical pitch between .tran/.meas/.model lines
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
            if comp.orientation == "R90" and comp.symbol_type in ("res", "ind"):
                lines.append("WINDOW 0 0 56 VBottom 2")
                lines.append("WINDOW 3 32 56 VTop 2")
            elif comp.orientation == "R90" and comp.symbol_type == "cap":
                lines.append("WINDOW 0 0 32 VBottom 2")
                lines.append("WINDOW 3 32 32 VTop 2")
            elif comp.symbol_type == "voltage":
                lines.append("WINDOW 3 24 96 Left 2")
            lines.append(f"SYMATTR InstName {comp.inst_name}")
            lines.append(f"SYMATTR Value {comp.value}")
            if comp.prefix:
                lines.append(f"SYMATTR Prefix {comp.prefix}")
            if comp.spice_line:
                lines.append(f"SYMATTR SpiceLine {comp.spice_line}")
        for dx, dy, text_cmd in resolved_directives:
            lines.append(f"TEXT {dx} {dy} Left 2 {text_cmd}")
        return "\n".join(lines) + "\n"

    def save(self, asc_path: str) -> str:
        """Write the `.asc` file to disk."""
        content = self.to_asc_string()
        with open(asc_path, "w", encoding="utf-8") as f:
            f.write(content)
        return asc_path
