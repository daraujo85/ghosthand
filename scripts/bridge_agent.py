#!/usr/bin/env python3
"""
bridge_agent.py - Lightweight Remote Desktop & Input Bridge Daemon
Provides screen capture and DirectInput/mouse/keyboard emulation for Windows, Mac, and Linux.
Designed for low latency, zero external server bloat (pure Python http.server),
and full DirectX/DirectInput compatibility for games (e.g. SP Football Life, Steam games).
"""

import os
import sys
import time
import json
import io
import math
import random
import argparse
from typing import Dict, Any, Optional, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

# 1. Screen capture backend detection
HAS_MSS = False
try:
    import mss
    def get_mss():
        return mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    HAS_MSS = True
except ImportError:
    pass

HAS_PIL = False
try:
    from PIL import Image, ImageGrab
    HAS_PIL = True
except ImportError:
    pass

# 2. Input backend detection
IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

HAS_PYDIRECTINPUT = False
if IS_WINDOWS:
    try:
        import pydirectinput
        pydirectinput.PAUSE = 0.02
        HAS_PYDIRECTINPUT = True
    except ImportError:
        pass

HAS_PYAUTOGUI = False
try:
    import pyautogui
    pyautogui.PAUSE = 0.02
    pyautogui.FAILSAFE = False
    HAS_PYAUTOGUI = True
except ImportError:
    pass

# Windows native ctypes fallback for DirectInput scancodes
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    # DirectX Scan Codes for common keys
    SCANCODES = {
        "escape": 0x01, "esc": 0x01, "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05,
        "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B,
        "backspace": 0x0E, "tab": 0x0F, "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13,
        "t": 0x14, "y": 0x15, "u": 0x16, "i": 0x17, "o": 0x18, "p": 0x19,
        "enter": 0x1C, "return": 0x1C, "ctrl": 0x1D, "leftctrl": 0x1D,
        "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
        "j": 0x24, "k": 0x25, "l": 0x26, "shift": 0x2A, "leftshift": 0x2A,
        "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30, "n": 0x31,
        "m": 0x32, "alt": 0x38, "leftalt": 0x38, "space": 0x39,
        "f1": 0x3B, "f2": 0x3C, "f3": 0x3D, "f4": 0x3E, "f5": 0x3F, "f6": 0x40,
        "f7": 0x41, "f8": 0x42, "f9": 0x43, "f10": 0x44, "f11": 0x57, "f12": 0x58,
        "up": 0xC8, "down": 0xD0, "left": 0xCB, "right": 0xCD,
        "delete": 0xD3, "home": 0xC7, "end": 0xCF, "pageup": 0xC9, "pagedown": 0xD1
    }


class ScreenCapturer:
    """Fast screen capture using mss or PIL ImageGrab."""

    @staticmethod
    def get_resolution() -> Tuple[int, int]:
        if HAS_MSS:
            with get_mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                return mon["width"], mon["height"]
        elif HAS_PIL:
            img = ImageGrab.grab()
            return img.size
        elif HAS_PYAUTOGUI:
            sz = pyautogui.size()
            return sz.width, sz.height
        return (1920, 1080)

    @staticmethod
    def capture(
        scale: float = 1.0,
        img_format: str = "JPEG",
        quality: int = 80,
        bbox: Optional[Tuple[int, int, int, int]] = None
    ) -> Tuple[bytes, int, int]:
        """
        Captures the screen and returns (bytes, width, height).
        scale: 0.1 to 1.0 to downsample and save bandwidth/latency.
        """
        pil_img = None

        if HAS_MSS:
            with get_mss() as sct:
                mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                if bbox:
                    mon = {
                        "left": bbox[0],
                        "top": bbox[1],
                        "width": bbox[2] - bbox[0],
                        "height": bbox[3] - bbox[1]
                    }
                sct_img = sct.grab(mon)
                pil_img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        elif HAS_PIL:
            pil_img = ImageGrab.grab(bbox=bbox)
        else:
            raise RuntimeError("No screen capture library available (mss or PIL required).")

        orig_w, orig_h = pil_img.size
        if scale < 0.99:
            new_w = max(1, int(orig_w * scale))
            new_h = max(1, int(orig_h * scale))
            pil_img = pil_img.resize((new_w, new_h), Image.Resampling.BILINEAR)

        buf = io.BytesIO()
        fmt = img_format.upper()
        if fmt == "JPG":
            fmt = "JPEG"
        if fmt == "JPEG":
            pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
        else:
            pil_img.save(buf, format=fmt)

        return buf.getvalue(), orig_w, orig_h


class InputController:
    """Unified cross-platform input controller supporting DirectInput on Windows for games."""

    @staticmethod
    def get_cursor_position() -> Tuple[int, int]:
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pos = pydirectinput.position()
            return pos[0], pos[1]
        elif HAS_PYAUTOGUI:
            pos = pyautogui.position()
            return pos.x, pos.y
        elif IS_WINDOWS:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
        return (0, 0)

    @staticmethod
    def human_move(target_x: int, target_y: int, duration: Optional[float] = None, steady: bool = False):
        """Move cursor to target using a natural cubic Bézier curve, easing, and micro-jitter."""
        start_x, start_y = InputController.get_cursor_position()
        dist = math.hypot(target_x - start_x, target_y - start_y)
        if dist < 4:
            InputController.move(target_x, target_y)
            return

        if duration is None:
            # Scale duration with distance (approx 0.6s to 1.4s for visible human glide)
            duration = max(0.6, min(1.5, 0.45 + 0.001 * dist))

        dx = target_x - start_x
        dy = target_y - start_y
        perp_x = -dy
        perp_y = dx
        norm = math.hypot(perp_x, perp_y)
        if norm > 0:
            perp_x /= norm
            perp_y /= norm

        # Random arc deviation
        arc = random.choice([-1, 1]) * random.uniform(0.12, 0.28) * min(dist, 250)
        p1_x = start_x + dx * 0.25 + perp_x * arc
        p1_y = start_y + dy * 0.25 + perp_y * arc
        p2_x = start_x + dx * 0.75 + perp_x * (arc * 0.4)
        p2_y = start_y + dy * 0.75 + perp_y * (arc * 0.4)

        steps = max(30, int(duration * 60))
        dt = duration / steps

        for step in range(steps + 1):
            t = step / steps
            # Smootherstep easing: 6t^5 - 15t^4 + 10t^3
            eased_t = t * t * t * (t * (t * 6 - 15) + 10)
            u = 1.0 - eased_t

            # Cubic Bézier
            bx = (u**3)*start_x + 3*(u**2)*eased_t*p1_x + 3*u*(eased_t**2)*p2_x + (eased_t**3)*target_x
            by = (u**3)*start_y + 3*(u**2)*eased_t*p1_y + 3*u*(eased_t**2)*p2_y

            # Micro-jitter along trajectory (sub-pixel noise, 0 at end)
            if step < steps and not steady:
                jx = random.uniform(-0.5, 0.5)
                jy = random.uniform(-0.5, 0.5)
            else:
                jx, jy = 0.0, 0.0

            cur_x = int(round(bx + jx))
            cur_y = int(round(by + jy))

            InputController.move(cur_x, cur_y)
            time.sleep(dt)

        # Micro-settle pause
        time.sleep(random.uniform(0.05, 0.12))

    @staticmethod
    def human_click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1):
        """Move cursor naturally like a human to (x, y), pause briefly, then click."""
        if x is not None and y is not None:
            InputController.human_move(x, y)
        time.sleep(random.uniform(0.06, 0.14))
        btn = button.lower()
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            for i in range(clicks):
                pydirectinput.click(button=btn)
                if i < clicks - 1:
                    time.sleep(random.uniform(0.08, 0.16))
        elif HAS_PYAUTOGUI:
            for i in range(clicks):
                pyautogui.click(button=btn)
                if i < clicks - 1:
                    time.sleep(random.uniform(0.08, 0.16))

    @staticmethod
    def human_type(text: str, wpm: float = 65.0):
        """Type text with realistic human variance, pauses on spaces, punctuation, and slight hesitations."""
        base_delay = 60.0 / (wpm * 5.0)  # ~0.18s
        for ch in text:
            if ch in '.!?':
                delay = random.uniform(0.28, 0.50)
            elif ch in ',;:':
                delay = random.uniform(0.18, 0.32)
            elif ch == ' ':
                delay = random.uniform(0.12, 0.22)
            else:
                delay = max(0.04, random.gauss(base_delay, base_delay * 0.35))

            if HAS_PYDIRECTINPUT and IS_WINDOWS:
                pydirectinput.write(ch)
            elif HAS_PYAUTOGUI:
                pyautogui.write(ch)
            time.sleep(delay)

    @staticmethod
    def move(x: int, y: int, duration: float = 0.0):
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.moveTo(int(x), int(y))
        elif HAS_PYAUTOGUI:
            pyautogui.moveTo(int(x), int(y), duration=duration)

    @staticmethod
    def click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1, interval: float = 0.1):
        if x is not None and y is not None:
            InputController.move(x, y)
            time.sleep(0.02)

        btn = button.lower()
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            for i in range(clicks):
                pydirectinput.click(button=btn)
                if i < clicks - 1:
                    time.sleep(interval)
        elif HAS_PYAUTOGUI:
            pyautogui.click(button=btn, clicks=clicks, interval=interval)

    @staticmethod
    def double_click(x: Optional[int] = None, y: Optional[int] = None):
        InputController.click(x=x, y=y, clicks=2, interval=0.1)

    @staticmethod
    def mouse_down(button: str = "left"):
        btn = button.lower()
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.mouseDown(button=btn)
        elif HAS_PYAUTOGUI:
            pyautogui.mouseDown(button=btn)

    @staticmethod
    def mouse_up(button: str = "left"):
        btn = button.lower()
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.mouseUp(button=btn)
        elif HAS_PYAUTOGUI:
            pyautogui.mouseUp(button=btn)

    @staticmethod
    def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5, button: str = "left"):
        InputController.move(x1, y1)
        time.sleep(0.05)
        InputController.mouse_down(button=button)
        time.sleep(0.05)
        InputController.move(x2, y2, duration=duration)
        time.sleep(0.05)
        InputController.mouse_up(button=button)

    @staticmethod
    def scroll(clicks: int, x: Optional[int] = None, y: Optional[int] = None):
        if x is not None and y is not None:
            InputController.move(x, y)
        if HAS_PYAUTOGUI:
            pyautogui.scroll(clicks)
        elif HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.scroll(clicks)

    @staticmethod
    def type_text(text: str, interval: float = 0.02):
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.write(text, interval=interval)
        elif HAS_PYAUTOGUI:
            pyautogui.write(text, interval=interval)

    @staticmethod
    def press_key(key: str):
        key = key.lower().strip()
        # Handle combinations like "ctrl+c" or "alt+f4"
        if "+" in key:
            parts = [p.strip() for p in key.split("+")]
            for p in parts:
                InputController.key_down(p)
                time.sleep(0.02)
            for p in reversed(parts):
                InputController.key_up(p)
                time.sleep(0.02)
            return

        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.press(key)
        elif HAS_PYAUTOGUI:
            pyautogui.press(key)

    @staticmethod
    def key_down(key: str):
        key = key.lower().strip()
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.keyDown(key)
        elif HAS_PYAUTOGUI:
            pyautogui.keyDown(key)

    @staticmethod
    def key_up(key: str):
        key = key.lower().strip()
        if HAS_PYDIRECTINPUT and IS_WINDOWS:
            pydirectinput.keyUp(key)
        elif HAS_PYAUTOGUI:
            pyautogui.keyUp(key)


class BridgeServerHandler(BaseHTTPRequestHandler):
    auth_token: Optional[str] = None

    def _check_auth(self) -> bool:
        if not self.auth_token:
            return True
        # Check header
        hdr = self.headers.get("X-Auth-Token")
        if hdr == self.auth_token:
            return True
        # Check query string
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("token", [None])[0] == self.auth_token:
            return True
        return False

    def _send_json(self, data: Dict[str, Any], status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._check_auth():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ["/status", "/health", "/"]:
            w, h = ScreenCapturer.get_resolution()
            cx, cy = InputController.get_cursor_position()
            self._send_json({
                "status": "online",
                "platform": sys.platform,
                "screen_width": w,
                "screen_height": h,
                "cursor_x": cx,
                "cursor_y": cy,
                "direct_input": HAS_PYDIRECTINPUT,
                "pyautogui": HAS_PYAUTOGUI,
                "mss": HAS_MSS
            })
        elif path == "/screenshot":
            scale = float(qs.get("scale", [1.0])[0])
            fmt = qs.get("format", ["jpeg"])[0].lower()
            quality = int(qs.get("quality", [80])[0])

            try:
                img_bytes, w, h = ScreenCapturer.capture(scale=scale, img_format=fmt, quality=quality)
                content_type = "image/png" if fmt == "png" else "image/jpeg"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(img_bytes)))
                self.send_header("X-Screen-Width", str(w))
                self.send_header("X-Screen-Height", str(h))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(img_bytes)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        elif path == "/cursor":
            cx, cy = InputController.get_cursor_position()
            self._send_json({"x": cx, "y": cy})
        else:
            self._send_json({"error": "Endpoint not found"}, 404)

    def do_POST(self):
        if not self._check_auth():
            self._send_json({"error": "Unauthorized"}, 401)
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Read JSON body
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            params = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            params = {}

        try:
            if path == "/click":
                InputController.click(
                    x=params.get("x"),
                    y=params.get("y"),
                    button=params.get("button", "left"),
                    clicks=params.get("clicks", 1),
                    interval=params.get("interval", 0.1)
                )
                self._send_json({"ok": True, "action": "click", "params": params})
            elif path == "/move":
                InputController.move(
                    x=params.get("x", 0),
                    y=params.get("y", 0),
                    duration=params.get("duration", 0.0)
                )
                self._send_json({"ok": True, "action": "move"})
            elif path == "/mouse_down":
                InputController.mouse_down(button=params.get("button", "left"))
                self._send_json({"ok": True, "action": "mouse_down"})
            elif path == "/mouse_up":
                InputController.mouse_up(button=params.get("button", "left"))
                self._send_json({"ok": True, "action": "mouse_up"})
            elif path == "/drag":
                InputController.drag(
                    x1=params["x1"],
                    y1=params["y1"],
                    x2=params["x2"],
                    y2=params["y2"],
                    duration=params.get("duration", 0.5),
                    button=params.get("button", "left")
                )
                self._send_json({"ok": True, "action": "drag"})
            elif path == "/type":
                InputController.type_text(
                    text=params.get("text", ""),
                    interval=params.get("interval", 0.02)
                )
                self._send_json({"ok": True, "action": "type"})
            elif path == "/press":
                key = params.get("key") or params.get("keys")
                if isinstance(key, list):
                    for k in key:
                        InputController.press_key(k)
                else:
                    InputController.press_key(str(key))
                self._send_json({"ok": True, "action": "press", "key": key})
            elif path == "/key_down":
                InputController.key_down(params.get("key", ""))
                self._send_json({"ok": True, "action": "key_down"})
            elif path == "/key_up":
                InputController.key_up(params.get("key", ""))
                self._send_json({"ok": True, "action": "key_up"})
            elif path == "/scroll":
                InputController.scroll(
                    clicks=params.get("clicks", 0),
                    x=params.get("x"),
                    y=params.get("y")
                )
                self._send_json({"ok": True, "action": "scroll"})
            elif path == "/batch":
                actions = params.get("actions", [])
                for act in actions:
                    act_type = act.get("action")
                    if act_type == "click":
                        InputController.click(act.get("x"), act.get("y"), act.get("button", "left"), act.get("clicks", 1))
                    elif act_type == "move":
                        InputController.move(act.get("x"), act.get("y"))
                    elif act_type == "type":
                        InputController.type_text(act.get("text", ""))
                    elif act_type == "press":
                        InputController.press_key(act.get("key", ""))
                    elif act_type == "wait":
                        time.sleep(act.get("seconds", act.get("ms", 100) / 1000.0))
                self._send_json({"ok": True, "batch_count": len(actions)})
            else:
                self._send_json({"error": "Unknown POST endpoint"}, 404)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


def run_server(host: str = "0.0.0.0", port: int = 8765, token: Optional[str] = None):
    BridgeServerHandler.auth_token = token
    server = HTTPServer((host, port), BridgeServerHandler)
    print(f"[RemoteBridge] Listening on http://{host}:{port}")
    if token:
        print("[RemoteBridge] Auth token enabled.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[RemoteBridge] Server stopped.")


def execute_action(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a single action directly via CLI without running a server."""
    act = action.lower()
    if act == "status":
        w, h = ScreenCapturer.get_resolution()
        cx, cy = InputController.get_cursor_position()
        return {
            "status": "online",
            "platform": sys.platform,
            "screen_width": w,
            "screen_height": h,
            "cursor_x": cx,
            "cursor_y": cy,
            "direct_input": HAS_PYDIRECTINPUT,
            "pyautogui": HAS_PYAUTOGUI
        }
    elif act == "screenshot":
        out_file = params.get("out", "screenshot.jpg")
        scale = float(params.get("scale", 1.0))
        fmt = "PNG" if out_file.lower().endswith(".png") else "JPEG"
        img_bytes, w, h = ScreenCapturer.capture(scale=scale, img_format=fmt)
        with open(out_file, "wb") as f:
            f.write(img_bytes)
        return {"ok": True, "file": out_file, "screen_width": w, "screen_height": h, "bytes": len(img_bytes)}
    elif act == "click":
        InputController.click(
            x=params.get("x"),
            y=params.get("y"),
            button=params.get("button", "left"),
            clicks=int(params.get("clicks", 1))
        )
        return {"ok": True, "action": "click", "x": params.get("x"), "y": params.get("y")}
    elif act == "move":
        InputController.move(int(params["x"]), int(params["y"]))
        return {"ok": True, "action": "move"}
    elif act == "type":
        InputController.type_text(params.get("text", ""))
        return {"ok": True, "action": "type"}
    elif act == "press":
        InputController.press_key(params.get("key", ""))
        return {"ok": True, "action": "press", "key": params.get("key")}
    elif act == "drag":
        InputController.drag(params["x1"], params["y1"], params["x2"], params["y2"])
        return {"ok": True, "action": "drag"}
    elif act == "scroll":
        InputController.scroll(int(params.get("clicks", 0)))
        return {"ok": True, "action": "scroll"}
    else:
        return {"error": f"Unknown action: {action}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remote Desktop Operator Bridge Agent")
    parser.add_argument("--server", action="store_true", help="Run as persistent HTTP server daemon")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind server")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind server")
    parser.add_argument("--token", help="Optional authentication token")

    # CLI direct execution options
    parser.add_argument("--action", help="Direct action to execute (status, screenshot, click, type, press, drag, scroll)")
    parser.add_argument("--x", type=int, help="X coordinate")
    parser.add_argument("--y", type=int, help="Y coordinate")
    parser.add_argument("--text", help="Text to type")
    parser.add_argument("--key", help="Key to press (e.g. enter, esc, ctrl+c)")
    parser.add_argument("--button", default="left", help="Mouse button (left, right, middle)")
    parser.add_argument("--clicks", type=int, default=1, help="Number of clicks")
    parser.add_argument("--out", help="Output file for screenshot")
    parser.add_argument("--scale", type=float, default=1.0, help="Screenshot downscale factor")
    parser.add_argument("--exec", dest="exec_json", help="Execute raw JSON action string")

    args = parser.parse_args()

    if args.server:
        run_server(host=args.host, port=args.port, token=args.token)
    elif args.exec_json:
        payload = json.loads(args.exec_json)
        res = execute_action(payload.get("action", ""), payload)
        print(json.dumps(res, indent=2))
    elif args.action:
        params = {
            "x": args.x,
            "y": args.y,
            "text": args.text,
            "key": args.key,
            "button": args.button,
            "clicks": args.clicks,
            "out": args.out or "screenshot.jpg",
            "scale": args.scale
        }
        res = execute_action(args.action, params)
        print(json.dumps(res, indent=2))
    else:
        parser.print_help()
