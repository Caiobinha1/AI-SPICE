### [AUDITORIA DE IMAGEM] Parâmetros Não Identificados na Imagem do Esquemático

Ao analisar a imagem do circuito, identifiquei a topologia completa, mas os seguintes parâmetros não estão visíveis ou especificados na figura:

| Componente / Nó | Categoria | O que falta | Sugestão Analítica (IA-SPICE) | Justificativa Técnica |
| :--- | :--- | :--- | :---: | :--- |
| **`VIN`** | `INPUT_STIMULUS` | Componente `VIN` (voltage) nos nós ['IN', 'GND'] está sem valor/modelo na imagem. | **`SINE(0 0.1 1k) AC 1`** | Estímulo senoidal de pequeno sinal (100 mVp a 1k) + AC=1 para análise .ac/.tran. |
| **`CIN`** | `COMPONENT_VALUE` | Componente `CIN` (cap) nos nós ['IN', 'B'] está sem valor/modelo na imagem. | **`1.6u`** | Capacitor de acoplamento/bypass com |Xc| <= 100 Ohm em 1k (E24). |
| **`R1`** | `COMPONENT_VALUE` | Componente `R1` (res) nos nós ['VCC', 'B'] está sem valor/modelo na imagem. | **`47k`** | Resistor superior de base para VB=2.05V com corrente de divisor rígido (E24). |
| **`R2`** | `COMPONENT_VALUE` | Componente `R2` (res) nos nós ['B', 'GND'] está sem valor/modelo na imagem. | **`13k`** | Resistor inferior de base para fixar VB=2.05V (E24). |
| **`RC`** | `COMPONENT_VALUE` | Componente `RC` (res) nos nós ['VCC', 'C'] está sem valor/modelo na imagem. | **`2.4k`** | Dimensionado para queda DC de ~40% de VCC (3.6V) com IC=1.5mA (E24). |
| **`RE`** | `COMPONENT_VALUE` | Componente `RE` (res) nos nós ['E', 'GND'] está sem valor/modelo na imagem. | **`910`** | Degeneração de emissor (~15% de VCC = 1.35V) para estabilidade térmica (E24). |
| **`Q1`** | `DEVICE_MODEL` | Componente `Q1` (npn) nos nós ['C', 'B', 'E'] está sem valor/modelo na imagem. | **`2N2222`** | Transistor NPN de uso geral / RF clássico com modelo SPICE padrão. |
| **`COUT`** | `COMPONENT_VALUE` | Componente `COUT` (cap) nos nós ['C', 'OUT'] está sem valor/modelo na imagem. | **`1.6u`** | Capacitor de acoplamento/bypass com |Xc| <= 100 Ohm em 1k (E24). |
| **`VCC`** | `SUPPLY_VOLTAGE` | Barramento de alimentação `VCC` aparece no esquemático sem o valor de tensão DC. | **`9`** | Tensão DC de barramento padrão sugerida (9 V). |

**Como deseja prosseguir?**
1. **Informar os valores manualmente** (responda com os valores desejados para os itens acima); ou
2. **Autorizar dimensionamento autônomo** (a ferramenta calculará e adotará os valores sugeridos acima com base no ponto de operação e séries comerciais E24).
