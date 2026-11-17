---
name: ghosthand
description: >-
  GhostHand: Operador autônomo de desktop (mouse, teclado e tela) local (Mac/Linux) e remoto (Windows via bridge/SSH).
  Utiliza pipeline híbrido de visão local (OpenCV, Fast OCR e Ollama com LFM3-VL/Moondream) para ZERO consumo de tokens de nuvem,
  movimento de mouse com curvas de Bézier cúbicas humanizadas, digitação natural e suporte a jogos DirectX com DirectInput.
  Compatível nativamente com Antigravity (AGY), Claude Code, OpenCode e Model Context Protocol (MCP).
---

# 👻 GhostHand — Zero-Token Human-like Desktop Operator

O **GhostHand** (anteriormente `remote-desktop-operator`) permite que qualquer agente do **Antigravity (AGY)**, **Claude Code**, **OpenCode** ou **servidor MCP** assuma o controle visual e físico de uma máquina — seja localmente no macOS/Linux ou remotamente em um Windows/Linux via conexão de rede (HTTP Bridge ou SSH).

Projetado sob a filosofia **Zero Cloud Tokens**: todas as capturas de tela, OCR, localização de botões e marcação de grids (Set of Marks) são processadas **100% localmente** (via Ollama com modelos leves como `lfm3-vl`, `moondream` ou OpenCV), eliminando o envio de dezenas de megabytes de imagens para APIs de nuvem pagas.

---

## ⚡ Por que Zero Cloud Tokens?

| Abordagem Tradicional (Nuvem) | Remote Desktop Operator (Local Vision) |
|---|---|
| Envia prints de 1080p/4K para GPT-4o / Claude Vision (~1.600 tokens por frame). | Processa o print localmente no Ollama (`lfm3-vl` / `moondream`) ou OpenCV. |
| Latência de 3 a 8 segundos por clique. | Latência de sub-300ms na ponte e 1s a 2s no modelo local. |
| Alto custo financeiro e esgotamento rápido de quota de tokens. | **Custo Zero ($0.00)** e **0 tokens de nuvem consumidos**. |
| Falha em jogos DirectX por usar `mouse_event` legado. | Suporte nativo a **DirectInput (`pydirectinput`)** para jogos como *SP Football Life 2027*. |

---

## 📐 Arquitetura

```
+-------------------------------------------------------+
|                 Antigravity Agent                     |
|            (Claude / Gemini / Agent CLI)             |
+---------------------------+---------------------------+
                            |
                            v
       [ scripts/operator_cli.py ]  <----+
         |                 |             |
         | (Visão Local)   | (Controle)  |
         v                 v             |
+-------------------+  +--------------------------------+
|  vision_local.py  |  | Target: Local Mac/Linux       |
|  - Ollama API     |  | - mss (Screen capture)         |
|  - LFM3-VL /      |  | - PyAutoGUI (Mouse/Teclado)    |
|    Moondream      |  +--------------------------------+
|  - OpenCV Matcher |                    OU
|  - Set of Marks   |  +--------------------------------+
+-------------------+  | Target: Remote Windows PC      |
                       | (ex: 192.168.31.231)           |
                       | - bridge_agent.py (:8788)      |
                       | - pydirectinput (DirectX /     |
                       |   Football Life Games)         |
                       +--------------------------------+
```

---

## 📁 Estrutura de Arquivos

```
~/.gemini/config/skills/remote-desktop-operator/
├── SKILL.md                 # Esta documentação
├── requirements.txt         # Dependências Python
└── scripts/
    ├── operator_cli.py      # CLI principal para o agente
    ├── vision_local.py      # Módulo de visão local (Ollama / OpenCV / Grid)
    └── bridge_agent.py      # Daemon leve para a máquina alvo (Windows / Linux)
```

---

## 🚀 Como Usar

### 1. Pré-requisitos na Máquina Controladora (Mac)
- **Ollama**: rodando localmente com um modelo de visão (`lfm3-vl`, `moondream` ou `qwen2-vl`):
  ```bash
  ollama list
  # Caso não tenha nenhum: ollama pull lfm3-vl ou ollama pull moondream
  ```
- **Dependências Python**:
  ```bash
  pip install -r ~/.gemini/config/skills/remote-desktop-operator/requirements.txt
  ```

---

### 2. Controle Local (Mac)

O modo padrão é `--target local` (você pode chamar diretamente `ghosthand` ou o script python):

```bash
# 1. Verificar status da tela e do modelo de visão local
ghosthand status

# 2. Tirar um print da tela
ghosthand screenshot --out /tmp/current.jpg

# 3. Encontrar um elemento visualmente com ZERO tokens de nuvem (Cascade OCR/Color/VLM)
ghosthand find "Botão Fechar"

# 4. Encontrar e clicar diretamente em 1 passo!
ghosthand click --find "Iniciar Jogo"

# 5. Clicar em coordenadas exatas (comportamento instantâneo ou humano)
ghosthand click --x 500 --y 300
ghosthand click --x 500 --y 300 --human

# 6. Movimento de mouse humano (Curva de Bézier cúbica, aceleração e micro-jitter orgânico)
ghosthand move --x 800 --y 450 --human --duration 1.2

# 7. Digitação humanizada (variação gaussiana por caractere, pausas em pontuações e espaços)
ghosthand type "Texto digitado como humano" --human --wpm 60

# 8. Pressionar teclas especiais ou atalhos
ghosthand press enter
ghosthand press "alt+f4"
ghosthand press "ctrl+s"

# 9. Segurar uma tecla por X segundos (ideal para jogos/movimentação)
ghosthand hold w --duration 1.5

# 10. Iniciar como Servidor MCP (Model Context Protocol stdio)
ghosthand mcp
```

---

### 3. Controle Remoto de PC Windows (ex: SP Football Life / Máquina de Jogos)

Para controlar uma máquina Windows (como `192.168.31.231`):

#### No Windows:
1. Copie o script `bridge_agent.py` para a máquina Windows (ou acesse pelo compartilhamento SMB).
2. Instale as dependências:
   ```cmd
   pip install pydirectinput mss pillow pyautogui
   ```
3. Inicie o daemon:
   ```cmd
   python bridge_agent.py --server --port 8788
   ```

#### No Mac / Antigravity Agent:
Basta passar `--target http://<IP_DO_WINDOWS>:8788`:

```bash
# Status do Windows remoto
ghosthand --target http://192.168.31.231:8788 status

# Tirar print remoto da tela do jogo
ghosthand --target http://192.168.31.231:8788 screenshot --out /tmp/game.jpg

# Clicar no botão do menu do jogo via DirectInput (hardware scancodes)
ghosthand --target http://192.168.31.231:8788 click --find "EXHIBITION MATCH"
```

> [!TIP]
> Você pode definir uma variável de ambiente para não precisar digitar `--target` toda vez:
> ```bash
> export REMOTE_DESKTOP_TARGET="http://192.168.31.231:8788"
> ```

---

## ⚡ Pipeline Híbrido em Cascata (Zero Tokens & Alta Fluidez)

O motor `find` e `click --find` agora opera com um pipeline hierárquico em 4 camadas que combina visão computacional clássica com IA:

1. **Camada 1: Detecção de Badges / Notificações Coloridas (6ms a 20ms)**
   - Algoritmo OpenCV em espaço de cor RGB/HSV para identificar contadores verdes (WhatsApp), alertas vermelhos e tags.
2. **Camada 2: Template Matching de Ícones (5ms)**
   - Comparação exata de matriz de pixels para ícones pré-conhecidos (lupa, fechar, play).
3. **Camada 3: Fast OCR Text & Phrase Fuzzy Matcher (300ms a 600ms)**
   - Motor local baseado no Tesseract para varrer todos os textos e agrupar frases vizinhas com tolerância a acentos (`não lidas` = `nao lidas`).
4. **Camada 4: Fallback Local VLM (Ollama lfm3-vl / moondream) (~1.5s)**
   - Acionado automaticamente quando o alvo for conceitual ou puramente visual (ex: "foto de perfil", "área de mensagens").

```bash
# Busca automática (escolhe o método mais rápido)
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py find "Não lidas"

# Forçar OCR puro (< 500ms)
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py find "Pesquisar" --strategy ocr

# Forçar detecção de selo/badge em tempo real (< 20ms)
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py find "selo verde" --strategy color
```

---

## 🎯 Métodos Visuais Determinísticos (Sem Alucinação)

### 1. Set-of-Marks (Numbered Badges)
Analisa a tela usando contornos e detectores de contraste, numerando cada botão ou caixa de texto com um badge vermelho:

```bash
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py candidates --out /tmp/candidates.png
```
Retorno JSON:
```json
{
  "ok": true,
  "annotated_image": "/tmp/candidates.png",
  "candidates": {
    "1": {"id": 1, "x": 320, "y": 190, "bbox": [147, 197, 233, 443]},
    "2": {"id": 2, "x": 540, "y": 300, "bbox": [280, 500, 320, 580]}
  }
}
```
O agente pode simplesmente clicar nas coordenadas do elemento desejado com 100% de precisão.

### 2. Coordinate Grid Overlay (A1 a J10)
Cria uma grade sutil numerada sobre a tela para navegação rápida:
```bash
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py grid --out /tmp/grid.png
```

### 3. Template Matching Exato (OpenCV)
Se você tiver um ícone ou pedaço de botão salvo (`assets/button_play.png`):
```bash
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/vision_local.py --image /tmp/screen.png --template assets/button_play.png
```

---

## 🎮 Suporte a Jogos DirectX e DirectInput (Windows)

Jogos modernos (como *SP Football Life 2027*, *Pro Evolution Soccer*, *FIFA*, jogos em Unreal Engine/Unity) usam a API **DirectX DirectInput / RawInput**, que costuma descartar chamadas Win32 convencionais de mouse/teclado.

O `bridge_agent.py` inclui:
1. **`pydirectinput`**: Envia eventos de DirectInput via driver de teclado/mouse.
2. **Scancodes de Hardware DirectX**: Mapeamento direto de DIK (DirectInput Key codes como `0x1C` para Enter, `0x11` para W, etc.).
3. **`hold` command**: Permite segurar teclas de movimento ou aceleração por durações precisas:
   ```bash
   python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py hold enter --duration 0.8
   ```

---

## 📜 Execução de Macros Complexas (JSON)

Você pode encadear ações complexas sem latência de roundtrip:

```json
[
  {"action": "click", "find": "Settings"},
  {"action": "wait", "ms": 500},
  {"action": "press", "key": "down"},
  {"action": "press", "key": "enter"},
  {"action": "type", "text": "1920x1080"},
  {"action": "press", "key": "enter"},
  {"action": "wait", "ms": 1000},
  {"action": "screenshot", "out": "/tmp/settings_applied.jpg"}
]
```

Para executar:
```bash
python3 ~/.gemini/config/skills/remote-desktop-operator/scripts/operator_cli.py macro macro.json
```

---

## 🛡️ Segurança e Autenticação

Para proteger a ponte remota na sua rede local, utilize um token secreto:

1. No servidor:
   ```cmd
   python bridge_agent.py --server --port 8788 --token "meu_segredo_forte"
   ```
2. No cliente:
   ```bash
   python3 operator_cli.py --target http://192.168.31.231:8788 --token "meu_segredo_forte" status
   ```
   Ou via variável de ambiente:
   ```bash
   export REMOTE_DESKTOP_TOKEN="meu_segredo_forte"
   ```
