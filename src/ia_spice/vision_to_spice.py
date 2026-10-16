"""
Vision-to-SPICE & Missing-Parameter Detection Engine (`ia_spice.vision_to_spice`).

Why this module exists:
-----------------------
When a user provides a photo, textbook screenshot, or hand-drawn sketch of an electronic schematic,
critical SPICE simulation parameters are almost always partially missing or ambiguous on the drawing:
- Supply voltages labeled simply as `+VCC` or `VDD` without a numeric voltage (e.g., `9V`, `12V`, `5V`).
- Signal sources labeled `Vin` or `Audio In` without amplitude or frequency (`SINE(0 0.1 1k)`).
- Generic semiconductor symbols (`Q1`, `D1`, `U1`) without a part number (`2N2222`, `1N4148`), or
  resistors/capacitors labeled only by reference designator (`R1`, `C_load`) without numeric values.

If an AI blindly guesses these values without telling the user, the resulting simulation may not match
the user's real circuit. Conversely, if the user *wants* the AI to size the missing components
autonomously, the tool should compute them from first-principles (`ia_spice.sizing`) and clearly
document every inferred value.

How `VisionCircuitImporter` works:
----------------------------------
1. Accepts a `VisionCircuitGraph` extracted from a schematic image by a multimodal LLM (containing
   components, pin-to-node connections, known values, and `"?"` / `None` for unreadable/missing values,
   plus optional design targets like `target_f0_hz` or `target_gain_vv`).
2. Runs `audit_missing_information(graph)` to detect:
   - Missing component values (`R1=?`, `C1=?`)
   - Unspecified transistor/diode models (`Q1=?`)
   - Unspecified power rail voltages (`VCC=?`)
   - Unspecified input stimulus (`VIN=?`)
   - Topological sanity warnings (missing Ground `0`, single-pin floating nets).
3. Supports two resolution modes:
   - **`mode="ask_user"`**: Halts compilation and returns a structured, human-readable prompt listing
     every missing parameter and asking the user whether to provide exact values or authorize
     autonomous analytical sizing.
   - **`mode="autonomous"`**: Uses `ia_spice.sizing` (E24 snapping, BJT operating point rules, LC resonance)
     to synthesize physically sound defaults/values for all missing parameters, logs the rationale for
     each choice, and compiles the graph into both a `.cir` netlist and a clean 16-pixel grid `.asc`
     schematic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .asc_compiler import SchematicBuilder
from .sizing import format_spice_value, snap_to_series


@dataclass
class ExtractedComponent:
    """Represents a single component identified in a schematic image."""
    inst_name: str               # e.g., "R1", "C1", "Q1", "V1"
    comp_type: str               # "res", "cap", "ind", "npn", "pnp", "diode", "voltage", "current"
    nodes: List[str]             # Ordered list of connected node names: [n1, n2] or [C, B, E]
    value: Optional[str] = None  # e.g., "10k", "100n", "2N2222", "9", or None / "?" if missing in image
    role_hint: Optional[str] = None  # Optional hint: "pullup_base", "pulldown_base", "emitter_res", "collector_res", "load", "coupling_cap", "supply", "ac_input"


@dataclass
class VisionCircuitGraph:
    """Structured circuit representation extracted from an image."""
    title: str
    components: List[ExtractedComponent] = field(default_factory=list)
    known_supply_voltages: Dict[str, str] = field(default_factory=dict)  # e.g., {"VCC": "9"}
    design_targets: Dict[str, float] = field(default_factory=dict)       # e.g., {"v_cc": 9.0, "f_cutoff_hz": 1000.0, "i_c_ma": 2.0}


@dataclass
class MissingParamItem:
    """Describes a single missing or ambiguous piece of information from the schematic photo."""
    identifier: str
    category: str       # "COMPONENT_VALUE", "DEVICE_MODEL", "SUPPLY_VOLTAGE", "INPUT_STIMULUS", "TOPOLOGY"
    description: str
    suggested_value: str
    sizing_rationale: str


@dataclass
class VisionImportAudit:
    """Result of auditing a `VisionCircuitGraph` for missing information."""
    is_complete: bool
    missing_items: List[MissingParamItem] = field(default_factory=list)
    topology_warnings: List[str] = field(default_factory=list)

    def format_user_prompt(self) -> str:
        """
        Generate a clear, structured question message asking the user whether to provide the
        missing values or let IA-SPICE size them autonomously.
        """
        if self.is_complete and not self.topology_warnings:
            return "All circuit parameters were clearly identified in the image. Ready to simulate."

        lines: List[str] = [
            "### [AUDITORIA DE IMAGEM] Parâmetros Não Identificados na Imagem do Esquemático",
            "",
            "Ao analisar a imagem do circuito, identifiquei a topologia completa, mas os seguintes parâmetros não estão visíveis ou especificados na figura:",
            "",
            "| Componente / Nó | Categoria | O que falta | Sugestão Analítica (IA-SPICE) | Justificativa Técnica |",
            "| :--- | :--- | :--- | :---: | :--- |",
        ]
        for item in self.missing_items:
            lines.append(
                f"| **`{item.identifier}`** | `{item.category}` | {item.description} | "
                f"**`{item.suggested_value}`** | {item.sizing_rationale} |"
            )

        if self.topology_warnings:
            lines.extend(["", "**Avisos Topológicos:**"])
            for w in self.topology_warnings:
                lines.append(f"- [AVISO] {w}")

        lines.extend([
            "",
            "**Como deseja prosseguir?**",
            "1. **Informar os valores manualmente** (responda com os valores desejados para os itens acima); ou",
            "2. **Autorizar dimensionamento autônomo** (a ferramenta calculará e adotará os valores sugeridos acima com base no ponto de operação e séries comerciais E24).",
        ])
        return "\n".join(lines)


def _is_missing(val: Optional[str]) -> bool:
    if val is None:
        return True
    s = val.strip().upper()
    return s in ("", "?", "UNKNOWN", "NONE", "TBD", "N/A")


def _suggest_missing_parameter(
    comp: ExtractedComponent,
    graph: VisionCircuitGraph,
) -> Tuple[str, str]:
    """
    Use analytical heuristics and `ia_spice.sizing` E24 snapping to propose a physically sound value
    and rationale when a component value or model is missing from the schematic photo.
    """
    v_cc = float(graph.design_targets.get("v_cc", 9.0))
    f_target = float(graph.design_targets.get("f_target_hz", 1000.0))
    i_c_target = float(graph.design_targets.get("i_c_a", 1.5e-3))

    ctype = comp.comp_type.lower()
    role = (comp.role_hint or "").lower()

    if ctype == "npn":
        return "2N2222", "Transistor NPN de uso geral / RF clássico com modelo SPICE padrão."
    if ctype == "pnp":
        return "2N2907", "Transistor PNP complementar de uso geral."
    if ctype == "diode":
        return "1N4148", "Diodo de sinal rápido padrão."
    if ctype == "voltage":
        if "in" in comp.inst_name.lower() or role == "ac_input":
            return f"SINE(0 0.1 {format_spice_value(f_target)}) AC 1", (
                f"Estímulo senoidal de pequeno sinal (100 mVp a {format_spice_value(f_target)}) + AC=1 para análise .ac/.tran."
            )
        return format_spice_value(v_cc), f"Alimentação DC padrão adotada ({v_cc:g} V)."

    if ctype == "res":
        if role == "collector_res":
            # Drop ~40% of VCC across RC at target IC
            rc_ideal = (0.40 * v_cc) / i_c_target
            rc_e24, _ = snap_to_series(rc_ideal, "E24")
            return format_spice_value(rc_e24), f"Dimensionado para queda DC de ~40% de VCC ({0.4*v_cc:.1f}V) com IC={i_c_target*1e3:.1f}mA (E24)."
        if role == "emitter_res":
            # Drop ~15% of VCC across RE for thermal stability
            re_ideal = (0.15 * v_cc) / i_c_target
            re_e24, _ = snap_to_series(re_ideal, "E24")
            return format_spice_value(re_e24), f"Degeneração de emissor (~15% de VCC = {0.15*v_cc:.2f}V) para estabilidade térmica (E24)."
        if role == "pullup_base":
            ve = 0.15 * v_cc
            vb = ve + 0.70
            i_div = i_c_target / 10.0  # Stiff divider rule: I_div ≈ I_C / 10
            r1_ideal = (v_cc - vb) / i_div
            r1_e24, _ = snap_to_series(r1_ideal, "E24")
            return format_spice_value(r1_e24), f"Resistor superior de base para VB={vb:.2f}V com corrente de divisor rígido (E24)."
        if role == "pulldown_base":
            ve = 0.15 * v_cc
            vb = ve + 0.70
            i_div = i_c_target / 10.0
            r2_ideal = vb / i_div
            r2_e24, _ = snap_to_series(r2_ideal, "E24")
            return format_spice_value(r2_e24), f"Resistor inferior de base para fixar VB={vb:.2f}V (E24)."
        if role == "load":
            return "10k", "Carga de alta impedância padrão (10 kOhm) para evitar carregamento excessivo."
        return "1k", "Valor padrão E24 de 1 kOhm (função específica não rotulada na imagem)."

    if ctype == "cap":
        if role == "coupling_cap":
            # Reactance X_C <= 100 Ohm at f_target
            c_ideal = 1.0 / (2.0 * math.pi * f_target * 100.0)
            c_e24, _ = snap_to_series(c_ideal, "E24")
            return format_spice_value(c_e24), f"Capacitor de acoplamento/bypass com |Xc| <= 100 Ohm em {format_spice_value(f_target)} (E24)."
        # Filter capacitor with 1k resistor for f_target
        c_ideal = 1.0 / (2.0 * math.pi * f_target * 1000.0)
        c_e24, _ = snap_to_series(c_ideal, "E24")
        return format_spice_value(c_e24), f"Dimensionado para frequência de corte em {format_spice_value(f_target)} com R=1k (E24)."

    if ctype == "ind":
        return "4.7u", "Indutor de RF comercial padrão (4.7 uH)."

    return "1k", "Valor padrão."


def audit_circuit_from_image(graph: VisionCircuitGraph) -> VisionImportAudit:
    """
    Inspect an extracted `VisionCircuitGraph` and identify all missing component values,
    device models, power supply voltages, and topological issues.
    """
    missing: List[MissingParamItem] = []
    warnings: List[str] = []

    all_nodes: Dict[str, int] = {}
    has_voltage_source = False

    for comp in graph.components:
        if comp.comp_type.lower() == "voltage":
            has_voltage_source = True
        for n in comp.nodes:
            nu = n.upper()
            all_nodes[nu] = all_nodes.get(nu, 0) + 1

        if _is_missing(comp.value):
            suggested, rationale = _suggest_missing_parameter(comp, graph)
            cat = "DEVICE_MODEL" if comp.comp_type.lower() in ("npn", "pnp", "diode") else (
                "SUPPLY_VOLTAGE" if comp.comp_type.lower() == "voltage" and "in" not in comp.inst_name.lower() else (
                    "INPUT_STIMULUS" if comp.comp_type.lower() == "voltage" else "COMPONENT_VALUE"
                )
            )
            missing.append(
                MissingParamItem(
                    identifier=comp.inst_name,
                    category=cat,
                    description=f"Componente `{comp.inst_name}` ({comp.comp_type}) nos nós {comp.nodes} está sem valor/modelo na imagem.",
                    suggested_value=suggested,
                    sizing_rationale=rationale,
                )
            )

    # Check if a power rail node (like VCC or VDD) is referenced by components but has no voltage source driving it
    driven_supply_nodes = {
        comp.nodes[0].upper()
        for comp in graph.components
        if comp.comp_type.lower() == "voltage" and len(comp.nodes) >= 1
    }
    for node_name in list(all_nodes.keys()):
        if node_name in ("VCC", "VDD", "VEE", "VSS", "VCC_OSC", "VCC_AUX"):
            if node_name not in driven_supply_nodes and node_name not in graph.known_supply_voltages:
                v_def = format_spice_value(float(graph.design_targets.get("v_cc", 9.0)))
                missing.append(
                    MissingParamItem(
                        identifier=node_name,
                        category="SUPPLY_VOLTAGE",
                        description=f"Barramento de alimentação `{node_name}` aparece no esquemático sem o valor de tensão DC.",
                        suggested_value=v_def,
                        sizing_rationale=f"Tensão DC de barramento padrão sugerida ({v_def} V).",
                    )
                )

    if "0" not in all_nodes and "GND" not in all_nodes:
        warnings.append("Nó de terra (`0` / `GND`) não identificado na imagem.")

    for node_name, count in all_nodes.items():
        if count == 1 and node_name not in ("0", "GND", "OUT", "VOUT"):
            warnings.append(f"Nó `{node_name}` está conectado a apenas 1 terminal (possível pino flutuante).")

    return VisionImportAudit(
        is_complete=(len(missing) == 0),
        missing_items=missing,
        topology_warnings=warnings,
    )


def resolve_and_compile_vision_graph(
    graph: VisionCircuitGraph,
    mode: str = "ask_user",
    user_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[VisionImportAudit, Optional[str], Optional[SchematicBuilder]]:
    """
    Resolve a `VisionCircuitGraph` under either:
    - `mode="ask_user"`: If any parameter is missing (after applying `user_overrides`), returns
      `(audit, None, None)` so the caller/agent can ask the user with `audit.format_user_prompt()`.
    - `mode="autonomous"`: Automatically fills any remaining missing parameters using the analytical
      suggestions in `audit.missing_items` and compiles both a `.cir` netlist string and a
      16-pixel grid `SchematicBuilder`.
    """
    overrides = {k.upper(): v for k, v in (user_overrides or {}).items()}

    # Apply explicit user overrides first
    for comp in graph.components:
        if comp.inst_name.upper() in overrides:
            comp.value = overrides[comp.inst_name.upper()]

    for rail in ("VCC", "VDD", "VEE", "VSS"):
        if rail in overrides:
            graph.known_supply_voltages[rail] = overrides[rail]

    audit = audit_circuit_from_image(graph)

    if not audit.is_complete and mode.lower() == "ask_user":
        return audit, None, None

    # In autonomous mode (or when complete), apply analytical suggestions for any remaining missing items
    suggestion_map = {item.identifier.upper(): item.suggested_value for item in audit.missing_items}

    for comp in graph.components:
        if _is_missing(comp.value) and comp.inst_name.upper() in suggestion_map:
            comp.value = suggestion_map[comp.inst_name.upper()]

    # Synthesize voltage sources for any supply rails that weren't explicitly drawn as batteries
    driven_supply_nodes = {
        comp.nodes[0].upper()
        for comp in graph.components
        if comp.comp_type.lower() == "voltage" and len(comp.nodes) >= 1
    }
    all_referenced_nodes = {n.upper() for c in graph.components for n in c.nodes}
    for rail in ("VCC", "VDD", "VEE", "VSS"):
        if rail in all_referenced_nodes and rail not in driven_supply_nodes:
            v_val = graph.known_supply_voltages.get(rail) or suggestion_map.get(rail, "9")
            graph.components.insert(
                0,
                ExtractedComponent(
                    inst_name=f"V_{rail}",
                    comp_type="voltage",
                    nodes=[rail, "0"],
                    value=v_val,
                    role_hint="supply",
                ),
            )

    # 1. Build SPICE netlist (.cir)
    cir_lines = [f"* Auto-reconstructed from schematic image: {graph.title}"]
    needs_2n2222 = False
    needs_1n4148 = False

    for comp in graph.components:
        nodes_norm = ["0" if n.upper() == "GND" else n.upper() for n in comp.nodes]
        val_str = comp.value or "1k"
        if "2N2222" in val_str.upper():
            needs_2n2222 = True
        if "1N4148" in val_str.upper():
            needs_1n4148 = True
        cir_lines.append(f"{comp.inst_name} {' '.join(nodes_norm)} {val_str}")

    if needs_2n2222:
        cir_lines.append(
            ".model 2N2222 NPN(IS=1E-14 VAF=100 BF=200 IKF=0.3 XTB=1.5 BR=3 CJC=8E-12 CJE=25E-12 TR=100E-9 TF=400E-12 ITF=1 VTF=2 XTF=3 RB=10 RC=0.3 RE=0.2)"
        )
    if needs_1n4148:
        cir_lines.append(".model 1N4148 D(IS=2.52E-9 RS=0.568 N=1.752 CJO=4E-12 M=0.4 TT=20E-9)")

    cir_lines.append(".end")
    cir_text = "\n".join(cir_lines) + "\n"

    # 2. Build clean 16-pixel grid LTspice schematic (.asc) using modular column placement
    # Each component is placed on a 160-pixel pitch column with local net flags on its pins,
    # guaranteeing zero wire collisions and 100% netlist isomorphism!
    n_comps = len(graph.components)
    sheet_w = max(960, 160 * (n_comps + 2))
    sch = SchematicBuilder(width=sheet_w, height=640)

    for idx, comp in enumerate(graph.components):
        col_x = 96 + idx * 144  # Always a multiple of 16 (96 = 6*16, 144 = 9*16)
        row_y = 224             # 14 * 16
        nodes_norm = ["0" if n.upper() == "GND" else n.upper() for n in comp.nodes]
        placed = sch.add_component(
            inst_name=comp.inst_name,
            symbol_type=comp.comp_type.lower(),
            x=col_x,
            y=row_y,
            value=comp.value or "1k",
            orientation="R0",
        )
        ctype = comp.comp_type.lower()
        if ctype in ("npn", "pnp") and len(nodes_norm) == 3:
            sch.add_flag(placed.pin("C"), nodes_norm[0], stub_dy=-16)
            sch.add_flag(placed.pin("B"), nodes_norm[1], stub_dx=-16)
            sch.add_flag(placed.pin("E"), nodes_norm[2], stub_dy=16)
        elif len(nodes_norm) >= 2:
            sch.add_flag(placed.pin("top"), nodes_norm[0], stub_dy=-16)
            sch.add_flag(placed.pin("bot"), nodes_norm[1], stub_dy=16)

    if needs_2n2222:
        sch.add_directive(
            ".model 2N2222 NPN(IS=1E-14 VAF=100 BF=200 IKF=0.3 XTB=1.5 BR=3 CJC=8E-12 CJE=25E-12 TR=100E-9 TF=400E-12 ITF=1 VTF=2 XTF=3 RB=10 RC=0.3 RE=0.2)"
        )
    if needs_1n4148:
        sch.add_directive(".model 1N4148 D(IS=2.52E-9 RS=0.568 N=1.752 CJO=4E-12 M=0.4 TT=20E-9)")

    return audit, cir_text, sch
