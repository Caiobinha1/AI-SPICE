<div align="center">

# ⚡ IA-SPICE (Português - BR)

**Framework de Engenharia de Circuitos Analógicos e de RF Guiado por Equações, com Verificação em Malha Fechada no LTspice, Reconstrução por Imagem (Vision-to-SPICE) e Compilação de Esquemáticos `.asc` Sem Sobreposições.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](pyproject.toml)
[![Simulator: LTspice 24](https://img.shields.io/badge/Simulator-LTspice%2024%20%2F%20XVII-dc2626.svg)](#)
[![MCP Server: 7 Tools](https://img.shields.io/badge/MCP-JSON--RPC%202.0-16a34a.svg)](src/ia_spice/mcp_server.py)
[![Tests: 34 Passed](https://img.shields.io/badge/Tests-34%2F34%20Passing-10b981.svg)](tests/)
[![Read in English](https://img.shields.io/badge/Language-English-2563eb.svg)](README.md)

</div>

---

## 💡 Por Que o IA-SPICE Existe?

Quando engenheiros ou estudantes pedem a uma IA comum (*ChatGPT, Claude, Gemini*) para *"Projetar um transmissor FM ou um amplificador analógico no LTspice"*, o circuito quase sempre falha na simulação:
1. **Ausência de Dimensionamento Analítico**: A IA chuta valores de resistores e capacitores diretamente na netlist sem resolver o ponto de operação DC ($V_{CE}, I_C, g_m$), a frequência de ressonância ou as séries comerciais **E12/E24/E96**.
2. **Colapso por Carregamento entre Estágios**: Conectar o Estágio $N+1$ (ex.: amplificador RF ou antena de $50\,\Omega / 100\,\Omega$) diretamente ao Estágio $N$ (ex.: VCO Colpitts) derruba o ganho de malha abaixo do critério de Barkhausen ($g_m R_p < 1$) e mata a oscilação.
3. **Esquemáticos `.asc` Quebrados e Sobrepostos**: Gerar arquivos `.asc` no chute produz pinos desconectados (`NC_01`), fios diagonais, labels verticais colidindo com componentes e textos `.tran`/`.meas` em cima do circuito.

O **IA-SPICE resolve esses três problemas de forma definitiva** integrando equações analíticas de primeira ordem, contratos de impedância (`StageContract`), simulação em lote no `LTspice.exe`, diagnóstico físico de falhas, **reconstrução de circuitos por foto (`vision_to_spice`)** e um **editor/compilador universal de projetos LTspice (`ltspice_project`)**.

![Benchmark Principal IA-SPICE: Cálculos Analíticos vs LTspice 24](assets/flagship_vco_theory_vs_ltspice.png)

---

## 📊 Resultados do Projeto Principal: Transmissor FM 5 MHz (VCO Colpitts de 6 Estágios)

Todos os esquemáticos `.asc` e netlists `.cir` estão prontos para abrir em [`showcase/01_flagship_5mhz_fm_transmitter/`](showcase/01_flagship_5mhz_fm_transmitter/):

| Estágio / Testbench | Parâmetro | Alvo Analítico | Simulado LTspice 24 (`.meas`) | Erro Relativo | Pinos `NC_` | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Bloco 1 (`TESTE_bloco1_audio.asc`)** | Tensão DC de Emissor $V_{CTRL}$ | $4,00\text{ V}$ | **$4,108\text{ V}$** | $+2,70\%$ | `0` | ✅ APROVADO |
| **Bloco 1 (`TESTE_bloco1_audio.asc`)** | Ganho do Seguidor $A_{v4}$ | $0,9935\text{ V/V}$ | **$0,9934\text{ V/V}$** | $-0,01\%$ | `0` | ✅ APROVADO |
| **Bloco 3 (`S1_varactor_validacao.asc`)** | Varactor $C_{var}(4\text{V})$ | $20,69\text{ pF}$ | **$20,69\text{ pF}$** | $0,00\%$ | `0` | ✅ APROVADO |
| **Bloco 4 (`S2_S3_vco_nucleo.asc`)** | Frequência do VCO $f_{osc}$ | $5,000\text{ MHz}$ | **$4,9957\text{ MHz}$** | $-0,085\%$ | `0` | ✅ APROVADO |
| **Bloco 4 (`S4_curva_fosc_vctrl.asc`)** | Sensibilidade $K_{VCO}$ | $\Delta f = 51,6\text{ kHz}$ | **$12,90\text{ kHz/V}$** | Otimizado ($C_{CV}=47\text{pF}$) | `0` | ✅ APROVADO |
| **Blocos 5+6 (`TESTE_blocos5_6_amplificador.asc`)** | Potência em $50\,\Omega$ | $10,00\text{ mW}$ | **$10,00\text{ mW}$** | $0,00\%$ | `0` | ✅ APROVADO |
| **Sistema Completo (`S6_modulacao_fm_completa.asc`)** | Portadora Integrada $f_0$ | $5,000\text{ MHz}$ | **$4,9798\text{ MHz}$** | $-0,40\%$ | `0` | ✅ APROVADO |
| **Sistema Completo (`S6_modulacao_fm_completa.asc`)** | Potência na Antena ($50\,\Omega$) | $10,00\text{ mW}$ | **$9,823\text{ mW}$** | $-1,77\%$ | `0` | ✅ APROVADO |
| **Sistema Completo (`S6_modulacao_fm_completa.asc`)** | Potência DC Total $P_{total}$ | $< 500\text{ mW}$ | **$387,97\text{ mW}$** | $-22,4\%$ folga | `0` | ✅ APROVADO |

![Curva de Sintonia do Varactor SMV1237 e VCO Colpitts](assets/vco_kvco_tuning_curve.png)

---

## 🛠️ Principais Funcionalidades

1. **Reconstrução de Esquemático por Foto (`vision_to_spice.py`)**:
   - Recebe a topologia extraída de uma foto/imagem de esquemático, identifica quais valores de componentes, modelos ou fontes não estão informados na figura e:
     - No modo **`ask_user`**: apresenta uma tabela detalhada ao usuário com sugestões analíticas E24 e pergunta se o usuário deseja informar os valores ou deixar a ferramenta dimensionar sozinha;
     - No modo **`autonomous`**: calcula e aplica todos os valores na série E24, gera a netlist `.cir` e o esquemático `.asc` limpo (`NC_ = 0`) e simula no LTspice.
2. **Suporte Completo a Todos os Formatos LTspice (`ltspice_project.py`)**:
   - Lê, diagnostica, modifica valores (`modify_asc_component`), adiciona novos componentes (`add_component_to_asc`) e embeleza esquemáticos existentes cobrindo **`.asc`, `.cir`, `.net`, `.sub`, `.lib`, `.asy`, `.plt` e `.log`**.
3. **3 Estilos Automáticos de Layout `.asc` + Sanitizador Anti-Colisão (`asc_compiler.py`)**:
   - Converte qualquer netlist `.cir` em esquemático `.asc` sem precisar informar coordenadas, oferecendo os estilos `A_textbook_wired`, `B_power_rail_bus` (barramento superior contínuo `VCC`) e `C_modular_blocks`.
   - Garante matematicamente que todas as labels fiquem na horizontal (`0°`), que terras tenham espaçamento de `32 px` e que diretivas `.tran`/`.meas` fiquem no rodapé a `>= 160 px` abaixo do circuito.

---

## 🚀 Como Executar

```bash
pip install -e .[dev]
python -m unittest discover -s tests -v
```
Veja também o guia de divulgação e lançamento da ferramenta em [`docs/GUIA_DIVULGACAO_E_LANCAMENTO.md`](docs/GUIA_DIVULGACAO_E_LANCAMENTO.md).
