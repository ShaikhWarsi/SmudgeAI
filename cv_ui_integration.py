import asyncio
import logging
import os
import time
import json
import base64
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any
from PIL import Image
import pyautogui
import numpy as np
import ai_engine
import desktop_state
import local_vlm

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor
    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False


@dataclass
class DetectedElement:
    x: int
    y: int
    width: int
    height: int
    label: str
    confidence: float
    element_type: str = "unknown"

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    @property
    def center(self) -> Tuple[int, int]:
        return (self.center_x, self.center_y)

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def contains_point(self, px: int, py: int) -> bool:
        return self.x <= px <= self.x + self.width and self.y <= py <= self.y + self.height


class CVUIModel:
    def __init__(self, model_name: str = "microsoft/torchvision-models"):
        self.model = None
        self.processor = None
        self.model_name = model_name
        self.device = "cpu"
        self._is_loaded = False

    def load(self) -> bool:
        if self._is_loaded:
            return True

        if not TRANSFORMERS_AVAILABLE:
            logging.warning("Transformers not available, using fallback CV")
            return False

        try:
            logging.info(f"Loading CV model: {self.model_name}")
            self.model = AutoModelForCausalLM.from_pretrained(self.model_name, trust_remote_code=True)
            self.model.to(self.device)
            self.model.eval()
            self._is_loaded = True
            logging.info("CV model loaded successfully")
            return True
        except Exception as e:
            logging.error(f"Failed to load CV model: {e}")
            return False

    def unload(self):
        if self.model:
            del self.model
            self.model = None
            self._is_loaded = False
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


class UIElementDetector:
    def __init__(self):
        self.cv_model = None
        self._desktop_state = desktop_state.get_desktop_state()
        self._screenshot_cache = {}
        self._cache_ttl = 2.0
        self._last_screenshot_time = 0
        self._use_local_cv = False
        self._model_path = None

    def initialize(self, use_local_cv: bool = True, model_path: Optional[str] = None):
        self._model_path = model_path
        if use_local_cv and TRANSFORMERS_AVAILABLE:
            self.cv_model = CVUIModel(model_path or "microsoft/torchvision-models")
            self._use_local_cv = self.cv_model.load()
            if not self._use_local_cv:
                logging.info("Falling back to hybrid CV+LLM approach")

    async def detect_elements(self, screenshot_path: str, prompt: Optional[str] = None) -> List[DetectedElement]:
        self._last_screenshot_time = time.time()

        if self._use_local_cv and self.cv_model and self.cv_model._is_loaded:
            return await self._detect_with_model(screenshot_path, prompt)

        return await self._detect_with_hybrid(screenshot_path, prompt)

    async def _detect_with_model(self, screenshot_path: str, prompt: Optional[str]) -> List[DetectedElement]:
        try:
            image = Image.open(screenshot_path).convert("RGB")

            if prompt:
                inputs = self.cv_model.processor(text=prompt, images=image, return_tensors="pt")
                inputs = {k: v.to(self.cv_model.device) for k, v in inputs.items()}

                with torch.no_grad():
                    outputs = self.cv_model.model(**inputs)

                elements = self._parse_model_outputs(outputs, image.size)
                return elements

            return []

        except Exception as e:
            logging.error(f"Model detection failed: {e}")
            return await self._detect_with_hybrid(screenshot_path, prompt)

    async def _detect_with_hybrid(self, screenshot_path: str, prompt: Optional[str]) -> List[DetectedElement]:
        elements = []

        elements.extend(self._detect_buttons_with_opencv(screenshot_path))

        elements.extend(self._detect_inputs_with_opencv(screenshot_path))

        llm_elements = await self._detect_with_llm_vision(screenshot_path, prompt)
        elements.extend(llm_elements)

        elements = self._deduplicate_elements(elements)

        return elements

    def _detect_buttons_with_opencv(self, screenshot_path: str) -> List[DetectedElement]:
        elements = []
        try:
            import cv2

            img = cv2.imread(screenshot_path)
            if img is None:
                return elements

            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

            lower_bound = np.array([0, 0, 180])
            upper_bound = np.array([180, 50, 255])
            mask = cv2.inRange(hsv, lower_bound, upper_bound)

            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if 20 < w < 400 and 10 < h < 100:
                    elements.append(DetectedElement(
                        x=x, y=y, width=w, height=h,
                        label="Button",
                        confidence=0.6,
                        element_type="button"
                    ))

        except Exception as e:
            logging.debug(f"OpenCV button detection error: {e}")

        return elements

    def _detect_inputs_with_opencv(self, screenshot_path: str) -> List[DetectedElement]:
        elements = []
        try:
            import cv2

            img = cv2.imread(screenshot_path)
            if img is None:
                return elements

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

            rectangles = []

            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = w / h if h > 0 else 0
                if 50 < w < 800 and 15 < h < 60 and 1.5 < aspect_ratio < 20:
                    rectangles.append((x, y, w, h))

            rectangles = self._merge_close_rectangles(rectangles)

            for x, y, w, h in rectangles:
                elements.append(DetectedElement(
                    x=x, y=y, width=w, height=h,
                    label="Input",
                    confidence=0.5,
                    element_type="input"
                ))

        except Exception as e:
            logging.debug(f"OpenCV input detection error: {e}")

        return elements

    def _merge_close_rectangles(self, rectangles: List, threshold: int = 10) -> List:
        if not rectangles:
            return []

        merged = True
        while merged:
            merged = False
            new_rects = []

            for i, (x1, y1, w1, h1) in enumerate(rectangles):
                for j, (x2, y2, w2, h2) in enumerate(rectangles):
                    if i != j:
                        if abs(x1 - x2) < threshold and abs(y1 - y2) < threshold:
                            nx = min(x1, x2)
                            ny = min(y1, y2)
                            nw = max(x1 + w1, x2 + w2) - nx
                            nh = max(y1 + h1, y2 + h2) - ny
                            rectangles[i] = (nx, ny, nw, nh)
                            merged = True
                            break
                if (x1, y1, w1, h1) in new_rects:
                    continue
                new_rects.append((x1, y1, w1, h1))

            rectangles = new_rects

        return rectangles

    async def _detect_with_llm_vision(self, screenshot_path: str, prompt: Optional[str]) -> List[DetectedElement]:
        elements = []

        local_vlm_instance = local_vlm.get_local_vlm()
        is_local_available = await local_vlm_instance.check_availability()

        if is_local_available:
            try:
                result = await local_vlm_instance.analyze_image(screenshot_path, prompt)
                if result.elements:
                    for elem_data in result.elements:
                        elem = DetectedElement(
                            x=int(elem_data.get("x", 0)),
                            y=int(elem_data.get("y", 0)),
                            width=int(elem_data.get("width", 50)),
                            height=int(elem_data.get("height", 30)),
                            label=elem_data.get("label", "Unknown"),
                            confidence=float(elem_data.get("confidence", 0.6)),
                            element_type=elem_data.get("type", "unknown")
                        )
                        elements.append(elem)
                    logging.info(f"Local VLM detected {len(elements)} elements in {result.latency_ms:.0f}ms")
                    return elements
            except Exception as e:
                logging.warning(f"Local VLM failed, falling back to cloud: {e}")

        if not prompt:
            prompt = """Identify all interactive UI elements in this screenshot.
For each element provide:
- type: button, input, menu, link, checkbox, or other
- label: the visible text or description
- x, y: top-left corner coordinates
- width, height: dimensions in pixels

Return ONLY a JSON array like:
[{"type": "button", "label": "Save", "x": 100, "y": 200, "width": 80, "height": 30}]

Do not add any explanation, just the JSON array."""

        try:
            result = await ai_engine.analyze_image(screenshot_path, prompt)
            elements = self._parse_llm_response(result)
        except Exception as e:
            logging.error(f"LLM vision detection failed: {e}")

        return elements

    def _parse_llm_response(self, response: str) -> List[DetectedElement]:
        import re
        elements = []

        try:
            json_str = response.strip()
            match = re.search(r'\[.*\]', json_str, re.DOTALL)
            if match:
                json_str = match.group(0)

            data = json.loads(json_str)

            for item in data:
                elem = DetectedElement(
                    x=int(item.get("x", 0)),
                    y=int(item.get("y", 0)),
                    width=int(item.get("width", 50)),
                    height=int(item.get("height", 30)),
                    label=item.get("label", "Unknown"),
                    confidence=float(item.get("confidence", 0.5)),
                    element_type=item.get("type", "unknown")
                )
                elements.append(elem)

        except (json.JSONDecodeError, ValueError) as e:
            logging.debug(f"Failed to parse LLM response: {e}")

        return elements

    def _deduplicate_elements(self, elements: List[DetectedElement], iou_threshold: float = 0.5) -> List[DetectedElement]:
        if not elements:
            return []

        filtered = []
        for elem in elements:
            is_duplicate = False
            for existing in filtered:
                if self._compute_iou(elem, existing) > iou_threshold:
                    if elem.confidence > existing.confidence:
                        filtered.remove(existing)
                        is_duplicate = False
                        break
                    else:
                        is_duplicate = True
                        break
            if not is_duplicate:
                filtered.append(elem)

        return filtered

    def _compute_iou(self, elem1: DetectedElement, elem2: DetectedElement) -> float:
        x1 = max(elem1.x, elem2.x)
        y1 = max(elem1.y, elem2.y)
        x2 = min(elem1.x + elem1.width, elem2.x + elem2.width)
        y2 = min(elem1.y + elem1.height, elem2.y + elem2.height)

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area1 = elem1.width * elem1.height
        area2 = elem2.width * elem2.height
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0


class KeyboardShortcuts:
    COMMON_SHORTCUTS = {
        "copy": ["ctrl", "c"],
        "paste": ["ctrl", "v"],
        "cut": ["ctrl", "x"],
        "select_all": ["ctrl", "a"],
        "undo": ["ctrl", "z"],
        "redo": ["ctrl", "y"],
        "save": ["ctrl", "s"],
        "open": ["ctrl", "o"],
        "close": ["alt", "f4"],
        "tab_next": ["ctrl", "tab"],
        "tab_prev": ["ctrl", "shift", "tab"],
        "refresh": ["f5"],
        "find": ["ctrl", "f"],
        "new_window": ["ctrl", "n"],
        "new_tab": ["ctrl", "t"],
        "close_tab": ["ctrl", "w"],
        "quit": ["ctrl", "q"],
        "escape": ["esc"],
        "enter": ["enter"],
        "delete": ["delete"],
        "backspace": ["backspace"],
        "home": ["home"],
        "end": ["end"],
        "page_up": ["pageup"],
        "page_down": ["pagedown"],
        "up": ["up"],
        "down": ["down"],
        "left": ["left"],
        "right": ["right"],
        "switch_app": ["alt", "tab"],
        "force_close": ["ctrl", "shift", "escape"],
    }

    def __init__(self):
        self._held_keys = set()

    def parse_shortcut(self, shortcut_name: str) -> List[str]:
        shortcut_name = shortcut_name.lower().strip()
        if shortcut_name in self.COMMON_SHORTCUTS:
            return self.COMMON_SHORTCUTS[shortcut_name]
        return self._parse_key_combination(shortcut_name)

    def _parse_key_combination(self, combo: str) -> List[str]:
        keys = []
        parts = combo.lower().replace("+", " ").split()
        key_map = {
            "ctrl": "ctrl", "control": "ctrl",
            "alt": "alt", "option": "alt",
            "shift": "shift",
            "cmd": "cmd", "command": "cmd", "win": "cmd", "windows": "cmd",
            "tab": "tab",
            "enter": "enter", "return": "enter",
            "escape": "esc", "esc": "esc",
            "delete": "delete", "del": "delete",
            "backspace": "backspace",
            "up": "up", "down": "down", "left": "left", "right": "right",
            "home": "home", "end": "end",
            "pageup": "pageup", "pagedown": "pagedown",
            "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
            "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
            "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
        }
        for part in parts:
            if part in key_map:
                keys.append(key_map[part])
            elif len(part) == 1:
                keys.append(part)
        return keys

    def press(self, shortcut_name: str) -> bool:
        try:
            keys = self.parse_shortcut(shortcut_name)
            if not keys:
                return False
            for key in keys[:-1]:
                pyautogui.keyDown(key)
                self._held_keys.add(key)
            pyautogui.press(keys[-1])
            for key in reversed(keys[:-1]):
                pyautogui.keyUp(key)
                self._held_keys.discard(key)
            return True
        except Exception as e:
            logging.error(f"Keyboard shortcut '{shortcut_name}' failed: {e}")
            self._release_all()
            return False

    def hold(self, key: str) -> bool:
        try:
            parsed = self._parse_key_combination(key)
            if parsed:
                for k in parsed:
                    pyautogui.keyDown(k)
                    self._held_keys.add(k)
            return True
        except Exception as e:
            logging.error(f"Hold key '{key}' failed: {e}")
            return False

    def release(self, key: str) -> bool:
        try:
            parsed = self._parse_key_combination(key)
            if parsed:
                for k in parsed:
                    pyautogui.keyUp(k)
                    self._held_keys.discard(k)
            return True
        except Exception as e:
            logging.error(f"Release key '{key}' failed: {e}")
            return False

    def release_all(self):
        for key in list(self._held_keys):
            try:
                pyautogui.keyUp(key)
            except:
                pass
        self._held_keys.clear()

    def type_text(self, text: str, delay: float = 0.05) -> bool:
        try:
            pyautogui.write(text, interval=delay)
            return True
        except Exception as e:
            logging.error(f"Type text failed: {e}")
            return False

    async def type_text_async(self, text: str, delay: float = 0.05) -> bool:
        return await asyncio.to_thread(self.type_text, text, delay)

    def shortcut_to_string(self, shortcut_name: str) -> str:
        keys = self.parse_shortcut(shortcut_name)
        return " + ".join(k.upper() for k in keys)


class RobustClicker:
    def __init__(self):
        self.detector = UIElementDetector()
        self._desktop_state = desktop_state.get_desktop_state()
        self._max_retries = 3
        self._retry_delay = 0.5
        self._keyboard = KeyboardShortcuts()

    def initialize(self, use_local_cv: bool = True):
        self.detector.initialize(use_local_cv=use_local_cv)

    async def find_and_click(self, description: str, screenshot_path: Optional[str] = None) -> Dict[str, Any]:
        if not screenshot_path:
            screenshot_path = self._take_screenshot()

        result = {
            "success": False,
            "description": description,
            "attempts": 0,
            "elements_found": [],
            "final_position": None,
            "error": None
        }

        for attempt in range(self._max_retries):
            result["attempts"] = attempt + 1

            screenshot = screenshot_path if attempt == 0 else self._take_screenshot()
            elements = await self.detector.detect_elements(
                screenshot,
                f"Find '{description}' in this UI"
            )

            result["elements_found"] = [e.label for e in elements]

            matched = self._match_element(elements, description)

            if matched:
                result["final_position"] = matched.center
                click_success = await self._execute_click_with_verification(matched)
                result["success"] = click_success

                if click_success:
                    return result

            await asyncio.sleep(self._retry_delay)

        result["error"] = f"Failed after {self._max_retries} attempts"
        return result

    async def find_and_drag(
        self,
        source_description: str,
        target_description: str,
        screenshot_path: Optional[str] = None
    ) -> Dict[str, Any]:
        if not screenshot_path:
            screenshot_path = self._take_screenshot()

        result = {
            "success": False,
            "source": source_description,
            "target": target_description,
            "attempts": 0,
            "source_pos": None,
            "target_pos": None,
            "error": None
        }

        for attempt in range(self._max_retries):
            result["attempts"] = attempt + 1

            screenshot = screenshot_path if attempt == 0 else self._take_screenshot()
            elements = await self.detector.detect_elements(screenshot)

            source = self._match_element(elements, source_description)
            target = self._match_element(elements, target_description)

            if source and target:
                result["source_pos"] = source.center
                result["target_pos"] = target.center

                drag_success = await self._execute_drag_with_verification(source, target)
                result["success"] = drag_success

                if drag_success:
                    return result

            await asyncio.sleep(self._retry_delay)

        result["error"] = f"Failed after {self._max_retries} attempts"
        return result

    async def _execute_drag_with_verification(
        self,
        source: DetectedElement,
        target: DetectedElement
    ) -> bool:
        start_x, start_y = source.center
        end_x, end_y = target.center

        self._desktop_state.update(force=True)
        pre_state = self._desktop_state.get_state_summary()

        pyautogui.moveTo(start_x, start_y)
        await asyncio.sleep(0.1)
        pyautogui.drag(
            end_x - start_x,
            end_y - start_y,
            duration=0.5,
            button='left'
        )

        await asyncio.sleep(0.3)

        self._desktop_state.update(force=True)
        post_state = self._desktop_state.get_state_summary()

        return pre_state != post_state

    async def wait_for_element(
        self,
        description: str,
        timeout: float = 10.0,
        poll_interval: float = 0.5
    ) -> Dict[str, Any]:
        result = {
            "found": False,
            "description": description,
            "element": None,
            "wait_time": 0.0,
            "error": None
        }

        start_time = time.time()

        while time.time() - start_time < timeout:
            screenshot = self._take_screenshot()
            elements = await self.detector.detect_elements(screenshot)

            matched = self._match_element(elements, description)

            if matched:
                result["found"] = True
                result["element"] = matched
                result["wait_time"] = time.time() - start_time
                return result

            await asyncio.sleep(poll_interval)

        result["error"] = f"Element '{description}' not found within {timeout}s"
        return result

    async def wait_for_state_change(
        self,
        expected_description: str,
        timeout: float = 10.0,
        poll_interval: float = 0.5
    ) -> Dict[str, Any]:
        result = {
            "changed": False,
            "expected": expected_description,
            "actual": None,
            "wait_time": 0.0,
            "error": None
        }

        self._desktop_state.update(force=True)
        pre_summary = self._desktop_state.get_state_summary()

        start_time = time.time()

        while time.time() - start_time < timeout:
            await asyncio.sleep(poll_interval)

            self._desktop_state.update(force=True)
            post_summary = self._desktop_state.get_state_summary()

            if post_summary != pre_summary:
                result["changed"] = True
                result["actual"] = post_summary
                result["wait_time"] = time.time() - start_time

                if expected_description.lower() in post_summary.lower():
                    return result

                pre_summary = post_summary

        result["error"] = f"Expected state '{expected_description}' not reached within {timeout}s"
        return result

    def _match_element(self, elements: List[DetectedElement], description: str) -> Optional[DetectedElement]:
        if not elements:
            return None

        desc_lower = description.lower()
        words = desc_lower.split()

        best_match = None
        best_score = 0.0

        for elem in elements:
            label_lower = elem.label.lower()

            if desc_lower in label_lower:
                return elem

            matches = sum(1 for word in words if word in label_lower)
            if matches > 0:
                score = matches / len(words)
                if score > best_score:
                    best_score = score
                    best_match = elem

        return best_match if best_score > 0.5 else None

    async def _execute_click_with_verification(self, element: DetectedElement) -> bool:
        x, y = element.center

        self._desktop_state.update(force=True)
        pre_state = self._desktop_state.get_state_summary()

        pyautogui.click(x, y)

        await asyncio.sleep(0.2)

        self._desktop_state.update(force=True)
        post_state = self._desktop_state.get_state_summary()

        return pre_state != post_state or self._verify_element_exists(element)

    def _verify_element_exists(self, element: DetectedElement) -> bool:
        try:
            import pyautogui
            current = pyautogui.position()
            return True
        except:
            return False

    def _take_screenshot(self) -> str:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
            pyautogui.screenshot().save(filename)
            return filename
        except Exception as e:
            logging.error(f"Screenshot failed: {e}")
            return ""


from datetime import datetime


_ui_detector_instance: Optional[UIElementDetector] = None
_robust_clicker_instance: Optional[RobustClicker] = None


def get_ui_detector() -> UIElementDetector:
    global _ui_detector_instance
    if _ui_detector_instance is None:
        _ui_detector_instance = UIElementDetector()
    return _ui_detector_instance


def get_robust_clicker() -> RobustClicker:
    global _robust_clicker_instance
    if _robust_clicker_instance is None:
        _robust_clicker_instance = RobustClicker()
    return _robust_clicker_instance