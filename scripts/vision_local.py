#!/usr/bin/env python3
"""
vision_local.py - Local Vision & UI Element Detection Module
Zero-cloud-token screen understanding using local Ollama vision models,
OpenCV template matching, and Set-of-Marks (SoM) / Grid visual overlays.
"""

import os
import sys
import re
import io
import json
import time
import base64
import csv
import difflib
import unicodedata
import subprocess
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

# Optional OpenCV import for template matching
try:
    import cv2
    import numpy as np
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
PREFERRED_VISION_MODELS = [
    "lfm3-vl",
    "lfm3-vl-nothink",
    "moondream",
    "moondream:latest",
    "qwen2-vl",
    "llava",
    "llava:7b",
    "llava:13b",
    "minicpm-v",
    "bakllava",
    "llama3.2-vision"
]


def _normalize_string(s: str) -> str:
    """Strip accents, lowercase, and normalize whitespace for robust fuzzy matching."""
    s = s.lower().strip()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


class FastOCRMatcher:
    """
    Ultra-fast screen text matcher using local Tesseract OCR.
    Extracts text bounding boxes and performs fuzzy & multi-word phrase matching in < 500ms.
    """
    _TESSERACT_BIN = None

    @classmethod
    def get_tesseract_bin(cls) -> Optional[str]:
        if cls._TESSERACT_BIN is not None:
            return cls._TESSERACT_BIN
        for candidate in ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract", "tesseract"]:
            try:
                res = subprocess.run([candidate, "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                if res.returncode == 0:
                    cls._TESSERACT_BIN = candidate
                    return candidate
            except Exception:
                pass
        return None

    @classmethod
    def find_text(cls, image_input: Any, query: str, min_ratio: float = 0.72) -> Dict[str, Any]:
        bin_path = cls.get_tesseract_bin()
        if not bin_path:
            return {"found": False, "error": "Tesseract OCR not installed or not in PATH"}

        start_t = time.time()
        temp_file = None
        if isinstance(image_input, str):
            img_path = os.path.abspath(image_input)
        elif isinstance(image_input, bytes):
            temp_file = f"/tmp/ocr_temp_{int(time.time()*1000)}.png"
            with open(temp_file, "wb") as f:
                f.write(image_input)
            img_path = temp_file
        elif isinstance(image_input, Image.Image):
            temp_file = f"/tmp/ocr_temp_{int(time.time()*1000)}.png"
            image_input.save(temp_file)
            img_path = temp_file
        else:
            return {"found": False, "error": "Invalid image input"}

        try:
            p = subprocess.run(
                [bin_path, img_path, "stdout", "--psm", "11", "tsv"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            raw_tsv = p.stdout
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

        reader = csv.DictReader(io.StringIO(raw_tsv), delimiter="\t")
        words = []
        for row in reader:
            txt = row.get("text", "").strip()
            if txt:
                try:
                    words.append({
                        "text": txt,
                        "norm": _normalize_string(txt),
                        "left": int(row.get("left", 0)),
                        "top": int(row.get("top", 0)),
                        "width": int(row.get("width", 0)),
                        "height": int(row.get("height", 0)),
                        "conf": float(row.get("conf", 0))
                    })
                except Exception:
                    pass

        norm_query = _normalize_string(query)
        latency_ms = round((time.time() - start_t) * 1000, 2)

        if not words:
            return {"found": False, "latency_ms": latency_ms, "error": "No words detected on screen"}

        # 1. Exact match on single words
        for w in words:
            if w["norm"] == norm_query or (len(norm_query) >= 4 and norm_query in w["norm"]):
                cx = w["left"] + w["width"] // 2
                cy = w["top"] + w["height"] // 2
                return {
                    "found": True,
                    "x": cx,
                    "y": cy,
                    "bbox_pixels": [w["top"], w["left"], w["top"] + w["height"], w["left"] + w["width"]],
                    "matched": w["text"],
                    "confidence": 0.95,
                    "engine": "fast_ocr",
                    "latency_ms": latency_ms
                }

        # 2. Multi-word phrase search across adjacent horizontal words (up to 5 words)
        best_candidate = None
        best_ratio = 0.0

        for i in range(len(words)):
            for j in range(i + 1, min(i + 6, len(words))):
                w1 = words[i]
                w2 = words[j]
                if abs(w1["top"] - w2["top"]) <= 14 and (w2["left"] - (w1["left"] + w1["width"])) < 45:
                    phrase = " ".join(words[k]["text"] for k in range(i, j + 1))
                    norm_phrase = _normalize_string(phrase)
                    
                    if norm_query in norm_phrase or norm_phrase in norm_query:
                        ratio = 1.0
                    else:
                        ratio = difflib.SequenceMatcher(None, norm_query, norm_phrase).ratio()

                    if ratio > best_ratio and ratio >= min_ratio:
                        best_ratio = ratio
                        left = words[i]["left"]
                        top = min(words[k]["top"] for k in range(i, j + 1))
                        right = words[j]["left"] + words[j]["width"]
                        bottom = max(words[k]["top"] + words[k]["height"] for k in range(i, j + 1))
                        best_candidate = {
                            "found": True,
                            "x": (left + right) // 2,
                            "y": (top + bottom) // 2,
                            "bbox_pixels": [top, left, bottom, right],
                            "matched": phrase,
                            "confidence": round(float(ratio), 2),
                            "engine": "fast_ocr",
                            "latency_ms": latency_ms
                        }
                        if ratio == 1.0:
                            return best_candidate

        if best_candidate:
            return best_candidate

        # 3. Fuzzy search on single words if query is short
        for w in words:
            ratio = difflib.SequenceMatcher(None, norm_query, w["norm"]).ratio()
            if ratio > best_ratio and ratio >= min_ratio:
                best_ratio = ratio
                best_candidate = {
                    "found": True,
                    "x": w["left"] + w["width"] // 2,
                    "y": w["top"] + w["height"] // 2,
                    "bbox_pixels": [w["top"], w["left"], w["top"] + w["height"], w["left"] + w["width"]],
                    "matched": w["text"],
                    "confidence": round(float(ratio), 2),
                    "engine": "fast_ocr",
                    "latency_ms": latency_ms
                }

        if best_candidate:
            return best_candidate

        return {"found": False, "latency_ms": latency_ms, "error": f"Text '{query}' not found via Fast OCR"}


class ColorBadgeDetector:
    """
    Sub-15ms OpenCV detector for notification/unread badges (WhatsApp green, iOS red, etc.).
    """
    @staticmethod
    def detect_badges(image_input: Any, color: str = "green") -> Dict[str, Any]:
        if not HAS_OPENCV:
            return {"found": False, "error": "OpenCV not available"}

        start_t = time.time()
        if isinstance(image_input, str):
            img = cv2.imread(image_input)
        elif isinstance(image_input, bytes):
            nparr = np.frombuffer(image_input, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif isinstance(image_input, Image.Image):
            img = cv2.cvtColor(np.array(image_input), cv2.COLOR_RGB2BGR)
        else:
            return {"found": False, "error": "Invalid image input"}

        if img is None:
            return {"found": False, "error": "Failed to decode image"}

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        color = color.lower()

        if color in ["green", "verde", "whatsapp"]:
            mask = ((rgb[:, :, 1] > 140) & (rgb[:, :, 0] < 120) & (rgb[:, :, 2] < 130)).astype(np.uint8) * 255
        elif color in ["red", "vermelho"]:
            mask = ((rgb[:, :, 0] > 180) & (rgb[:, :, 1] < 80) & (rgb[:, :, 2] < 80)).astype(np.uint8) * 255
        elif color in ["blue", "azul"]:
            mask = ((rgb[:, :, 2] > 180) & (rgb[:, :, 0] < 90) & (rgb[:, :, 1] < 140)).astype(np.uint8) * 255
        else:
            mask = ((rgb[:, :, 1] > 130) & (rgb[:, :, 1] > rgb[:, :, 0] + 30)).astype(np.uint8) * 255

        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
        badges = []
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]
            left = stats[i, cv2.CC_STAT_LEFT]
            top = stats[i, cv2.CC_STAT_TOP]
            cx, cy = centroids[i]
            if 30 <= area <= 1400 and 10 <= w <= 65 and 10 <= h <= 45:
                badges.append({
                    "x": int(cx),
                    "y": int(cy),
                    "bbox": [int(top), int(left), int(top + h), int(left + w)],
                    "area": int(area),
                    "w": int(w),
                    "h": int(h)
                })

        latency_ms = round((time.time() - start_t) * 1000, 2)
        if not badges:
            return {"found": False, "latency_ms": latency_ms, "error": f"No {color} badges detected"}

        badges = sorted(badges, key=lambda b: (b["y"] // 25, b["x"]))
        first = badges[0]
        return {
            "found": True,
            "x": first["x"],
            "y": first["y"],
            "bbox_pixels": first["bbox"],
            "badges_count": len(badges),
            "all_badges": badges,
            "confidence": 0.98,
            "engine": "color_badge",
            "latency_ms": latency_ms
        }


class LocalVisionEngine:
    def __init__(self, host: str = DEFAULT_OLLAMA_HOST, model: Optional[str] = None):
        self.host = host.rstrip("/")
        self.model = model or self._detect_best_vision_model()

    def _detect_best_vision_model(self) -> str:
        """Query Ollama API to find the best available vision model installed locally."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", headers={"User-Agent": "remote-desktop-operator"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                models = [m.get("name", "") for m in data.get("models", [])]
                
                # Check for preferred models in priority order
                for pref in PREFERRED_VISION_MODELS:
                    for m in models:
                        if m == pref or m.startswith(pref + ":"):
                            return m
                        
                # Check for any model containing 'vl' or 'vision'
                for m in models:
                    if "vl" in m.lower() or "vision" in m.lower() or "moondream" in m.lower():
                        return m
        except Exception:
            pass
        return "lfm3-vl"  # fallback default

    def is_available(self) -> bool:
        """Check if local Ollama vision service is responding."""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_status(self) -> Dict[str, Any]:
        """Return status info about local vision engine."""
        avail = self.is_available()
        return {
            "available": avail,
            "host": self.host,
            "selected_model": self.model,
            "has_opencv": HAS_OPENCV
        }

    def _image_to_base64(self, image_input: Any) -> Tuple[str, int, int]:
        """Convert image (path, bytes, or PIL Image) to base64 string and return (b64, width, height)."""
        if isinstance(image_input, str):
            with Image.open(image_input) as img:
                w, h = img.size
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return b64, w, h
        elif isinstance(image_input, bytes):
            with Image.open(io.BytesIO(image_input)) as img:
                w, h = img.size
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=90)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return b64, w, h
        elif isinstance(image_input, Image.Image):
            w, h = image_input.size
            buf = io.BytesIO()
            image_input.save(buf, format="JPEG", quality=90)
            b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return b64, w, h
        else:
            raise ValueError(f"Unsupported image input type: {type(image_input)}")

    def locate_element(
        self,
        image_input: Any,
        target_description: str,
        strategy: str = "auto",
        template: Optional[Any] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Locate a UI element on screen using an ultra-fast hybrid pipeline:
        1. Fast Color / Badge detection (< 15ms)
        2. Fast OCR text & phrase fuzzy matcher (< 500ms)
        3. Template matching if template provided (< 10ms)
        4. Local VLM (Ollama lfm3-vl / moondream) as comprehensive fallback (~1.5s)
        """
        strat = strategy.lower().strip()
        t_start = time.time()
        norm_desc = _normalize_string(target_description)

        # Level 1: If query explicitly asks for badge/counter/color, check ColorBadgeDetector (< 15ms)
        explicit_badge_keywords = ["badge", "selo", "contador", "bolinha", "circulo verde", "icone verde", "ponto verde"]
        if (strat in ["auto", "color"] and any(kw in norm_desc for kw in explicit_badge_keywords)) or strat == "color":
            color = "green"
            if "vermelh" in norm_desc or "red" in norm_desc:
                color = "red"
            elif "azul" in norm_desc or "blue" in norm_desc:
                color = "blue"
            badge_res = ColorBadgeDetector.detect_badges(image_input, color=color)
            if badge_res.get("found"):
                badge_res["strategy_used"] = "color_badge"
                badge_res["total_pipeline_ms"] = round((time.time() - t_start) * 1000, 2)
                return badge_res

        # Level 2: Fast Template Matching if template image provided (< 10ms)
        if (strat in ["auto", "template"] and template) or strat == "template":
            templ_res = TemplateMatcher.match(image_input, template)
            if templ_res.get("found"):
                templ_res["strategy_used"] = "template_matcher"
                templ_res["total_pipeline_ms"] = round((time.time() - t_start) * 1000, 2)
                return templ_res

        # Level 3: Fast OCR text & phrase search (< 500ms) - 90% of desktop/web UI interactions
        if strat in ["auto", "ocr", "hybrid"]:
            ocr_res = FastOCRMatcher.find_text(image_input, target_description)
            if ocr_res.get("found") and ocr_res.get("confidence", 0) >= 0.70:
                ocr_res["strategy_used"] = "fast_ocr"
                ocr_res["total_pipeline_ms"] = round((time.time() - t_start) * 1000, 2)
                return ocr_res
            elif strat == "ocr":
                # User explicitly requested OCR only
                return ocr_res

        # Level 4: Fallback to ColorBadge if query mentions unread/notifications and OCR didn't catch text
        secondary_badge_keywords = ["unread", "nao lida", "nao lidas", "notificacao", "notificacoes"]
        if strat in ["auto", "color"] and any(kw in norm_desc for kw in secondary_badge_keywords):
            badge_res = ColorBadgeDetector.detect_badges(image_input, color="green")
            if badge_res.get("found"):
                badge_res["strategy_used"] = "color_badge"
                badge_res["total_pipeline_ms"] = round((time.time() - t_start) * 1000, 2)
                return badge_res
                # User explicitly requested OCR only
                return ocr_res

        # Level 4: Local VLM (Ollama) Fallback for complex/abstract visuals
        active_model = model or self.model
        b64, width, height = self._image_to_base64(image_input)

        prompt = (
            f"You are a computer vision UI detector. Locate the following UI element on screen: '{target_description}'.\n"
            "Respond strictly in valid JSON format with coordinates scaled from 0 to 1000:\n"
            '{"box_2d": [ymin, xmin, ymax, xmax], "label": "' + target_description + '", "confidence": 0.9}\n'
            "where ymin, xmin, ymax, xmax are integers between 0 and 1000. Do not include markdown explanation."
        )

        payload = {
            "model": active_model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "options": {
                "temperature": 0.1
            }
        }

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        raw_response = ""
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_response = data.get("response", "").strip()
        except Exception as e:
            return {
                "found": False,
                "error": f"Ollama vision request failed: {e}",
                "model": active_model
            }

        # Parse JSON or extract coordinates using regex
        box_2d = None
        label = target_description
        confidence = 0.85

        # Try direct JSON parsing
        try:
            # Strip markdown json blocks if present
            clean_text = raw_response
            if "```json" in clean_text:
                clean_text = clean_text.split("```json")[1].split("```")[0].strip()
            elif "```" in clean_text:
                clean_text = clean_text.split("```")[1].split("```")[0].strip()
            
            parsed = json.loads(clean_text)
            if isinstance(parsed, dict):
                box_2d = parsed.get("box_2d") or parsed.get("bbox") or parsed.get("coordinates")
                label = parsed.get("label", label)
                confidence = float(parsed.get("confidence", 0.85))
            elif isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                box_2d = parsed[0].get("box_2d") or parsed[0].get("bbox")
                label = parsed[0].get("label", label)
        except Exception:
            pass

        # Regex fallback if JSON parsing didn't find box_2d
        if not box_2d:
            # Search for [num, num, num, num]
            m = re.search(r'\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]', raw_response)
            if m:
                box_2d = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]
            else:
                # Search for (x, y) coordinates
                m_point = re.search(r'\(?\s*(\d+)\s*,\s*(\d+)\s*\)?', raw_response)
                if m_point:
                    px, py = int(m_point.group(1)), int(m_point.group(2))
                    # Check if coordinates are 0-1000 or screen pixels
                    if px <= 1000 and py <= 1000:
                        box_2d = [max(0, py - 20), max(0, px - 40), min(1000, py + 20), min(1000, px + 40)]
                    else:
                        return {
                            "found": True,
                            "x": px,
                            "y": py,
                            "screen_width": width,
                            "screen_height": height,
                            "model": active_model,
                            "confidence": confidence,
                            "label": label,
                            "raw": raw_response
                        }

        if not box_2d or len(box_2d) < 4:
            return {
                "found": False,
                "error": "Could not parse bounding box from model output",
                "raw_response": raw_response,
                "model": active_model
            }

        ymin, xmin, ymax, xmax = [float(v) for v in box_2d[:4]]
        
        # Normalize if model gave 0.0-1.0 instead of 0-1000
        if ymax <= 1.0 and xmax <= 1.0:
            ymin, xmin, ymax, xmax = ymin * 1000, xmin * 1000, ymax * 1000, xmax * 1000

        # Scale to image dimensions
        px_ymin = int((ymin / 1000.0) * height)
        px_xmin = int((xmin / 1000.0) * width)
        px_ymax = int((ymax / 1000.0) * height)
        px_xmax = int((xmax / 1000.0) * width)

        center_x = (px_xmin + px_xmax) // 2
        center_y = (px_ymin + px_ymax) // 2

        return {
            "found": True,
            "x": center_x,
            "y": center_y,
            "bbox_pixels": [px_ymin, px_xmin, px_ymax, px_xmax],
            "bbox_normalized": [int(ymin), int(xmin), int(ymax), int(xmax)],
            "screen_width": width,
            "screen_height": height,
            "confidence": confidence,
            "label": label,
            "model": active_model,
            "strategy_used": "local_vlm",
            "total_pipeline_ms": round((time.time() - t_start) * 1000, 2),
            "raw": raw_response
        }

    def describe_screen(self, image_input: Any, prompt: Optional[str] = None) -> str:
        """Ask local vision model to describe what is currently displayed on screen."""
        b64, _, _ = self._image_to_base64(image_input)
        user_prompt = prompt or "Briefly describe the active window, visible menus, buttons, and overall state of this screen."
        
        payload = {
            "model": self.model,
            "prompt": user_prompt,
            "images": [b64],
            "stream": False
        }

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("response", "").strip()
        except Exception as e:
            return f"Error querying local vision: {e}"


class TemplateMatcher:
    """Fast, deterministic local template matching using OpenCV."""
    
    @staticmethod
    def match(
        screen_img: Any,
        template_img: Any,
        threshold: float = 0.8,
        multi_scale: bool = True
    ) -> Dict[str, Any]:
        if not HAS_OPENCV:
            return {"found": False, "error": "OpenCV (cv2) is not installed."}

        # Convert screen to numpy
        if isinstance(screen_img, str):
            screen_bgr = cv2.imread(screen_img)
        elif isinstance(screen_img, bytes):
            nparr = np.frombuffer(screen_img, np.uint8)
            screen_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif isinstance(screen_img, Image.Image):
            screen_bgr = cv2.cvtColor(np.array(screen_img), cv2.COLOR_RGB2BGR)
        else:
            return {"found": False, "error": "Invalid screen image input"}

        # Convert template to numpy
        if isinstance(template_img, str):
            templ_bgr = cv2.imread(template_img)
        elif isinstance(template_img, bytes):
            nparr = np.frombuffer(template_img, np.uint8)
            templ_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif isinstance(template_img, Image.Image):
            templ_bgr = cv2.cvtColor(np.array(template_img), cv2.COLOR_RGB2BGR)
        else:
            return {"found": False, "error": "Invalid template image input"}

        if screen_bgr is None or templ_bgr is None:
            return {"found": False, "error": "Could not decode screen or template image"}

        screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        templ_gray = cv2.cvtColor(templ_bgr, cv2.COLOR_BGR2GRAY)
        th, tw = templ_gray.shape[:2]

        scales = [1.0, 0.9, 1.1, 0.8, 1.2] if multi_scale else [1.0]
        best_match = None
        max_val_global = -1.0

        for scale in scales:
            if scale != 1.0:
                new_w = int(tw * scale)
                new_h = int(th * scale)
                if new_w >= screen_gray.shape[1] or new_h >= screen_gray.shape[0] or new_w <= 5 or new_h <= 5:
                    continue
                resized_templ = cv2.resize(templ_gray, (new_w, new_h))
            else:
                resized_templ = templ_gray

            rw, rh = resized_templ.shape[1], resized_templ.shape[0]
            res = cv2.matchTemplate(screen_gray, resized_templ, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)

            if max_val > max_val_global:
                max_val_global = max_val
                best_match = (max_loc, rw, rh, scale, max_val)

        if best_match and max_val_global >= threshold:
            max_loc, rw, rh, scale, val = best_match
            top_left = max_loc
            center_x = top_left[0] + rw // 2
            center_y = top_left[1] + rh // 2
            return {
                "found": True,
                "x": center_x,
                "y": center_y,
                "bbox_pixels": [top_left[1], top_left[0], top_left[1] + rh, top_left[0] + rw],
                "confidence": round(float(val), 3),
                "scale": scale
            }

        return {
            "found": False,
            "max_confidence": round(float(max_val_global), 3),
            "threshold": threshold
        }


class SetOfMarksGrid:
    """
    Renders high-visibility coordinate grids and numbered marks (Set of Marks / SoM)
    over a screenshot so an agent can pick exact targets with zero ambiguity.
    """
    
    @staticmethod
    def render_grid(
        image_input: Any,
        output_path: Optional[str] = None,
        cols: int = 10,
        rows: int = 10
    ) -> Tuple[Image.Image, Dict[str, Tuple[int, int]]]:
        """Draws a coordinate grid with A-J rows and 1-10 columns."""
        if isinstance(image_input, str):
            img = Image.open(image_input).convert("RGB")
        elif isinstance(image_input, bytes):
            img = Image.open(io.BytesIO(image_input)).convert("RGB")
        elif isinstance(image_input, Image.Image):
            img = image_input.copy().convert("RGB")
        else:
            raise ValueError("Invalid image input")

        w, h = img.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        dx = w / cols
        dy = h / rows
        coords_map = {}

        # Draw vertical lines
        for i in range(cols + 1):
            x = int(i * dx)
            draw.line([(x, 0), (x, h)], fill=(255, 0, 0, 100), width=1)

        # Draw horizontal lines
        for j in range(rows + 1):
            y = int(j * dy)
            draw.line([(0, y), (w, y)], fill=(255, 0, 0, 100), width=1)

        # Draw cell labels
        row_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for r in range(rows):
            r_label = row_letters[r] if r < len(row_letters) else str(r)
            for c in range(cols):
                c_label = str(c + 1)
                cell_id = f"{r_label}{c_label}"
                cx = int(c * dx + dx / 2)
                cy = int(r * dy + dy / 2)
                coords_map[cell_id] = (cx, cy)

                # Draw small badge
                bx1, by1 = int(c * dx) + 2, int(r * dy) + 2
                draw.rectangle([bx1, by1, bx1 + 28, by1 + 16], fill=(0, 0, 0, 160))
                draw.text((bx1 + 3, by1 + 2), cell_id, fill=(255, 255, 255, 240))

        combined = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        if output_path:
            combined.save(output_path)
        return combined, coords_map

    @staticmethod
    def annotate_candidates(
        image_input: Any,
        output_path: Optional[str] = None,
        min_area: int = 400,
        max_area: int = 250000
    ) -> Tuple[Image.Image, Dict[int, Dict[str, Any]]]:
        """
        Uses OpenCV edge and contour detection to find potential buttons/inputs,
        labels each candidate with a numbered badge, and returns {id: {x, y, bbox}}.
        """
        if isinstance(image_input, str):
            pil_img = Image.open(image_input).convert("RGB")
        elif isinstance(image_input, bytes):
            pil_img = Image.open(io.BytesIO(image_input)).convert("RGB")
        elif isinstance(image_input, Image.Image):
            pil_img = image_input.copy().convert("RGB")
        else:
            raise ValueError("Invalid image input")

        w, h = pil_img.size
        candidates = {}

        if HAS_OPENCV:
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            # Edge detection
            edges = cv2.Canny(gray, 50, 150)
            # Dilate to connect nearby edges
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            dilated = cv2.dilate(edges, kernel, iterations=2)
            contours, _ = cv2.findContours(dilated, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

            boxes = []
            for cnt in contours:
                x, y, bw, bh = cv2.boundingRect(cnt)
                area = bw * bh
                if min_area <= area <= max_area and 12 <= bh <= 200 and 20 <= bw <= 600:
                    aspect = bw / float(bh)
                    if 0.5 <= aspect <= 15:
                        boxes.append((x, y, bw, bh))

            # Non-maximum suppression / deduplication of overlapping boxes
            boxes = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
            filtered = []
            for b in boxes:
                x1, y1, bw, bh = b
                overlap = False
                for fb in filtered:
                    fx, fy, fbw, fbh = fb
                    if abs(x1 - fx) < 15 and abs(y1 - fy) < 15:
                        overlap = True
                        break
                if not overlap:
                    filtered.append(b)

            # Limit to top 50 elements sorted top-to-bottom, left-to-right
            filtered = sorted(filtered, key=lambda b: (b[1] // 30, b[0]))[:50]

            draw = ImageDraw.Draw(pil_img)
            for idx, (x, y, bw, bh) in enumerate(filtered, start=1):
                cx, cy = x + bw // 2, y + bh // 2
                candidates[idx] = {
                    "id": idx,
                    "x": cx,
                    "y": cy,
                    "bbox": [y, x, y + bh, x + bw]
                }
                # Draw bounding box
                draw.rectangle([x, y, x + bw, y + bh], outline=(255, 50, 50), width=2)
                # Draw numbered badge
                draw.rectangle([x, y - 18, x + 24, y], fill=(255, 50, 50))
                draw.text((x + 4, y - 16), str(idx), fill=(255, 255, 255))
        else:
            # Fallback if no OpenCV: simple 3x3 grid
            _, grid_map = SetOfMarksGrid.render_grid(pil_img, None, cols=4, rows=4)
            for idx, (code, coords) in enumerate(grid_map.items(), start=1):
                candidates[idx] = {"id": idx, "code": code, "x": coords[0], "y": coords[1]}

        if output_path:
            pil_img.save(output_path)

        return pil_img, candidates


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Local Vision & UI Detection")
    parser.add_argument("--image", required=True, help="Image file path")
    parser.add_argument("--find", help="Target element description to locate with local vision")
    parser.add_argument("--template", help="Template image path to match with OpenCV")
    parser.add_argument("--grid", action="store_true", help="Generate grid overlay")
    parser.add_argument("--candidates", action="store_true", help="Generate Set-of-Marks candidate annotations")
    parser.add_argument("--out", help="Output path for annotated image")
    parser.add_argument("--model", help="Ollama vision model to use")
    parser.add_argument("--status", action="store_true", help="Show local vision status")

    args = parser.parse_args()
    engine = LocalVisionEngine(model=args.model)

    if args.status:
        print(json.dumps(engine.get_status(), indent=2))
        sys.exit(0)

    if args.find:
        result = engine.locate_element(args.image, args.find, model=args.model)
        print(json.dumps(result, indent=2))
    elif args.template:
        result = TemplateMatcher.match(args.image, args.template)
        print(json.dumps(result, indent=2))
    elif args.grid:
        out_file = args.out or "grid_annotated.png"
        _, coords = SetOfMarksGrid.render_grid(args.image, out_file)
        print(json.dumps({"output": out_file, "cells_count": len(coords)}, indent=2))
    elif args.candidates:
        out_file = args.out or "candidates_annotated.png"
        _, marks = SetOfMarksGrid.annotate_candidates(args.image, out_file)
        print(json.dumps({"output": out_file, "elements": marks}, indent=2))
    else:
        desc = engine.describe_screen(args.image)
        print(json.dumps({"description": desc}, indent=2))
