# 🚀 Guia de Organização de Repositório e Estratégia de Divulgação — **IA-SPICE**

Este documento consolida o estudo de como ferramentas open-source de sucesso na interseção de **IA + Engenharia / CAD / Eletrônica** (como `blender-mcp` com +12k estrelas, `kicad-mcp`, `atopile`, `PyLTSpice`/`spicelib` e `SKiDL`) organizam seus repositórios no GitHub e estruturam seus lançamentos para alcançar máxima visibilidade e adoção pela comunidade.

---

## 1. Anatomia de um Repositório GitHub de Alto Impacto (O que já configuramos no `IA-SPICE`)

Ao analisar repositórios que viralizaram recentemente no GitHub, identificamos **7 elementos obrigatórios** que separam um "projeto acadêmico/pessoal" de uma **ferramenta open-source profissional**:

| Elemento | Como os projetos de sucesso fazem (`blender-mcp`, `atopile`, `spicelib`) | Como foi implementado no `IA-SPICE` |
| :--- | :--- | :--- |
| **1. Prova Visual Imediata no Topo do `README.md`** | Visitantes decidem em **10 segundos** se vão dar ⭐ (*Star*) com base em gráficos reais e diagramas de arquitetura logo no início do `README`. | Geramos 3 gráficos científicos em **300 DPI** em [`assets/`](../assets/) comparando teoria vs. LTspice 24 nos 6 blocos do Transmissor FM e provando o efeito de carregamento entre estágios, além de um fluxograma Mermaid da malha fechada. |
| **2. Pasta `showcase/` com Arquivos Prontos para Abrir** | Engenheiros querem abrir um arquivo real no software deles (LTspice/KiCad) imediatamente, sem precisar rodar scripts antes. | Criamos a pasta [`showcase/`](../showcase/) contendo os arquivos `.asc`, `.cir`, `.plt` e relatórios `.md` já compilados e verificados (`NC_ = 0`) para **4 projetos completos** (incluindo o Transmissor FM 5 MHz e os 3 estilos visuais de layout `.asc`). |
| **3. Instalação Zero-Fricção para Agentes (`configs/`)** | Ferramentas MCP que crescem rápido fornecem um bloco JSON pronto para copiar e colar no Claude Desktop, Cursor, Windsurf ou Antigravity. | Criamos [`configs/mcp_config.example.json`](../configs/mcp_config.example.json) e expusemos **7 ferramentas MCP** em Python puro (zero dependências externas de SDK que quebram na instalação). |
| **4. CI/CD Automatizado (`.github/workflows/ci.yml`)** | O selo verde de testes passando no GitHub Actions gera confiança imediata em desenvolvedores. | Criamos [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) testando automaticamente em Windows e Ubuntu nas versões Python 3.10, 3.11 e 3.12. |
| **5. Documentação Bilíngue (`README.md` + `README.pt-BR.md`)** | Repositórios globais usam o `README.md` principal em inglês (para indexação no Hacker News, Reddit internacional e listas `awesome-*`) com um botão no topo para a versão nativa em Português. | Criamos [`README.md`](../README.md) (Inglês) e [`README.pt-BR.md`](../README.pt-BR.md) (Português) conectados por badge no cabeçalho. |
| **6. Botão Oficial de Citação Acadêmica (`CITATION.cff`)** | Como o projeto resolve um problema pesquisado na academia (*IEEE TCAD / AAAI AnalogCoder*), ter um `CITATION.cff` ativa o botão *"Cite this repository"* no GitHub para artigos e TCCs. | Criamos [`CITATION.cff`](../CITATION.cff) na raiz do repositório. |
| **7. Templates de Contribuição de Circuitos** | Convidar usuários a submeterem novos circuitos desafia a comunidade a testar a ferramenta. | Criamos [`.github/ISSUE_TEMPLATE/circuit_benchmark.md`](../.github/ISSUE_TEMPLATE/circuit_benchmark.md). |

---

## 2. Checklist de Configuração na Interface do GitHub (Ao Subir o Repositório)

Assim que fizermos o `git push` para o seu GitHub:
1. **Descrição Curta (*About* na barra direita do GitHub):**
   > `⚡ Equation-Grounded AI Analog & RF Circuit Engineering Framework with Closed-Loop LTspice Verification, Vision-to-SPICE Reconstruction, and Zero-Overlap .asc Schematic Compiler (CLI + MCP Server).`
2. **Topics (Tags de busca no GitHub — adicione todas estas na engrenagem do *About*):**
   `ltspice` · `spice` · `analog-electronics` · `rf-engineering` · `mcp` · `mcp-server` · `eda` · `circuit-design` · `circuit-simulation` · `ai-agents` · `python` · `colpitts-oscillator`
3. **Social Preview Image:**
   Em *Settings $\rightarrow$ General $\rightarrow$ Social preview*, faça upload da imagem [`assets/flagship_vco_theory_vs_ltspice.png`](../assets/flagship_vco_theory_vs_ltspice.png). Assim, toda vez que o link do GitHub for compartilhado no LinkedIn, WhatsApp, Discord, Reddit ou X/Twitter, aparecerá o gráfico comparativo de alta resolução!

---

## 3. Roteiro de Divulgação Multicanal (Textos Prontos para Copiar e Publicar)

A estratégia que fez ferramentas como `blender-mcp` e `kicad-mcp` explodirem de estrelas combina **4 canais**:

### Canal 1: Diretórios Oficiais de MCP (Tráfego Passivo Contínuo)
Abra um Pull Request (leva 2 minutos) adicionando uma linha com o link do seu repositório nas listas mais acessadas do mundo por usuários de Claude/Cursor/Antigravity:
1. **`punkpeye/awesome-mcp-servers`** (GitHub — +40.000 estrelas)
2. **`wong2/awesome-mcp-servers`**
3. **`mcp.so`**, **`glama.ai/mcp/servers`** e **`pulsemcp.com`** (basta colar a URL do repositório GitHub no formulário de submissão deles).
- **Linha pronta para o PR nas listas `awesome-mcp-servers`:**
  ```markdown
  - [IA-SPICE](https://github.com/Caiobinha1/IA-SPICE) - Equation-grounded analog & RF circuit design, closed-loop LTspice batch simulation, Vision-to-SPICE schematic photo reconstruction, and 16px-grid `.asc` schematic compiler (`NC_ = 0`).
  ```

---

### Canal 2: Reddit (`r/ECE`, `r/rfelectronics`, `r/ClaudeAI`, `r/mcp`, `r/Electronics`)
No Reddit, posts que mostram **resultados reais de engenharia** (como o nosso VCO Colpitts de 5 MHz batendo $4,9957\text{ MHz}$ no LTspice e o gráfico de colapso de impedância) têm altíssimo engajamento.

**Título sugerido (`r/ECE` / `r/rfelectronics` / `r/ClaudeAI`):**
> `I built an open-source framework (IA-SPICE) that forces AI agents to do first-principles hand calculations, stage loading checks, and closed-loop LTspice verification before generating clean .asc schematics`

**Corpo do Post:**
> Whenever I saw people ask LLMs to design analog or RF circuits in SPICE, the results failed in simulation: the AI guessed resistor values without solving the DC operating point, connected 50-ohm loads directly to oscillator tanks (killing Barkhausen startup), and produced unreadable `.asc` schematics with floating pins (`NC_`) and overlapping text.
>
> Over the past weeks, we designed a complete **6-stage 5 MHz Colpitts VCO FM Transmitter** (audio pre-amp, emitter-follower buffer, hyperabrupt SMV1237 varactor modulator, 5 MHz Colpitts core, Class-A RF amplifier, and 50-ohm output buffer) 100% with an AI agent — achieving **0.085% frequency error (4.9957 MHz vs 5.000 MHz target)** and **9.82 mW into 50 ohms** in LTspice 24.
>
> We extracted the entire engineering methodology into an open-source Python toolkit, CLI, and MCP Server called **IA-SPICE**:
> - **Analytical First-Principles Sizing (`sizing.py`)**: Solves BJT DC/AC operating points, Colpitts + Varactor resonance, Barkhausen startup margin ($g_m R_p$), and snaps values to real commercial **E12/E24/E96** series.
> - **Inter-Stage Loading Contracts (`StageContract`)**: Automatically detects impedance loading collapse between cascaded stages and enforces buffer insertion.
> - **Vision-to-SPICE (`vision_to_spice.py`)**: Reconstructs circuits from schematic photos/scans, detects missing/unreadable values, and either asks the user or sizes them autonomously in E24.
> - **Universal LTspice Project Editor & 16px `.asc` Compiler (`ltspice_project.py` & `asc_compiler.py`)**: Reads, modifies, diagnoses, and compiles `.asc`, `.cir`, `.net`, `.sub`, `.asy`, `.plt`, and `.log` files with 3 automatic layout styles, formal `LTspice.exe -netlist` isomorphism verification (`NC_ = 0`), and automatic anti-overlap sanitization.
>
> GitHub Repo (MIT License, 34 automated tests, ready-to-open `.asc` showcase): [LINK_DO_GITHUB]
>
> Would love feedback from analog/RF engineers and students on what topologies or benchmarks we should add next!

---

### Canal 3: Hacker News (`Show HN`)
- **Título:**
  > `Show HN: IA-SPICE – Equation-grounded AI analog/RF circuit design with LTspice verification`
- **Dica de Ouro para o HN:** Publique entre terça e quinta-feira, por volta das 10h–12h (horário de Brasília), e coloque no primeiro comentário a explicação técnica dos 3 bugs físicos reais que o diagnóstico automático da ferramenta resolveu no VCO Colpitts (como a auto-retificação do varactor quando $C_{CV}$ era alto demais e como o `LTspice.exe -netlist` garante isomorfismo de grafos com zero pinos desconectados).

---

### Canal 4: LinkedIn / Comunidade Acadêmica e Universitária (Em Português)
Para divulgar no LinkedIn (marcando professores, colegas de Engenharia Elétrica/Eletrônica e grupos de Microeletrônica/RF):

**Texto pronto para LinkedIn:**
> 🚀 **Transformamos nosso projeto de Circuitos Analógicos/RF em uma ferramenta Open-Source de Engenharia assistida por IA: conheça o IA-SPICE!**
>
> Nos últimos tempos, surgiram várias integrações de IA com ferramentas de CAD, mas quem já tentou pedir para uma IA projetar um circuito analógico ou de RF no LTspice sabe o que acontece: valores chutados sem cálculo de ponto de operação, osciladores que não partem por efeito de carregamento entre estágios e esquemáticos `.asc` com fios e textos sobrepostos.
>
> Depois de projetarmos, dimensionarmos e validarmos com sucesso um **Transmissor FM completo em 5 MHz com VCO Colpitts de 6 estágios** (alcançando **4,9957 MHz** para uma meta de 5,000 MHz — erro de apenas 0,085% — e 9,82 mW na antena de 50 Ω), decidimos transformar toda essa metodologia de engenharia em um framework aberto no GitHub: o **IA-SPICE**.
>
> 🔧 **O que o IA-SPICE faz:**
> ✅ **Cálculo Analítico Antes da Simulação:** Resolve polarização DC/AC, tanque LC + Varactor, critério de partida de Barkhausen e arredonda componentes para as séries comerciais reais **E12, E24 e E96**.
> ✅ **Verificação de Carregamento entre Estágios (`StageContract`):** Detecta quedas de ganho por casamento de impedância e valida o isolamento com buffers.
> ✅ **Reconstrução de Esquemáticos por Foto (`Vision-to-SPICE`):** A partir da imagem de um esquemático, identifica parâmetros faltantes e pergunta ao usuário ou dimensiona tudo automaticamente na série E24.
> ✅ **Compilador e Editor Universal LTspice (`.asc`, `.cir`, `.sub`, `.asy`, `.plt`):** Lê e modifica projetos existentes do LTspice e gera esquemáticos `.asc` alinhados na grade de 16 pixels, sem sobreposição de textos e com verificação formal de zero pinos desconectados (`NC_ = 0`).
> ✅ **Integração via CLI e Servidor MCP:** Conecta-se diretamente ao Claude Code, Cursor e Antigravity.
>
> 🔗 **Repositório no GitHub (Licença MIT, com gráficos, testes automatizados e esquemáticos prontos para abrir):** [LINK_DO_SEU_GITHUB]
>
> #EngenhariaEletrica #EngenhariaEletronica #LTspice #SPICE #RF #CircuitosAnalogicos #OpenSource #IA #EDA
