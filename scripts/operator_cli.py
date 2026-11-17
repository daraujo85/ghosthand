#!/usr/bin/env python3
"""
operator_cli.py - Remote Desktop Operator CLI for Antigravity Agents
Enables autonomous control of mouse, keyboard, and screen (locally or remotely),
powered by zero-cloud-token local vision (Ollama / LFM3-VL / Moondream / OpenCV).
"""

import os
import sys
import time
import json
import io
import argparse
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, Any, Optional, Tuple, List

# Add current directory to path to import local modules
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from vision_local import LocalVisionEngine, TemplateMatcher, SetOfMarksGrid
from bridge_agent import ScreenCapturer, InputController


class RemoteBridgeClient:
    """Client to communicate with bridge_agent.py over HTTP."""
    def __init__(self, base_url: str, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        headers = {"User-Agent": "remote-desktop-operator-cli"}
        if self.token:
            headers["X-Auth-Token"] = self.token

        body_bytes = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body_bytes = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(resp.read().decode("utf-8"))
            elif "image" in content_type:
                return resp.read()
            else:
                return resp.read().decode("utf-8")

    def get_status(self) -> Dict[str, Any]:
        return self._request("/status", method="GET")

    def get_screenshot(self, scale: float = 1.0, fmt: str = "jpeg", quality: int = 80) -> bytes:
        qs = f"?scale={scale}&format={fmt}&quality={quality}"
        return self._request(f"/screenshot{qs}", method="GET")

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1):
        return self._request("/click", method="POST", data={"x": x, "y": y, "button": button, "clicks": clicks})

    def move(self, x: int, y: int):
        return self._request("/move", method="POST", data={"x": x, "y": y})

    def type_text(self, text: str, interval: float = 0.02):
        return self._request("/type", method="POST", data={"text": text, "interval": interval})

    def press_key(self, key: str):
        return self._request("/press", method="POST", data={"key": key})

    def key_down(self, key: str):
        return self._request("/key_down", method="POST", data={"key": key})

    def key_up(self, key: str):
        return self._request("/key_up", method="POST", data={"key": key})

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5, button: str = "left"):
        return self._request("/drag", method="POST", data={"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration": duration, "button": button})

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None):
        return self._request("/scroll", method="POST", data={"clicks": clicks, "x": x, "y": y})

    def batch(self, actions: List[Dict[str, Any]]):
        return self._request("/batch", method="POST", data={"actions": actions})


class DesktopOperator:
    def __init__(self, target: str = "local", token: Optional[str] = None, vision_model: Optional[str] = None):
        self.target = target.strip()
        self.is_remote = self.target.startswith("http://") or self.target.startswith("https://")
        self.remote_client = RemoteBridgeClient(self.target, token) if self.is_remote else None
        self.vision = LocalVisionEngine(model=vision_model)

    def get_status(self) -> Dict[str, Any]:
        """Fetch status of target machine and local vision system."""
        vision_info = self.vision.get_status()
        if self.is_remote:
            try:
                target_info = self.remote_client.get_status()
                target_info["connected"] = True
            except Exception as e:
                target_info = {"connected": False, "error": str(e)}
        else:
            w, h = ScreenCapturer.get_resolution()
            cx, cy = InputController.get_cursor_position()
            target_info = {
                "connected": True,
                "platform": sys.platform,
                "screen_width": w,
                "screen_height": h,
                "cursor_x": cx,
                "cursor_y": cy,
                "mode": "local"
            }

        return {
            "target": self.target,
            "target_info": target_info,
            "local_vision": vision_info
        }

    def capture_screen(self, out_path: Optional[str] = None, scale: float = 1.0, quality: int = 80) -> Tuple[bytes, str]:
        """Capture screenshot from local screen or remote bridge and optionally save to disk."""
        save_path = out_path or os.path.join("/tmp", f"screen_{int(time.time())}.jpg")
        fmt = "PNG" if save_path.lower().endswith(".png") else "JPEG"

        if self.is_remote:
            img_bytes = self.remote_client.get_screenshot(scale=scale, fmt=fmt.lower(), quality=quality)
        else:
            img_bytes, _, _ = ScreenCapturer.capture(scale=scale, img_format=fmt, quality=quality)

        with open(save_path, "wb") as f:
            f.write(img_bytes)

        return img_bytes, save_path

    def locate_element(
        self,
        target_description: str,
        image_path: Optional[str] = None,
        strategy: str = "auto",
        template: Optional[str] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Take screenshot (or use existing) and find coordinates using hybrid fast vision pipeline."""
        if not image_path:
            _, image_path = self.capture_screen(scale=1.0)
        return self.vision.locate_element(image_path, target_description, strategy=strategy, template=template, model=model)

    def click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        find: Optional[str] = None,
        button: str = "left",
        clicks: int = 1,
        human: bool = False,
        strategy: str = "auto",
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Click coordinates directly, or locate element visually and click it."""
        located_info = None
        if find:
            loc = self.locate_element(find, strategy=strategy, model=model)
            if not loc.get("found"):
                return {"ok": False, "error": f"Element not found: '{find}'", "details": loc}
            x = loc["x"]
            y = loc["y"]
            located_info = loc

        if x is None or y is None:
            return {"ok": False, "error": "Must specify either coordinates (x, y) or --find description."}

        if self.is_remote:
            res = self.remote_client.click(x=x, y=y, button=button, clicks=clicks)
        else:
            if human:
                InputController.human_click(x=x, y=y, button=button, clicks=clicks)
            else:
                InputController.click(x=x, y=y, button=button, clicks=clicks)
            res = {"ok": True, "action": "click", "x": x, "y": y, "button": button, "clicks": clicks, "human": human}

        if located_info:
            res["located"] = located_info
        return res

    def type_text(self, text: str, interval: float = 0.02, human: bool = False, wpm: float = 65.0) -> Dict[str, Any]:
        if self.is_remote:
            return self.remote_client.type_text(text, interval=interval)
        else:
            if human:
                InputController.human_type(text, wpm=wpm)
            else:
                InputController.type_text(text, interval=interval)
            return {"ok": True, "action": "type", "text": text, "human": human}

    def press_key(self, key: str) -> Dict[str, Any]:
        if self.is_remote:
            return self.remote_client.press_key(key)
        else:
            InputController.press_key(key)
            return {"ok": True, "action": "press", "key": key}

    def hold_key(self, key: str, duration: float = 0.5) -> Dict[str, Any]:
        if self.is_remote:
            self.remote_client.key_down(key)
            time.sleep(duration)
            self.remote_client.key_up(key)
        else:
            InputController.key_down(key)
            time.sleep(duration)
            InputController.key_up(key)
        return {"ok": True, "action": "hold_key", "key": key, "duration": duration}

    def drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.5, button: str = "left") -> Dict[str, Any]:
        if self.is_remote:
            return self.remote_client.drag(x1, y1, x2, y2, duration=duration, button=button)
        else:
            InputController.drag(x1, y1, x2, y2, duration=duration, button=button)
            return {"ok": True, "action": "drag", "from": [x1, y1], "to": [x2, y2]}

    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
        if self.is_remote:
            return self.remote_client.scroll(clicks, x=x, y=y)
        else:
            InputController.scroll(clicks, x=x, y=y)
            return {"ok": True, "action": "scroll", "clicks": clicks}

    def generate_grid(self, out_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture screen and create labeled Set-of-Marks coordinate grid."""
        _, screen_path = self.capture_screen()
        save_path = out_path or "/tmp/screen_grid.png"
        _, coords = SetOfMarksGrid.render_grid(screen_path, save_path)
        return {"ok": True, "grid_image": save_path, "cells_count": len(coords)}

    def generate_candidates(self, out_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture screen and generate numbered bounding boxes on all UI elements."""
        _, screen_path = self.capture_screen()
        save_path = out_path or "/tmp/screen_candidates.png"
        _, candidates = SetOfMarksGrid.annotate_candidates(screen_path, save_path)
        return {"ok": True, "annotated_image": save_path, "candidates": candidates}

    def watch(
        self,
        target_description: str,
        timeout: float = 30.0,
        interval: float = 2.0,
        auto_click: bool = False,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """Continuously inspect screen until target element appears."""
        start_time = time.time()
        attempt = 0

        while (time.time() - start_time) < timeout:
            attempt += 1
            _, screen_path = self.capture_screen()
            loc = self.vision.locate_element(screen_path, target_description, model=model)

            if loc.get("found"):
                res = {
                    "ok": True,
                    "found": True,
                    "target": target_description,
                    "x": loc["x"],
                    "y": loc["y"],
                    "attempts": attempt,
                    "elapsed_seconds": round(time.time() - start_time, 2)
                }
                if auto_click:
                    click_res = self.click(x=loc["x"], y=loc["y"])
                    res["click_result"] = click_res
                return res

            time.sleep(interval)

        return {
            "ok": False,
            "found": False,
            "error": f"Timeout after {timeout}s waiting for '{target_description}'",
            "attempts": attempt
        }

    def run_macro(self, macro_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute a sequence of desktop actions."""
        results = []
        for idx, step in enumerate(macro_steps):
            action = step.get("action", "").lower()
            if action == "click":
                res = self.click(
                    x=step.get("x"),
                    y=step.get("y"),
                    find=step.get("find"),
                    button=step.get("button", "left"),
                    clicks=step.get("clicks", 1),
                    human=step.get("human", False)
                )
            elif action == "move":
                if step.get("human", False):
                    InputController.human_move(step["x"], step["y"], duration=step.get("duration"))
                else:
                    InputController.move(step["x"], step["y"])
                res = {"ok": True, "action": "move", "x": step["x"], "y": step["y"]}
            elif action == "type":
                res = self.type_text(
                    step.get("text", ""),
                    interval=step.get("interval", 0.02),
                    human=step.get("human", False),
                    wpm=step.get("wpm", 65.0)
                )
            elif action == "press":
                res = self.press_key(step.get("key", ""))
            elif action == "hold":
                res = self.hold_key(step.get("key", ""), duration=step.get("duration", 0.5))
            elif action == "wait":
                sec = step.get("seconds", step.get("ms", 100) / 1000.0)
                time.sleep(sec)
                res = {"ok": True, "action": "wait", "seconds": sec}
            elif action == "screenshot":
                _, p = self.capture_screen(out_path=step.get("out"))
                res = {"ok": True, "action": "screenshot", "file": p}
            elif action == "drag":
                res = self.drag(step["x1"], step["y1"], step["x2"], step["y2"], duration=step.get("duration", 0.5))
            elif action == "scroll":
                res = self.scroll(step.get("clicks", 0))
            else:
                res = {"ok": False, "error": f"Unknown macro action '{action}'"}

            results.append({"step": idx + 1, "action": action, "result": res})
            if not res.get("ok", True) and step.get("stop_on_error", True):
                break

        return {"ok": True, "steps_executed": len(results), "details": results}


def main():
    parser = argparse.ArgumentParser(description="Remote Desktop Operator for Antigravity Agents")
    parser.add_argument("--target", default=os.environ.get("REMOTE_DESKTOP_TARGET", "local"),
                        help="Target desktop: 'local' or 'http://host:port'")
    parser.add_argument("--token", default=os.environ.get("REMOTE_DESKTOP_TOKEN"),
                        help="Optional authentication token for remote bridge")
    parser.add_argument("--model", help="Ollama local vision model (e.g. lfm3-vl, moondream, qwen2-vl)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Status
    subparsers.add_parser("status", help="Get status of target and local vision")

    # 2. Screenshot
    p_screen = subparsers.add_parser("screenshot", help="Capture screen")
    p_screen.add_argument("--out", help="Output file path (default /tmp/screen_<ts>.jpg)")
    p_screen.add_argument("--scale", type=float, default=1.0, help="Downscaling factor (0.1 to 1.0)")
    p_screen.add_argument("--quality", type=int, default=80, help="JPEG quality")

    # 3. Find
    p_find = subparsers.add_parser("find", help="Find element coordinates via hybrid local vision (OCR/Color/VLM)")
    p_find.add_argument("description", help="Natural language description of UI element")
    p_find.add_argument("--image", help="Existing screenshot file (if omitted, takes fresh screenshot)")
    p_find.add_argument("--strategy", choices=["auto", "ocr", "color", "vlm", "template"], default="auto", help="Grounding strategy: auto (cascade), ocr (<500ms), color (<15ms), or vlm (~1.5s)")

    # 4. Click
    p_click = subparsers.add_parser("click", help="Click coordinates or visual target")
    p_click.add_argument("--x", type=int, help="X coordinate")
    p_click.add_argument("--y", type=int, help="Y coordinate")
    p_click.add_argument("--find", help="Description of element to visually find and click")
    p_click.add_argument("--strategy", choices=["auto", "ocr", "color", "vlm", "template"], default="auto", help="Grounding strategy")
    p_click.add_argument("--button", default="left", help="Mouse button (left, right, middle)")
    p_click.add_argument("--double", action="store_true", help="Double click")
    p_click.add_argument("--clicks", type=int, default=1, help="Number of clicks")
    p_click.add_argument("--human", action="store_true", help="Natural human-like Bézier movement and click timing")

    # 5. Move
    p_move = subparsers.add_parser("move", help="Move mouse to coordinates")
    p_move.add_argument("--x", type=int, required=True, help="X coordinate")
    p_move.add_argument("--y", type=int, required=True, help="Y coordinate")
    p_move.add_argument("--human", action="store_true", help="Natural human-like Bézier curve and easing")
    p_move.add_argument("--duration", type=float, default=None, help="Movement duration in seconds")

    # 6. Type
    p_type = subparsers.add_parser("type", help="Type text")
    p_type.add_argument("text", help="Text to type")
    p_type.add_argument("--interval", type=float, default=0.02, help="Interval between keystrokes")
    p_type.add_argument("--human", action="store_true", help="Natural human-like typing speed, variance and pauses")
    p_type.add_argument("--wpm", type=float, default=65.0, help="Typing speed in Words Per Minute (default 65)")

    # 7. Press
    p_press = subparsers.add_parser("press", help="Press key or key combo")
    p_press.add_argument("key", help="Key name (enter, esc, tab, space, ctrl+c, alt+f4, etc.)")

    # 8. Hold
    p_hold = subparsers.add_parser("hold", help="Hold key down for duration (movement/games)")
    p_hold.add_argument("key", help="Key name (w, a, s, d, space, etc.)")
    p_hold.add_argument("--duration", type=float, default=0.5, help="Seconds to hold key")

    # 9. Drag
    p_drag = subparsers.add_parser("drag", help="Mouse drag")
    p_drag.add_argument("--from-x", type=int, required=True)
    p_drag.add_argument("--from-y", type=int, required=True)
    p_drag.add_argument("--to-x", type=int, required=True)
    p_drag.add_argument("--to-y", type=int, required=True)
    p_drag.add_argument("--duration", type=float, default=0.5)

    # 10. Scroll
    p_scroll = subparsers.add_parser("scroll", help="Mouse wheel scroll")
    p_scroll.add_argument("clicks", type=int, help="Scroll clicks (positive=up, negative=down)")
    p_scroll.add_argument("--x", type=int, help="Optional X position")
    p_scroll.add_argument("--y", type=int, help="Optional Y position")

    # 11. Grid
    p_grid = subparsers.add_parser("grid", help="Generate coordinate grid overlay")
    p_grid.add_argument("--out", default="/tmp/screen_grid.png", help="Output path")

    # 12. Candidates
    p_cand = subparsers.add_parser("candidates", help="Generate Set-of-Marks numbered badges on buttons")
    p_cand.add_argument("--out", default="/tmp/screen_candidates.png", help="Output path")

    # 13. Describe
    p_desc = subparsers.add_parser("describe", help="Ask local vision model to describe screen")
    p_desc.add_argument("--prompt", help="Specific question about the screen")
    p_desc.add_argument("--image", help="Existing screenshot")

    # 14. Watch
    p_watch = subparsers.add_parser("watch", help="Poll screen until element appears")
    p_watch.add_argument("target", help="Element to wait for")
    p_watch.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds")
    p_watch.add_argument("--interval", type=float, default=2.0, help="Polling interval")
    p_watch.add_argument("--click", action="store_true", help="Auto-click once found")

    # 15. Macro
    p_macro = subparsers.add_parser("macro", help="Execute JSON macro file or inline JSON")
    p_macro.add_argument("file_or_json", help="Path to JSON file or raw JSON array")

    # 16. MCP Server
    subparsers.add_parser("mcp", help="Launch GhostHand Model Context Protocol (MCP) stdio server")

    args = parser.parse_args()
    operator = DesktopOperator(target=args.target, token=args.token, vision_model=args.model)

    if args.command == "status":
        print(json.dumps(operator.get_status(), indent=2))
    elif args.command == "screenshot":
        img_bytes, path = operator.capture_screen(out_path=args.out, scale=args.scale, quality=args.quality)
        print(json.dumps({"ok": True, "file": path, "bytes": len(img_bytes)}, indent=2))
    elif args.command == "find":
        res = operator.locate_element(args.description, image_path=args.image, strategy=args.strategy, model=args.model)
        print(json.dumps(res, indent=2))
    elif args.command == "click":
        clicks = 2 if args.double else args.clicks
        res = operator.click(x=args.x, y=args.y, find=args.find, button=args.button, clicks=clicks, human=args.human, strategy=args.strategy, model=args.model)
        print(json.dumps(res, indent=2))
    elif args.command == "move":
        if operator.is_remote:
            res = operator.remote_client.move(args.x, args.y)
        else:
            if args.human:
                InputController.human_move(args.x, args.y, duration=args.duration)
            else:
                InputController.move(args.x, args.y)
            res = {"ok": True, "action": "move", "x": args.x, "y": args.y, "human": args.human}
        print(json.dumps(res, indent=2))
    elif args.command == "type":
        res = operator.type_text(args.text, interval=args.interval, human=args.human, wpm=args.wpm)
        print(json.dumps(res, indent=2))
    elif args.command == "press":
        res = operator.press_key(args.key)
        print(json.dumps(res, indent=2))
    elif args.command == "hold":
        res = operator.hold_key(args.key, duration=args.duration)
        print(json.dumps(res, indent=2))
    elif args.command == "drag":
        res = operator.drag(args.from_x, args.from_y, args.to_x, args.to_y, duration=args.duration)
        print(json.dumps(res, indent=2))
    elif args.command == "scroll":
        res = operator.scroll(args.clicks, x=args.x, y=args.y)
        print(json.dumps(res, indent=2))
    elif args.command == "grid":
        res = operator.generate_grid(out_path=args.out)
        print(json.dumps(res, indent=2))
    elif args.command == "candidates":
        res = operator.generate_candidates(out_path=args.out)
        print(json.dumps(res, indent=2))
    elif args.command == "describe":
        img_path = args.image
        if not img_path:
            _, img_path = operator.capture_screen()
        desc = operator.vision.describe_screen(img_path, prompt=args.prompt)
        print(json.dumps({"description": desc, "image": img_path}, indent=2))
    elif args.command == "watch":
        res = operator.watch(args.target, timeout=args.timeout, interval=args.interval, auto_click=args.click, model=args.model)
        print(json.dumps(res, indent=2))
    elif args.command == "macro":
        macro_content = args.file_or_json
        if os.path.exists(macro_content):
            with open(macro_content, "r") as f:
                steps = json.load(f)
        else:
            steps = json.loads(macro_content)
        res = operator.run_macro(steps)
        print(json.dumps(res, indent=2))
    elif args.command == "mcp":
        from mcp_server import run_stdio_server
        run_stdio_server()


if __name__ == "__main__":
    main()
