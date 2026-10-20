# 🚀 Guia de Lançamento e Divulgação — AI-SPICE

Este documento reúne o checklist passo a passo para o **dia do lançamento público** do **AI-SPICE**, além de instruções para indexação em diretórios de agentes de IA (MCP Registries) e templates prontos para divulgação.

---

## 1. Checklist no GitHub (No Momento de Tornar Público)

1. **Mudar Visibilidade para Público:**
   - Acesse `Settings` $\rightarrow$ `General` $\rightarrow$ `Danger Zone` $\rightarrow$ `Change repository visibility` $\rightarrow$ **Public**.
2. **Validar "About" e Topics (Na engrenagem do canto superior direito do repositório):**
   - **Description:** `Equation-Grounded AI Electronic Circuit Engineering Framework — Closed-Loop LTspice Verification, Vision-to-SPICE & Wired .asc Schematic Compiler (MCP Server + CLI)`
   - **Topics (já configurados via API):** `ltspice`, `spice`, `eda`, `circuit-design`, `analog-electronics`, `rf-engineering`, `electronics`, `schematic-generator`, `mcp`, `mcp-server`, `model-context-protocol`, `ai-agent`, `claude-code`, `cursor`, `codex`, `spice-simulator`, `filter-design`, `power-electronics`, `opamp`, `python`.
3. **Criar a Primeira Release Oficial (`v0.1.0`):**
   - Vá em `Releases` $\rightarrow$ `Create a new release` $\rightarrow$ Tag `v0.1.0` $\rightarrow$ Title: `AI-SPICE v0.1.0 — Equation-Grounded AI Circuit Engineering & Wired LTspice Schematic Compiler`.
   - Criar uma Release oficial faz com que mecanismos de busca, indexadores de código e diretórios MCP classifiquem o repositório como estável.

---

## 2. Como Fazer Outras IAs Encontrarem e Usarem o AI-SPICE Automaticamente

O repositório já contém todos os arquivos de descoberta automática para agentes (`llms.txt`, `mcp.json`, `smithery.yaml`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md` e `skills/ai-spice-engineer/SKILL.md`). No dia do lançamento, faça estes **4 cadastros gratuitos** (levam menos de 10 minutos no total):

1. **Publicar no PyPI (`pip install ai-spice`):**
   - Quando um usuário pede a uma IA para instalar uma biblioteca Python para LTspice, as IAs consultam o PyPI:
   ```bash
   pip install build twine
   python -m build
   twine upload dist/*
   ```
2. **Cadastrar no Smithery.ai (Maior diretório de MCP Servers para Cursor / Claude):**
   - Acesse `https://smithery.ai/new` e cole `https://github.com/Caiobinha1/AI-SPICE` (o arquivo `smithery.yaml` na raiz já configura tudo automaticamente).
3. **Cadastrar no Glama.ai MCP Directory & PulseMCP:**
   - Acesse `https://glama.ai/mcp/servers` $\rightarrow$ `Submit Server` e informe a URL do repositório.
4. **Abrir um Pull Request na lista `punkpeye/awesome-mcp-servers`:**
   - Adicione uma linha na categoria *Engineering / Developer Tools*:
     `- [AI-SPICE](https://github.com/Caiobinha1/AI-SPICE) - Equation-grounded electronic circuit sizing, headless LTspice batch verification, Vision-to-SPICE reconstruction, and 16px-grid wired .asc schematic compiler.`
   - Essa lista é lida diretamente pelos modelos de linguagem e buscadores quando alguém pergunta *"Is there an MCP server for LTspice or circuit design?"*.

---

## 3. Templates Prontos para Divulgação (Dia do Lançamento)

### Opção A — Reddit (`r/electronics`, `r/ElectricalEngineering`, `r/rfelectronics`, `r/LocalLLaMA`)
**Title:** `I built AI-SPICE: an open-source framework & MCP server that forces AI agents to size circuits with real equations, verify them in LTspice batch mode, and compile clean wired .asc schematics (NC_ = 0)`

**Body:**
> Every time I asked an LLM (ChatGPT, Claude, Codex) to design a multi-stage circuit or generate an LTspice `.asc` schematic, two things went wrong:
> 1. It guessed component values without checking inter-stage loading, DC operating points, or startup margins.
> 2. When asked for an `.asc` schematic, it either hallucinated off-grid coordinates (`NC_` floating pins everywhere) or dumped isolated components with net labels and zero wires.
>
> So I built **AI-SPICE** (`https://github.com/Caiobinha1/AI-SPICE`), an open-source Python toolkit, CLI, and Model Context Protocol (MCP) server that closes the loop between analytical equations and LTspice 24/XVII:
> - **Multi-Domain Analytical Sizing (E12/E24/E96):** Op-Amps, BJTs, MOSFETs, Active/Passive Filters, Oscillators, RF Impedance Matching, and Power Dividers.
> - **Closed-Loop Headless LTspice Verification:** Runs `LTspice.exe -b`, parses `.meas` logs automatically, and runs physics failure diagnostics.
> - **Topological Signal-Flow `.asc` Compiler:** Compiles any SPICE `.cir` netlist into a 16-pixel grid `.asc` schematic arranged left-to-right by functional blocks, with **100% physical orthogonal `WIRE` routing on all signal nets** and formal `LTspice.exe -netlist` isomorphism verification (`NC_ = 0`).
> - **Vision-to-SPICE:** Reconstructs `.cir` and wired `.asc` files from circuit photos/diagrams while auditing missing parameters.
>
> Repo (MIT License): https://github.com/Caiobinha1/AI-SPICE

### Opção B — Hacker News (`Show HN`)
**Title:** `Show HN: AI-SPICE – Equation-grounded circuit design and wired LTspice .asc compiler for AI agents`
