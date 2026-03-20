import asyncio
import logging
import os
import base64
import json
from typing import List, Optional, Dict, Any
from PIL import Image
from dataclasses import dataclass
import aiohttp

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


@dataclass
class VLMResponse:
    text: str
    elements: List[Dict[str, Any]]
    latency_ms: float
    source: str


class LocalVLM:
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = "llava:latest"
        self._session: Optional[aiohttp.ClientSession] = None
        self._is_available = None
        self._last_check = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def check_availability(self) -> bool:
        import time
        if time.time() - self._last_check < 30:
            return self._is_available or False
        self._last_check = time.time()

        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/tags", timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    self._is_available = any("llava" in m.lower() for m in models)
                    logging.info(f"Ollama availability: {self._is_available}, models: {models}")
                    return self._is_available
        except Exception as e:
            logging.warning(f"Ollama not available: {e}")
            self._is_available = False

        return False

    async def analyze_image(self, image_path: str, prompt: str) -> VLMResponse:
        import time
        start = time.time()

        if not os.path.exists(image_path):
            return VLMResponse(
                text=f"Error: Image not found: {image_path}",
                elements=[],
                latency_ms=0,
                source="error"
            )

        with open(image_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode("utf-8")

        try:
            session = await self._get_session()

            payload = {
                "model": self.model,
                "prompt": prompt,
                "images": [img_base64],
                "stream": False,
                "options": {"temperature": 0.1}
            }

            async with session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    response_text = data.get("response", "")

                    elements = self._parse_ui_elements(response_text)

                    return VLMResponse(
                        text=response_text,
                        elements=elements,
                        latency_ms=(time.time() - start) * 1000,
                        source="ollama"
                    )
                else:
                    error_text = await resp.text()
                    logging.error(f"Ollama error: {resp.status} - {error_text}")

        except asyncio.TimeoutError:
            logging.error("Ollama request timed out")
        except Exception as e:
            logging.error(f"Ollama request failed: {e}")

        return VLMResponse(
            text="",
            elements=[],
            latency_ms=(time.time() - start) * 1000,
            source="unavailable"
        )

    def _parse_ui_elements(self, text: str) -> List[Dict[str, Any]]:
        import re
        elements = []

        json_matches = re.findall(r'\[.*?\]', text, re.DOTALL)
        for match in json_matches:
            try:
                data = json.loads(match)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "type" in item:
                            elements.append(item)
            except json.JSONDecodeError:
                pass

        patterns = [
            r'(?:button|link|input|menu|checkbox|radio|dropdown|tab)["\s:]+["\']?([^"\'\n]+)["\']?',
            r'\[(\d+)\]\s*<(\w+)>\s*["\']([^"\']+)["\']',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text.lower())
            for match in matches:
                if len(match) >= 3:
                    elem_type = match[1] if len(match) > 1 else "unknown"
                    label = match[2] if len(match) > 2 else match[0]
                    elements.append({
                        "type": elem_type,
                        "label": label.strip(),
                        "confidence": 0.8
                    })
                elif len(match) == 1:
                    elements.append({
                        "type": "unknown",
                        "label": match[0].strip(),
                        "confidence": 0.5
                    })

        seen = set()
        unique_elements = []
        for elem in elements:
            key = (elem.get("type"), elem.get("label", "").lower())
            if key not in seen:
                seen.add(key)
                unique_elements.append(elem)

        return unique_elements

    async def find_element(self, image_path: str, element_description: str) -> Optional[Dict[str, Any]]:
        prompt = f"""Find the element described as "{element_description}" in this UI screenshot.
Return ONLY a JSON object with keys: x, y, width, height, type, label.
If not found, return: {{"found": false}}
Example: {{"found": true, "x": 150, "y": 300, "width": 100, "height": 40, "type": "button", "label": "Submit"}}
"""
        result = await self.analyze_image(image_path, prompt)

        if result.text and "found" in result.text.lower():
            import re
            match = re.search(r'\{[^}]+\}', result.text, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                    if "found" in data and not data["found"]:
                        return None
                    return data
                except json.JSONDecodeError:
                    pass

        return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class AdaptiveCVDetector:
    def __init__(self):
        self._screenshot_cache = {}
        self._cache_ttl = 1.0
        self._last_screenshot_time = 0

    def detect_buttons_adaptive(self, screenshot_path: str) -> List[Dict[str, Any]]:
        try:
            import cv2
            import numpy as np

            img = cv2.imread(screenshot_path)
            if img is None:
                return []

            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            elements = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                area = cv2.contourArea(contour)

                if 300 < area < 50000 and 10 < h < 200 and 20 < w < 600:
                    roi = img[y:y+h, x:x+w]
                    mean_color = roi.mean(axis=(0, 1))

                    is_light = mean_color[2] > 150
                    is_dark = mean_color[2] < 100
                    is_colored = abs(mean_color[0] - mean_color[1]) > 30 or abs(mean_color[1] - mean_color[2]) > 30

                    elem_type = "button"
                    if is_colored:
                        elem_type = "colored_button"
                    elif is_light:
                        elem_type = "light_button"
                    elif is_dark:
                        elem_type = "dark_button"

                    elements.append({
                        "x": int(x),
                        "y": int(y),
                        "width": int(w),
                        "height": int(h),
                        "type": elem_type,
                        "label": f"{elem_type}_{len(elements)}",
                        "confidence": 0.6 + (0.2 if is_colored else 0)
                    })

            elements = self._merge_overlapping(elements)

            return elements

        except Exception as e:
            logging.debug(f"Adaptive CV detection error: {e}")
            return []

    def detect_inputs_adaptive(self, screenshot_path: str) -> List[Dict[str, Any]]:
        try:
            import cv2
            import numpy as np

            img = cv2.imread(screenshot_path)
            if img is None:
                return []

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            lines = cv2.HoughLinesP(
                gray, 1, np.pi/180,
                threshold=50,
                minLineLength=50,
                maxLineGap=10
            )

            rectangles = []
            if lines is not None:
                for line in lines:
                    x1, y1, x2, y2 = line[0]
                    if abs(y1 - y2) < 5:
                        rectangles.append((min(x1, x2), min(y1, y2), abs(x2-x1), 20))

            edges = cv2.Canny(gray, 30, 90)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                aspect = w / h if h > 0 else 0
                if 2 < aspect < 25 and 15 < h < 60 and 40 < w < 1000:
                    rectangles.append((x, y, w, h))

            rectangles = self._merge_horizontal_lines(rectangles)

            elements = []
            for x, y, w, h in rectangles:
                elements.append({
                    "x": int(x),
                    "y": int(y),
                    "width": int(w),
                    "height": int(h),
                    "type": "input",
                    "label": f"input_{len(elements)}",
                    "confidence": 0.5
                })

            return elements

        except Exception as e:
            logging.debug(f"Adaptive input detection error: {e}")
            return []

    def _merge_overlapping(self, elements: List[Dict], threshold: float = 0.5) -> List[Dict]:
        if not elements:
            return []

        merged = True
        while merged:
            merged = False
            new_elements = []

            for i, elem1 in enumerate(elements):
                for j, elem2 in enumerate(elements):
                    if i != j and elem1 not in new_elements and elem2 not in new_elements:
                        if self._boxes_overlap(elem1, elem2, threshold):
                            new_elem = self._combine_boxes(elem1, elem2)
                            new_elements.append(new_elem)
                            merged = True
                        else:
                            if elem1 not in new_elements:
                                new_elements.append(elem1)

                if elem1 not in new_elements:
                    new_elements.append(elem1)

            elements = new_elements

        return elements

    def _merge_horizontal_lines(self, rectangles: List) -> List:
        if not rectangles:
            return []

        merged = True
        while merged:
            merged = False
            new_rects = []

            for i, (x1, y1, w1, h1) in enumerate(rectangles):
                for j, (x2, y2, w2, h2) in enumerate(rectangles):
                    if i != j:
                        if abs(y1 - y2) < 10 and abs(h1 - h2) < 10 and x1 < x2 < x1 + w1:
                            nx = min(x1, x2)
                            ny = min(y1, y2)
                            nw = max(x1 + w1, x2 + w2) - nx
                            nh = max(h1, h2)
                            rectangles[i] = (nx, ny, nw, nh)
                            merged = True
                        elif i not in [r[0] if isinstance(r, tuple) else -1 for r in new_rects]:
                            if not any(x1 == r[0] and y1 == r[1] for r in new_rects if isinstance(r, tuple)):
                                new_rects.append((x1, y1, w1, h1))

            rectangles = [r for r in rectangles if not any(r[0] == nr[0] and r[1] == nr[1] for nr in new_rects if isinstance(nr, tuple))]
            rectangles.extend(new_rects)
            merged = len(rectangles) > len(new_rects)

        return rectangles[:20]

    def _boxes_overlap(self, box1: Dict, box2: Dict, threshold: float) -> bool:
        x1 = max(box1["x"], box2["x"])
        y1 = max(box1["y"], box2["y"])
        x2 = min(box1["x"] + box1["width"], box2["x"] + box2["width"])
        y2 = min(box1["y"] + box1["height"], box2["y"] + box2["height"])

        if x2 <= x1 or y2 <= y1:
            return False

        intersection = (x2 - x1) * (y2 - y1)
        area1 = box1["width"] * box1["height"]
        area2 = box2["width"] * box2["height"]
        union = area1 + area2 - intersection

        return (intersection / union) > threshold if union > 0 else False

    def _combine_boxes(self, box1: Dict, box2: Dict) -> Dict:
        return {
            "x": min(box1["x"], box2["x"]),
            "y": min(box1["y"], box2["y"]),
            "width": max(box1["x"] + box1["width"], box2["x"] + box2["width"]) - min(box1["x"], box2["x"]),
            "height": max(box1["y"] + box1["height"], box2["y"] + box2["height"]) - min(box1["y"], box2["y"]),
            "type": box1.get("type", "unknown"),
            "label": box1.get("label", "merged"),
            "confidence": max(box1.get("confidence", 0), box2.get("confidence", 0))
        }


_local_vlm_instance: Optional[LocalVLM] = None
_adaptive_cv_instance: Optional[AdaptiveCVDetector] = None


def get_local_vlm() -> LocalVLM:
    global _local_vlm_instance
    if _local_vlm_instance is None:
        _local_vlm_instance = LocalVLM()
    return _local_vlm_instance


def get_adaptive_cv() -> AdaptiveCVDetector:
    global _adaptive_cv_instance
    if _adaptive_cv_instance is None:
        _adaptive_cv_instance = AdaptiveCVDetector()
    return _adaptive_cv_instance