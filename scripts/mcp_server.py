#!/usr/bin/env python3
"""
mcp_server.py - GhostHand Model Context Protocol (MCP) Server
Enables any MCP-compatible client (Claude Desktop, Antigravity IDE, Cursor, Windsurf, Zed, Claude Code)
to control local and remote desktop environments via stdio JSON-RPC.
"""

import os
import sys
import json
import logging
from typing import Dict, Any, Optional

# Add parent dir to path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from operator_cli import DesktopOperator

# Configure logging to stderr so stdout remains clean for JSON-RPC
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ghosthand-mcp")

# Default global operator
DEFAULT_TARGET = os.environ.get("GHOSTHAND_TARGET", "local")
operator = DesktopOperator(target=DEFAULT_TARGET)

# MCP Tool Definitions Schema
TOOLS = [
    {
        "name": "ghosthand_status",
        "description": "Get current status of GhostHand operator, target machine (screen resolution, cursor position) and local vision engine.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Optional target URL (e.g. 'local' or 'http://192.168.31.231:8788')"}
            }
        }
    },
    {
        "name": "ghosthand_screenshot",
        "description": "Capture a screenshot of the local or remote screen and save it to disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out": {"type": "string", "description": "Output file path (default /tmp/screenshot_<timestamp>.jpg)"},
                "scale": {"type": "number", "description": "Downscaling factor (0.1 to 1.0, default 1.0)"},
                "quality": {"type": "integer", "description": "JPEG quality (1-100, default 80)"}
            }
        }
    },
    {
        "name": "ghosthand_find",
        "description": "Locate an element on screen using GhostHand's ultra-fast hybrid cascade (ColorBadge <15ms -> FastOCR <500ms -> Template <5ms -> Local VLM ~1.5s). Returns exact pixel coordinates with ZERO cloud tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "Natural language description or text of the UI element (e.g. 'Não lidas', 'Pesquisar', 'selo verde', 'Submit')"},
                "image": {"type": "string", "description": "Optional path to existing screenshot file"},
                "strategy": {
                    "type": "string",
                    "enum": ["auto", "ocr", "color", "vlm", "template"],
                    "description": "Grounding strategy: 'auto' (fast cascade), 'ocr', 'color', 'vlm', or 'template'"
                }
            },
            "required": ["description"]
        }
    },
    {
        "name": "ghosthand_click",
        "description": "Click on specific coordinates (x, y) or visually find an element by description and click it. Supports human-like Bézier motion, double click and right click.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X pixel coordinate to click"},
                "y": {"type": "integer", "description": "Y pixel coordinate to click"},
                "find": {"type": "string", "description": "Visual element description to automatically find and click"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1, "description": "Number of clicks (1 for single, 2 for double)"},
                "human": {"type": "boolean", "default": False, "description": "Whether to use natural human-like Bézier curve and realistic click timing"},
                "strategy": {"type": "string", "enum": ["auto", "ocr", "color", "vlm", "template"], "default": "auto"}
            }
        }
    },
    {
        "name": "ghosthand_move",
        "description": "Move the mouse cursor to (x, y) coordinates with optional human-like Bézier curve, speed easing, and micro-jitter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Target X coordinate"},
                "y": {"type": "integer", "description": "Target Y coordinate"},
                "human": {"type": "boolean", "default": False, "description": "Use smooth human-like Bézier curve"},
                "duration": {"type": "number", "description": "Duration in seconds for the movement"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "ghosthand_type",
        "description": "Type text on the active window. Supports realistic human-like typing with Gaussian delays, natural variance, and pauses on punctuation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"},
                "human": {"type": "boolean", "default": False, "description": "Simulate natural human typing rhythm"},
                "wpm": {"type": "number", "default": 65.0, "description": "Words Per Minute speed (default 65)"},
                "interval": {"type": "number", "default": 0.02, "description": "Interval between keystrokes if not human"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "ghosthand_press",
        "description": "Press a key or key combination (e.g. 'enter', 'esc', 'tab', 'command+space', 'ctrl+c', 'alt+f4').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key or hotkey string to press"}
            },
            "required": ["key"]
        }
    },
    {
        "name": "ghosthand_hold",
        "description": "Hold a key down for a duration in seconds (critical for movement and game controls like DirectInput in SP Football Life).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key to hold (e.g. 'w', 'space', 'shift')"},
                "duration": {"type": "number", "default": 0.5, "description": "Seconds to hold key"}
            },
            "required": ["key"]
        }
    },
    {
        "name": "ghosthand_drag",
        "description": "Drag the mouse from (from_x, from_y) to (to_x, to_y).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_x": {"type": "integer", "description": "Starting X"},
                "from_y": {"type": "integer", "description": "Starting Y"},
                "to_x": {"type": "integer", "description": "Destination X"},
                "to_y": {"type": "integer", "description": "Destination Y"},
                "duration": {"type": "number", "default": 0.5}
            },
            "required": ["from_x", "from_y", "to_x", "to_y"]
        }
    },
    {
        "name": "ghosthand_scroll",
        "description": "Scroll the mouse wheel up (positive clicks) or down (negative clicks).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clicks": {"type": "integer", "description": "Scroll clicks (positive for up, negative for down)"},
                "x": {"type": "integer", "description": "Optional X coordinate"},
                "y": {"type": "integer", "description": "Optional Y coordinate"}
            },
            "required": ["clicks"]
        }
    },
    {
        "name": "ghosthand_macro",
        "description": "Execute a sequence of actions in batch (macro) with minimal overhead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of action steps [{\"action\": \"click\", \"x\": 100, \"y\": 200}, {\"action\": \"type\", \"text\": \"hello\"}]"
                }
            },
            "required": ["steps"]
        }
    }
]


def handle_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    global operator
    target = args.get("target")
    if target and target != operator.target:
        operator = DesktopOperator(target=target)

    if name == "ghosthand_status":
        return operator.get_status()
    elif name == "ghosthand_screenshot":
        out_path = args.get("out")
        scale = args.get("scale", 1.0)
        quality = args.get("quality", 80)
        img_bytes, path = operator.capture_screen(out_path=out_path, scale=scale, quality=quality)
        return {"ok": True, "file": path, "bytes": len(img_bytes)}
    elif name == "ghosthand_find":
        desc = args["description"]
        img_path = args.get("image")
        strat = args.get("strategy", "auto")
        return operator.locate_element(desc, image_path=img_path, strategy=strat)
    elif name == "ghosthand_click":
        x = args.get("x")
        y = args.get("y")
        find = args.get("find")
        button = args.get("button", "left")
        clicks = args.get("clicks", 1)
        human = args.get("human", False)
        strat = args.get("strategy", "auto")
        return operator.click(x=x, y=y, find=find, button=button, clicks=clicks, human=human, strategy=strat)
    elif name == "ghosthand_move":
        x = args["x"]
        y = args["y"]
        human = args.get("human", False)
        dur = args.get("duration")
        if operator.is_remote:
            return operator.remote_client.move(x, y)
        else:
            from bridge_agent import InputController
            if human:
                InputController.human_move(x, y, duration=dur)
            else:
                InputController.move(x, y)
            return {"ok": True, "action": "move", "x": x, "y": y, "human": human}
    elif name == "ghosthand_type":
        text = args["text"]
        human = args.get("human", False)
        wpm = args.get("wpm", 65.0)
        interval = args.get("interval", 0.02)
        return operator.type_text(text, interval=interval, human=human, wpm=wpm)
    elif name == "ghosthand_press":
        key = args["key"]
        return operator.press_key(key)
    elif name == "ghosthand_hold":
        key = args["key"]
        dur = args.get("duration", 0.5)
        return operator.hold_key(key, duration=dur)
    elif name == "ghosthand_drag":
        x1 = args["from_x"]
        y1 = args["from_y"]
        x2 = args["to_x"]
        y2 = args["to_y"]
        dur = args.get("duration", 0.5)
        return operator.drag(x1, y1, x2, y2, duration=dur)
    elif name == "ghosthand_scroll":
        clicks = args["clicks"]
        x = args.get("x")
        y = args.get("y")
        return operator.scroll(clicks, x=x, y=y)
    elif name == "ghosthand_macro":
        steps = args["steps"]
        return operator.execute_macro(steps)
    else:
        raise ValueError(f"Unknown tool name: {name}")


def run_stdio_server():
    """Main stdio JSON-RPC loop for Model Context Protocol (MCP)."""
    logger.info("GhostHand MCP Server started (stdio JSON-RPC)")

    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            request = json.loads(line)
            req_id = request.get("id")
            method = request.get("method")
            params = request.get("params", {})

            # 1. Initialize
            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {
                                "listChanged": False
                            }
                        },
                        "serverInfo": {
                            "name": "ghosthand",
                            "version": "1.0.0"
                        }
                    }
                }
            # 2. Initialized notification
            elif method == "notifications/initialized":
                continue
            # 3. Ping
            elif method == "ping":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {}
                }
            # 4. List Tools
            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": TOOLS
                    }
                }
            # 5. Call Tool
            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                try:
                    result_data = handle_tool_call(tool_name, tool_args)
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result_data, indent=2, ensure_ascii=False)
                                }
                            ],
                            "isError": not result_data.get("ok", True) if isinstance(result_data, dict) and "ok" in result_data else False
                        }
                    }
                except Exception as ex:
                    logger.error(f"Error executing tool {tool_name}: {ex}", exc_info=True)
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Error executing tool '{tool_name}': {str(ex)}"
                                }
                            ],
                            "isError": True
                        }
                    }
            else:
                # Unsupported method
                response = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found"
                    }
                }

            # Write JSON-RPC response followed by newline
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

        except json.JSONDecodeError:
            continue
        except Exception as e:
            logger.error(f"Unexpected MCP server error: {e}", exc_info=True)


if __name__ == "__main__":
    run_stdio_server()
