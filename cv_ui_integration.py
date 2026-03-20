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


class ScreenHelper:
    _instance = None
    _dpi_scale = None
    _monitor_info = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._init_dpi()
        self._init_monitors()

    def _init_dpi(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            self._dpi_scale = user32.GetDpiForSystem() / 96.0
            logging.info(f"DPI scale detected: {self._dpi_scale:.2f}")
        except Exception as e:
            logging.warning(f"Could not detect DPI scale: {e}")
            self._dpi_scale = 1.0

    def _init_monitors(self):
        self._monitor_info = {}
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            def callback(hMonitor, hdcMonitor, lParam, dwData):
                r = ctypes.Structure
                class RECT(ctypes.Structure):
                    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                    is_primary = bool(mi.dwFlags & 1)
                    self._monitor_info[hMonitor] = {
                        "rect": (mi.rcMonitor.left, mi.rcMonitor.top,
                                mi.rcMonitor.right - mi.rcMonitor.left,
                                mi.rcMonitor.bottom - mi.rcMonitor.top),
                        "work": (mi.rcWork.left, mi.rcWork.top,
                                mi.rcWork.right - mi.rcWork.left,
                                mi.rcWork.bottom - mi.rcWork.top),
                        "primary": is_primary
                    }
                return 1

            MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR,
                                                wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
        except Exception as e:
            logging.warning(f"Could not enumerate monitors: {e}")

    def get_dpi_scale(self) -> float:
        return self._dpi_scale or 1.0

    def get_primary_monitor_offset(self) -> Tuple[int, int]:
        for hmon, info in self._monitor_info.items():
            if info.get("primary"):
                return (info["rect"][0], info["rect"][1])
        return (0, 0)

    def adjust_coords_for_monitor(self, x: int, y: int, window_rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
        win_left, win_top, win_width, win_height = window_rect
        primary_offset_x, primary_offset_y = self.get_primary_monitor_offset()

        if win_left < 0 or win_top < 0:
            x = x + win_left
            y = y + win_top

        if primary_offset_x != 0 or primary_offset_y != 0:
            x = x - primary_offset_x
            y = y - primary_offset_y

        return (int(x), int(y))

    def scale_screenshot_for_dpi(self, screenshot_path: str) -> str:
        scale = self.get_dpi_scale()
        if scale == 1.0:
            return screenshot_path

        try:
            img = Image.open(screenshot_path)
            if img.width != int(img.width * scale) or img.height != int(img.height * scale):
                new_size = (int(img.width * scale), int(img.height * scale))
                img_scaled = img.resize(new_size, Image.LANCZOS)
                img_scaled.save(screenshot_path)
                logging.info(f"Screenshot scaled from {img.width}x{img.height} to {new_size[0]}x{new_size[1]}")
        except Exception as e:
            logging.warning(f"Could not scale screenshot for DPI: {e}")

        return screenshot_path

    def get_monitor_containing_point(self, x: int, y: int) -> Optional[Dict]:
        for hmon, info in self._monitor_info.items():
            rect = info["rect"]
            if rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]:
                return info
        return None

    def get_system_locale(self) -> str:
        try:
            import locale
            return locale.getdefaultlocale()[0] or "en_US"
        except:
            return "en_US"

_screen_helper = ScreenHelper.get_instance()


def get_screen_helper():
    return _screen_helper


_LOCALIZED_UI_TERMS = {
    "en_US": {},
    "de_DE": {
        "save": ["Speichern", "Speichern"],
        "open": ["Öffnen", "Datei öffnen"],
        "close": ["Schließen"],
        "cancel": ["Abbrechen"],
        "ok": ["OK", "Bestätigen"],
        "yes": ["Ja"],
        "no": ["Nein"],
        "delete": ["Löschen", "Entfernen"],
        "edit": ["Bearbeiten"],
        "copy": ["Kopieren"],
        "paste": ["Einfügen"],
        "cut": ["Ausschneiden"],
        "new": ["Neu", "Neue"],
        "open": ["Öffnen"],
        "settings": ["Einstellungen", "Optionen"],
        "search": ["Suchen", "Suche"],
        "help": ["Hilfe"],
        "back": ["Zurück"],
        "next": ["Weiter", "Weiter"],
        "finish": ["Fertig", "Beenden"],
        "apply": ["Übernehmen", "Anwenden"],
        "reset": ["Zurücksetzen"],
        "refresh": ["Aktualisieren", "Neu laden"],
        "logout": ["Abmelden", "Ausloggen"],
        "login": ["Anmelden", "Einloggen"],
    },
    "fr_FR": {
        "save": ["Enregistrer", "Sauvegarder"],
        "open": ["Ouvrir"],
        "close": ["Fermer"],
        "cancel": ["Annuler"],
        "ok": ["OK", "Confirmer"],
        "yes": ["Oui"],
        "no": ["Non"],
        "delete": ["Supprimer"],
        "edit": ["Modifier"],
        "copy": ["Copier"],
        "paste": ["Coller"],
        "cut": ["Couper"],
        "new": ["Nouveau", "Nouvelle"],
        "settings": ["Paramètres", "Options"],
        "search": ["Rechercher", "Recherche"],
        "help": ["Aide"],
        "back": ["Retour"],
        "next": ["Suivant", "Suite"],
        "finish": ["Terminer", "Finir"],
        "apply": ["Appliquer"],
        "reset": ["Réinitialiser"],
        "refresh": ["Actualiser", "Recharger"],
    },
    "es_ES": {
        "save": ["Guardar"],
        "open": ["Abrir"],
        "close": ["Cerrar"],
        "cancel": ["Cancelar", "Cancelar"],
        "ok": ["Aceptar", "OK"],
        "yes": ["Sí"],
        "no": ["No"],
        "delete": ["Eliminar", "Borrar"],
        "edit": ["Editar"],
        "copy": ["Copiar"],
        "paste": ["Pegar"],
        "cut": ["Cortar"],
        "new": ["Nuevo", "Nueva"],
        "settings": ["Configuración", "Opciones"],
        "search": ["Buscar", "Búsqueda"],
        "help": ["Ayuda"],
        "back": ["Atrás", "Volver"],
        "next": ["Siguiente"],
        "finish": ["Finalizar", "Terminar"],
        "apply": ["Aplicar"],
        "reset": ["Restablecer", "Reiniciar"],
        "refresh": ["Actualizar", "Recargar"],
    },
    "ja_JP": {
        "save": ["保存"],
        "open": ["開く"],
        "close": ["閉じる"],
        "cancel": ["キャンセル"],
        "ok": ["OK", "了解"],
        "yes": ["はい"],
        "no": ["いいえ"],
        "delete": ["削除", "消去"],
        "edit": ["編集"],
        "copy": ["コピー"],
        "paste": ["貼り付け"],
        "cut": ["切り取り"],
        "new": ["新規"],
        "settings": ["設定"],
        "search": ["検索"],
        "help": ["ヘルプ"],
        "back": ["戻る", "後退"],
        "next": ["次へ"],
        "finish": ["完了", "終了"],
        "apply": ["適用"],
        "reset": ["リセット", "初期化"],
        "refresh": ["更新", "再読み込み"],
    },
    "zh_CN": {
        "save": ["保存", "存储"],
        "open": ["打开", "开启"],
        "close": ["关闭"],
        "cancel": ["取消"],
        "ok": ["确定", "好"],
        "yes": ["是", "是"],
        "no": ["否", "不"],
        "delete": ["删除"],
        "edit": ["编辑"],
        "copy": ["复制"],
        "paste": ["粘贴"],
        "cut": ["剪切"],
        "new": ["新建", "新建"],
        "settings": ["设置"],
        "search": ["搜索"],
        "help": ["帮助"],
        "back": ["返回", "后退"],
        "next": ["下一步", "继续"],
        "finish": ["完成", "结束"],
        "apply": ["应用", "Apply"],
        "reset": ["重置", ["重置"]],
        "refresh": ["刷新", "重新加载"],
    },
    "ko_KR": {
        "save": ["저장"],
        "open": ["열기"],
        "close": ["닫기"],
        "cancel": ["취소"],
        "ok": ["확인", "OK"],
        "yes": ["예"],
        "no": ["아니오"],
        "delete": ["삭제"],
        "edit": ["편집"],
        "copy": ["복사"],
        "paste": ["붙여넣기"],
        "cut": ["잘라내기"],
        "new": ["새로 만들기", "새로운"],
        "settings": ["설정"],
        "search": ["검색"],
        "help": ["도움말"],
        "back": ["뒤로", "이전"],
        "next": ["다음"],
        "finish": ["완료", "마침"],
        "apply": ["적용"],
        "reset": ["초기화", "재설정"],
        "refresh": ["새로고침", "새整理"],
    },
}


def get_localized_terms(term: str) -> List[str]:
    locale = _screen_helper.get_system_locale()
    localized = []
    for locale_key, translations in _LOCALIZED_UI_TERMS.items():
        if term.lower() in translations:
            localized.extend(translations[term.lower()])
    if term.lower() not in localized:
        localized.append(term.lower())
    return list(set(localized))


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

        if elements:
            elements = self._verify_llm_coordinates(elements, screenshot_path)
            logging.info(f"LLM vision returned {len(elements)} verified elements")

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

    def _verify_llm_coordinates(self, elements: List[DetectedElement], screenshot_path: str) -> List[DetectedElement]:
        verified = []
        for elem in elements:
            if self._is_coord_in_any_element_bounds(elem):
                elem.confidence = min(elem.confidence + 0.3, 1.0)
                verified.append(elem)
            else:
                logging.warning(f"LLM hallucinated coords for '{elem.label}': ({elem.x}, {elem.y}) - verifying against actual elements")
                elem.confidence = max(elem.confidence - 0.4, 0.0)
                verified.append(elem)
        return [e for e in verified if e.confidence > 0.3]

    def _is_coord_in_any_element_bounds(self, llm_elem: DetectedElement) -> bool:
        try:
            import desktop_state
            ds = desktop_state.get_desktop_state()
            ds.update(force=True)
            if not ds.active_window:
                return False
            all_elems = ds._flatten_elements(ds.active_window.elements)
            for ui_elem in all_elems:
                if (ui_elem.x <= llm_elem.x <= ui_elem.x + ui_elem.width and
                    ui_elem.y <= llm_elem.y <= ui_elem.y + ui_elem.height):
                    return True
            return False
        except Exception:
            return False

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
        self._circuit_breaker_max_iterations = 10
        self._circuit_breaker_iterations = 0
        self._total_time_budget = 30.0
        self._start_time = None

    def initialize(self, use_local_cv: bool = True):
        self.detector.initialize(use_local_cv=use_local_cv)

    def _check_circuit_breaker(self) -> bool:
        if self._circuit_breaker_iterations >= self._circuit_breaker_max_iterations:
            logging.warning(f"Circuit breaker triggered: exceeded {self._circuit_breaker_max_iterations} iterations")
            return False
        if self._start_time and (time.time() - self._start_time) > self._total_time_budget:
            logging.warning(f"Circuit breaker triggered: exceeded {self._total_time_budget}s time budget")
            return False
        return True

    def _get_retry_delay(self, attempt: int) -> float:
        base_delay = self._retry_delay
        exponential_delay = base_delay * (2 ** attempt)
        max_delay = 5.0
        jitter = 0.1 * base_delay * (hash(str(attempt)) % 10)
        return min(exponential_delay + jitter, max_delay)

    async def find_and_click(self, description: str, screenshot_path: Optional[str] = None) -> Dict[str, Any]:
        if self._start_time is None:
            self._start_time = time.time()
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
            if not self._check_circuit_breaker():
                result["error"] = "Circuit breaker triggered - too many attempts"
                return result

            result["attempts"] = attempt + 1
            self._circuit_breaker_iterations += 1

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
                    self._reset_circuit_breaker()
                    return result

            retry_delay = self._get_retry_delay(attempt)
            logging.info(f"Attempt {attempt + 1} failed for '{description}', waiting {retry_delay:.2f}s before retry")
            await asyncio.sleep(retry_delay)

        result["error"] = f"Failed after {self._max_retries} attempts - circuit breaker iterations: {self._circuit_breaker_iterations}"
        return result

    def _reset_circuit_breaker(self):
        self._circuit_breaker_iterations = 0
        self._start_time = None

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
            if not self._check_circuit_breaker():
                result["error"] = "Circuit breaker triggered - too many attempts"
                return result

            result["attempts"] = attempt + 1
            self._circuit_breaker_iterations += 1

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
                    self._reset_circuit_breaker()
                    return result

            retry_delay = self._get_retry_delay(attempt)
            await asyncio.sleep(retry_delay)

        result["error"] = f"Failed after {self._max_retries} attempts - circuit breaker iterations: {self._circuit_breaker_iterations}"
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

        search_terms = set(words)
        for word in words:
            localized = get_localized_terms(word)
            search_terms.update(localized)

        best_match = None
        best_score = 0.0

        for elem in elements:
            label_lower = elem.label.lower()

            if desc_lower in label_lower:
                return elem

            elem_words = set(label_lower.replace("_", " ").replace("-", " ").split())
            matches = len(search_terms & elem_words)
            if matches > 0:
                score = matches / max(len(search_terms), 1)
                if score > best_score:
                    best_score = score
                    best_match = elem

        return best_match if best_score > 0.3 else None

    async def _execute_click_with_verification(self, element: DetectedElement) -> bool:
        x, y = element.center

        self._desktop_state.update(force=True)

        window_rect = None
        if self._desktop_state.active_window:
            window_rect = self._desktop_state.active_window.rect

        screen_helper = get_screen_helper()
        if window_rect:
            x, y = screen_helper.adjust_coords_for_monitor(x, y, window_rect)

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
            screen_helper = get_screen_helper()
            screen_helper.scale_screenshot_for_dpi(filename)
            return filename
        except Exception as e:
            logging.error(f"Screenshot failed: {e}")
            return ""


from datetime import datetime
from collections import OrderedDict
from functools import lru_cache


_ui_detector_instance: Optional[UIElementDetector] = None
_robust_clicker_instance: Optional[RobustClicker] = None
_desktop_actions_instance: Optional['DesktopActions'] = None


class DesktopActions:
    def __init__(self):
        self._desktop_state = desktop_state.get_desktop_state()
        self._keyboard = KeyboardShortcuts()
        self._max_retries = 3
        self._retry_delay = 0.3
        self._element_cache = OrderedDict()
        self._cache_max_size = 100
        self._click_verification = True

    def _get_cached_element(self, key: str) -> Optional[Tuple[int, int]]:
        if key in self._element_cache:
            self._element_cache.move_to_end(key)
            return self._element_cache[key]
        return None

    def _cache_element(self, key: str, position: Tuple[int, int]):
        if key in self._element_cache:
            self._element_cache.move_to_end(key)
        else:
            self._element_cache[key] = position
            if len(self._element_cache) > self._cache_max_size:
                self._element_cache.popitem(last=False)

    def _make_cache_key(self, description: str, window_title: str = None) -> str:
        active = self._desktop_state.active_window
        win_title = window_title or (active.title if active else "")
        return f"{win_title.lower()}:{description.lower()}"

    def _get_state_snapshot(self) -> Dict[str, Any]:
        self._desktop_state.update(force=True)
        snapshot = {
            "active_window": self._desktop_state.active_window.title if self._desktop_state.active_window else None,
            "windows": list(self._desktop_state.windows.keys()),
            "buttons": [b.title for b in self._desktop_state.get_buttons()[:10]],
            "inputs": [i.title for i in self._desktop_state.get_inputs()[:10]],
            "timestamp": time.time()
        }
        return snapshot

    def _verify_state_change(self, before: Dict, after: Dict, expected_change: str = None) -> bool:
        if before["active_window"] != after["active_window"]:
            return True
        if set(before["windows"]) != set(after["windows"]):
            return True
        if before["buttons"] != after["buttons"]:
            return True
        if before["inputs"] != after["inputs"]:
            return True
        return False

    def _find_element_uia(self, description: str) -> Optional[Tuple[int, int]]:
        self._desktop_state.update(force=True)
        cache_key = self._make_cache_key(description)

        cached = self._get_cached_element(cache_key)
        if cached:
            x, y = cached
            if 0 < x < 5000 and 0 < y < 3000:
                return cached

        elem = self._desktop_state.find_element(description, fuzzy=True)
        if elem and elem.center_x > 0 and elem.center_y > 0:
            self._cache_element(cache_key, elem.center)
            return elem.center

        return None

    def _find_element_cv(self, screenshot_path: str, description: str) -> Optional[Tuple[int, int, float]]:
        try:
            import cv2
            img = cv2.imread(screenshot_path)
            if img is None:
                return None

            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            best_match = None
            best_score = 0.0

            desc_lower = description.lower()
            desc_words = [w for w in desc_lower.split() if len(w) > 2]

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if 15 < w < 800 and 10 < h < 200:
                    roi = gray[max(0, y-2):y+h+2, max(0, x-2):x+w+2]
                    if roi.size == 0:
                        continue

                    brightness = roi.mean()
                    aspect = w / h if h > 0 else 0

                    is_button_like = (brightness > 180 or brightness < 80) and 1.5 < aspect < 20
                    is_input_like = 50 < w < 1000 and 15 < h < 60 and 1.5 < aspect < 25

                    if is_button_like or is_input_like:
                        confidence = 0.5
                        if desc_words:
                            confidence = 0.6
                        if is_button_like:
                            confidence += 0.2

                        if confidence > best_score:
                            best_score = confidence
                            best_match = (x + w//2, y + h//2, confidence)

            return best_match

        except Exception as e:
            logging.debug(f"CV element finding failed: {e}")
            return None

    async def click(self, description: str, verify: bool = None) -> Dict[str, Any]:
        verify = verify if verify is not None else self._click_verification
        result = {
            "success": False,
            "method": None,
            "position": None,
            "description": description,
            "verified": False,
            "error": None
        }

        for attempt in range(self._max_retries):
            before_state = self._get_state_snapshot() if verify else None

            position = self._find_element_uia(description)
            if position:
                x, y = position
                screen_helper = get_screen_helper()
                window_rect = self._desktop_state.active_window.rect if self._desktop_state.active_window else None
                if window_rect:
                    x, y = screen_helper.adjust_coords_for_monitor(x, y, window_rect)
                pyautogui.click(x, y)
                result["method"] = "uia"
                result["position"] = (x, y)
                result["success"] = True

                if verify and before_state:
                    await asyncio.sleep(0.15)
                    after_state = self._get_state_snapshot()
                    result["verified"] = self._verify_state_change(before_state, after_state)

                return result

            screenshot = self._take_screenshot()
            cv_result = self._find_element_cv(screenshot, description)
            if cv_result:
                x, y, conf = cv_result
                screen_helper = get_screen_helper()
                window_rect = self._desktop_state.active_window.rect if self._desktop_state.active_window else None
                if window_rect:
                    x, y = screen_helper.adjust_coords_for_monitor(x, y, window_rect)
                pyautogui.click(x, y)
                result["method"] = "cv"
                result["position"] = (x, y)
                result["success"] = True

                if verify and before_state:
                    await asyncio.sleep(0.15)
                    after_state = self._get_state_snapshot()
                    result["verified"] = self._verify_state_change(before_state, after_state)

                return result

            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._retry_delay)

        result["error"] = f"Element '{description}' not found after {self._max_retries} attempts"
        return result

    async def double_click(self, description: str) -> Dict[str, Any]:
        result = await self.click(description)
        if result["success"]:
            pyautogui.doubleClick()
            result["action"] = "double_click"
        return result

    async def right_click(self, description: str) -> Dict[str, Any]:
        result = await self.click(description)
        if result["success"]:
            x, y = result["position"]
            pyautogui.rightClick(x, y)
            result["action"] = "right_click"
        return result

    async def hover(self, description: str) -> Dict[str, Any]:
        result = {
            "success": False,
            "position": None,
            "description": description
        }

        position = self._find_element_uia(description)
        if position:
            x, y = position
            pyautogui.moveTo(x, y)
            result["success"] = True
            result["position"] = position
            return result

        screenshot = self._take_screenshot()
        cv_result = self._find_element_cv(screenshot, description)
        if cv_result:
            x, y, _ = cv_result
            pyautogui.moveTo(x, y)
            result["success"] = True
            result["position"] = (x, y)

        return result

    async def drag(self, source_desc: str, target_desc: str) -> Dict[str, Any]:
        result = {
            "success": False,
            "source": None,
            "target": None,
            "error": None
        }

        source_pos = self._find_element_uia(source_desc)
        if not source_pos:
            screenshot = self._take_screenshot()
            cv_result = self._find_element_cv(screenshot, source_desc)
            if cv_result:
                source_pos = (cv_result[0], cv_result[1])

        target_pos = self._find_element_uia(target_desc)
        if not target_pos:
            screenshot = self._take_screenshot()
            cv_result = self._find_element_cv(screenshot, target_desc)
            if cv_result:
                target_pos = (cv_result[0], cv_result[1])

        if not source_pos or not target_pos:
            result["error"] = f"Could not find source or target element"
            return result

        result["source"] = source_pos
        result["target"] = target_pos

        before_state = self._get_state_snapshot()

        sx, sy = source_pos
        tx, ty = target_pos

        pyautogui.moveTo(sx, sy)
        await asyncio.sleep(0.1)
        pyautogui.drag(tx - sx, ty - sy, duration=0.5)

        await asyncio.sleep(0.2)
        after_state = self._get_state_snapshot()

        result["success"] = self._verify_state_change(before_state, after_state)
        return result

    async def scroll(self, direction: str = "down", amount: int = 3) -> Dict[str, Any]:
        direction = direction.lower()
        if direction not in ("up", "down", "left", "right"):
            return {"success": False, "error": "Direction must be up, down, left, or right"}

        try:
            if direction == "down":
                pyautogui.scroll(-amount * 100)
            elif direction == "up":
                pyautogui.scroll(amount * 100)
            elif direction == "left":
                pyautogui.hscroll(-amount * 100)
            elif direction == "right":
                pyautogui.hscroll(amount * 100)

            return {"success": True, "direction": direction, "amount": amount}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def type_text(self, text: str, delay: float = 0.05) -> Dict[str, Any]:
        try:
            pyautogui.write(text, interval=delay)
            return {"success": True, "text": text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def press_key(self, key: str) -> Dict[str, Any]:
        try:
            self._keyboard.press(key)
            return {"success": True, "key": key}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def select_all(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+a")

    async def copy(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+c")

    async def paste(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+v")

    async def undo(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+z")

    async def save(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+s")

    async def close_tab(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+w")

    async def new_tab(self) -> Dict[str, Any]:
        return await self.press_key("ctrl+t")

    async def switch_app(self) -> Dict[str, Any]:
        return await self.press_key("alt+tab")

    async def execute_verified(self, action_name: str, description: str, **kwargs) -> Dict[str, Any]:
        before_state = self._get_state_snapshot()

        action_map = {
            "click": self.click,
            "double_click": self.double_click,
            "right_click": self.right_click,
            "hover": self.hover,
            "drag": self.drag,
            "scroll": self.scroll,
            "type_text": self.type_text,
            "press_key": self.press_key,
        }

        if action_name not in action_map:
            return {"success": False, "error": f"Unknown action: {action_name}"}

        result = await action_map[action_name](description, **kwargs)

        await asyncio.sleep(0.15)
        after_state = self._get_state_snapshot()

        result["state_changed"] = self._verify_state_change(before_state, after_state)
        result["before_state"] = before_state["active_window"]
        result["after_state"] = after_state["active_window"]

        return result

    def _take_screenshot(self) -> str:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshot_{timestamp}.png"
            pyautogui.screenshot().save(filename)
            screen_helper = get_screen_helper()
            screen_helper.scale_screenshot_for_dpi(filename)
            return filename
        except Exception as e:
            logging.error(f"Screenshot failed: {e}")
            return ""

    def clear_cache(self):
        self._element_cache.clear()


def get_desktop_actions() -> DesktopActions:
    global _desktop_actions_instance
    if _desktop_actions_instance is None:
        _desktop_actions_instance = DesktopActions()
    return _desktop_actions_instance


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