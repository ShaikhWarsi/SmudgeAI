import pygetwindow as gw
import pyautogui
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
import threading

_pywinauto_initialized = False

def _ensure_pywinauto_com_init():
    global _pywinauto_initialized
    if _pywinauto_initialized:
        return True
    try:
        import pythoncom
        pythoncom.CoInitializeEx(None, pythoncom.COINIT_MULTITHREADED)
        _pywinauto_initialized = True
        logging.info("pywinauto COM initialized (MTA mode)")
        return True
    except Exception as e:
        logging.error(f"Failed to initialize pywinauto COM: {e}")
        return False


class ElementType(Enum):
    WINDOW = "window"
    BUTTON = "button"
    EDIT = "edit"
    MENU = "menu"
    MENU_ITEM = "menu_item"
    TAB = "tab"
    CHECKBOX = "checkbox"
    RADIO_BUTTON = "radio_button"
    COMBOBOX = "combobox"
    LIST = "list"
    LIST_ITEM = "list_item"
    TEXT = "text"
    UNKNOWN = "unknown"


@dataclass
class UIElement:
    title: str
    element_type: ElementType
    rect: tuple
    automation_id: Optional[str] = None
    class_name: Optional[str] = None
    is_visible: bool = True
    is_enabled: bool = True
    children: List['UIElement'] = field(default_factory=list)
    parent: Optional['UIElement'] = None

    @property
    def x(self) -> int:
        return self.rect[0]

    @property
    def y(self) -> int:
        return self.rect[1]

    @property
    def width(self) -> int:
        return self.rect[2]

    @property
    def height(self) -> int:
        return self.rect[3]

    @property
    def center(self) -> tuple:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2


@dataclass
class WindowInfo:
    title: str
    process_name: str
    rect: tuple
    is_active: bool
    elements: List[UIElement] = field(default_factory=list)
    app_id: Optional[str] = None

    @property
    def x(self) -> int:
        return self.rect[0]

    @property
    def y(self) -> int:
        return self.rect[1]

    @property
    def width(self) -> int:
        return self.rect[2]

    @property
    def height(self) -> int:
        return self.rect[3]


class DesktopState:
    def __init__(self):
        self.windows: Dict[str, WindowInfo] = {}
        self.active_window: Optional[WindowInfo] = None
        self.active_app: Optional[str] = None
        self.previous_state: Optional[Dict] = None
        self._lock = threading.RLock()
        self._listeners: List[callable] = []
        self._last_update = 0
        self._update_interval = 0.1
        self._is_monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None

    def add_listener(self, callback: callable):
        self._listeners.append(callback)

    def remove_listener(self, callback: callable):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify_listeners(self, event_type: str, data: Any):
        for listener in self._listeners:
            try:
                listener(event_type, data)
            except Exception as e:
                logging.error(f"DesktopState listener error: {e}")

    def _map_control_type(self, control_type: str) -> ElementType:
        type_mapping = {
            'button': ElementType.BUTTON,
            'edit': ElementType.EDIT,
            'menu': ElementType.MENU,
            'menuitem': ElementType.MENU_ITEM,
            'tab': ElementType.TAB,
            'checkbox': ElementType.CHECKBOX,
            'radiobutton': ElementType.RADIO_BUTTON,
            'combobox': ElementType.COMBOBOX,
            'list': ElementType.LIST,
            'listitem': ElementType.LIST_ITEM,
            'text': ElementType.TEXT,
            'pane': ElementType.UNKNOWN,
            'window': ElementType.WINDOW,
        }
        return type_mapping.get(control_type.lower(), ElementType.UNKNOWN)

    def _capture_window_hierarchy(self, window) -> List[UIElement]:
        elements = []
        tried_backends = []

        if not _ensure_pywinauto_com_init():
            logging.warning("pywinauto COM initialization failed, UI hierarchy will be unavailable")
            return elements

        tried_backends.append("uia")
        try:
            from pywinauto import Desktop
            app = Desktop(backend="uia")
            try:
                app_window = app.window(title=window.title, handle=window._hWnd)
                if app_window.exists():
                    root = app_window.wrapper_object()
                    elements = self._build_element_tree(root)
                    if elements:
                        return elements
            except Exception as e:
                logging.debug(f"Could not get UIA hierarchy for {window.title}: {e}")
        except ImportError:
            logging.debug("pywinauto UIA not available")

        tried_backends.append("win32")
        try:
            from pywinauto import Desktop
            app = Desktop(backend="win32")
            try:
                app_window = app.window(title=window.title, handle=window._hWnd)
                if app_window.exists():
                    root = app_window.wrapper_object()
                    elements = self._build_element_tree_win32(root)
                    if elements:
                        logging.info(f"Win32 backend succeeded for {window.title}")
                        return elements
            except Exception as e:
                logging.debug(f"Could not get Win32 hierarchy for {window.title}: {e}")
        except ImportError:
            logging.debug("pywinauto Win32 not available")

        return elements

    def _build_element_tree_win32(self, element, parent: Optional[UIElement] = None) -> List[UIElement]:
        elements = []
        try:
            children = element.children()
            for child in children:
                try:
                    title = child.window_text() or ""
                    if not title:
                        continue

                    try:
                        ctrl_type = child.friendly_class_name()
                    except:
                        ctrl_type = "unknown"

                    elem_type = self._map_control_type(ctrl_type)

                    rect = child.rectangle()
                    ui_elem = UIElement(
                        title=title,
                        element_type=elem_type,
                        rect=(rect.left, rect.top, rect.width(), rect.height()),
                        class_name=ctrl_type,
                        is_visible=True,
                        is_enabled=True,
                        parent=parent
                    )
                    ui_elem.children = self._build_element_tree_win32(child, ui_elem)
                    elements.append(ui_elem)
                except Exception as e:
                    continue
        except Exception as e:
            logging.debug(f"Error building Win32 element tree: {e}")
        return elements

    def _build_element_tree(self, element, parent: Optional[UIElement] = None) -> List[UIElement]:
        elements = []
        try:
            children = element.children()
            for child in children:
                try:
                    elem_type = self._map_control_type(child.element_type)
                    title = child.window_text() or ""
                    if not title and elem_type != ElementType.UNKNOWN:
                        title = f"[{elem_type.value}]"

                    if title:
                        rect = child.rectangle()
                        ui_elem = UIElement(
                            title=title,
                            element_type=elem_type,
                            rect=(rect.left, rect.top, rect.width(), rect.height()),
                            automation_id=child.automation_id(),
                            class_name=child.class_name(),
                            is_visible=child.is_visible(),
                            is_enabled=child.is_enabled(),
                            parent=parent
                        )
                        ui_elem.children = self._build_element_tree(child, ui_elem)
                        elements.append(ui_elem)
                except Exception as e:
                    continue
        except Exception as e:
            logging.debug(f"Error building element tree: {e}")
        return elements

    def update(self, force: bool = False) -> bool:
        current_time = time.time()
        if not force and (current_time - self._last_update) < self._update_interval:
            return False

        with self._lock:
            self._last_update = current_time
            previous_state = self._snapshot()

            try:
                all_windows = gw.getAllWindows()
                new_windows = {}

                for window in all_windows:
                    try:
                        if not window.title or window.width < 50 or window.height < 50:
                            continue

                        process_name = ""
                        try:
                            import psutil
                            proc = psutil.Process(window.owner)
                            process_name = proc.name()
                        except:
                            process_name = "unknown"

                        elements = self._capture_windowHierarchy(window)

                        win_info = WindowInfo(
                            title=window.title,
                            process_name=process_name,
                            rect=(window.left, window.top, window.width, window.height),
                            is_active=window == gw.getActiveWindow(),
                            elements=elements
                        )
                        new_windows[window.title] = win_info

                    except Exception as e:
                        logging.debug(f"Error capturing window {window}: {e}")
                        continue

                self.windows = new_windows

                active = gw.getActiveWindow()
                if active:
                    self.active_window = self.windows.get(active.title)
                    if self.active_window:
                        self.active_window.is_active = True
                    self.active_app = active.title

                self.previous_state = previous_state

                state_changed = self._detect_changes(previous_state)
                if state_changed:
                    self._notify_listeners("state_changed", state_changed)

                return True

            except Exception as e:
                logging.error(f"DesktopState update error: {e}")
                return False

    def _capture_windowHierarchy(self, window) -> List[UIElement]:
        elements = []
        try:
            from pywinauto import Desktop
            app = Desktop(backend="uia")
            try:
                app_window = app.window(title=window.title, handle=window._hWnd)
                if app_window.exists():
                    root = app_window.wrapper_object()
                    elements = self._build_element_tree(root)
            except Exception as e:
                logging.debug(f"Could not get UIA hierarchy for {window.title}: {e}")
        except ImportError:
            pass
        return elements

    def _snapshot(self) -> Dict:
        return {
            "windows": {k: {"title": v.title, "active": v.is_active} for k, v in self.windows.items()},
            "active_window": self.active_window.title if self.active_window else None,
            "timestamp": time.time()
        }

    def _detect_changes(self, previous: Optional[Dict]) -> Optional[Dict]:
        if not previous:
            return {"type": "initial", "windows": list(self.windows.keys())}

        current = self._snapshot()
        changes = {"type": "update", "differences": []}

        prev_windows = set(previous.get("windows", {}).keys())
        curr_windows = set(current.get("windows", {}).keys())

        if prev_windows != curr_windows:
            changes["differences"].append({
                "change": "windows_changed",
                "added": list(curr_windows - prev_windows),
                "removed": list(prev_windows - curr_windows)
            })

        if previous.get("active_window") != current.get("active_window"):
            changes["differences"].append({
                "change": "active_window_changed",
                "from": previous.get("active_window"),
                "to": current.get("active_window")
            })

        if changes["differences"]:
            return changes
        return None

    def find_element(self, description: str, fuzzy: bool = True) -> Optional[UIElement]:
        if not self.active_window:
            self.update(force=True)

        if not self.active_window:
            return None

        description_lower = description.lower()

        for element in self._flatten_elements(self.active_window.elements):
            title_lower = element.title.lower()

            if fuzzy:
                if description_lower in title_lower or any(
                    word in title_lower for word in description_lower.split() if len(word) > 2
                ):
                    return element
            else:
                if description_lower == title_lower:
                    return element

        return None

    def find_element_by_type(self, element_type: ElementType) -> List[UIElement]:
        if not self.active_window:
            return []
        return [e for e in self._flatten_elements(self.active_window.elements) if e.element_type == element_type]

    def _flatten_elements(self, elements: List[UIElement]) -> List[UIElement]:
        flat = []
        for elem in elements:
            flat.append(elem)
            flat.extend(self._flatten_elements(elem.children))
        return flat

    def get_buttons(self) -> List[UIElement]:
        return self.find_element_by_type(ElementType.BUTTON)

    def get_inputs(self) -> List[UIElement]:
        return self.find_element_by_type(ElementType.EDIT)

    def click_element(self, element: UIElement) -> bool:
        try:
            pyautogui.click(element.center_x, element.center_y)
            time.sleep(0.1)
            self.update(force=True)
            return True
        except Exception as e:
            logging.error(f"Failed to click element: {e}")
            return False

    def get_window_by_title(self, title: str, fuzzy: bool = True) -> Optional[WindowInfo]:
        if fuzzy:
            title_lower = title.lower()
            for win_title, win_info in self.windows.items():
                if title_lower in win_title.lower():
                    return win_info
        else:
            return self.windows.get(title)
        return None

    def start_monitoring(self, interval: float = 0.5):
        if self._is_monitoring:
            return
        self._is_monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval,), daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self):
        self._is_monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1)

    def _monitor_loop(self, interval: float):
        while self._is_monitoring:
            self.update()
            time.sleep(interval)

    def get_state_summary(self) -> str:
        if not self.active_window:
            return "No active window"
        summary = f"Active: {self.active_window.title}\n"
        summary += f"Process: {self.active_window.process_name}\n"
        summary += f"Elements: {len(self.active_window.elements)}\n"
        buttons = self.get_buttons()
        inputs = self.get_inputs()
        if buttons:
            summary += f"Buttons: {[b.title for b in buttons[:5]]}\n"
        if inputs:
            summary += f"Inputs: {[i.title for i in inputs[:5]]}\n"
        return summary

    def activate_window(self, title: str, fuzzy: bool = True) -> bool:
        win = self.get_window_by_title(title, fuzzy)
        if win:
            try:
                target = gw.getWindowsWithTitle(win.title)
                if target:
                    target[0].activate()
                    time.sleep(0.2)
                    self.update(force=True)
                    return True
            except Exception as e:
                logging.error(f"Failed to activate window '{title}': {e}")
        return False

    def minimize_window(self, title: str = None) -> bool:
        if title:
            win = self.get_window_by_title(title, fuzzy=True)
            if not win:
                return False
            try:
                target = gw.getWindowsWithTitle(win.title)
                if target:
                    target[0].minimize()
                    return True
            except Exception as e:
                logging.error(f"Failed to minimize window '{title}': {e}")
        elif self.active_window:
            try:
                target = gw.getWindowsWithTitle(self.active_window.title)
                if target:
                    target[0].minimize()
                    return True
            except Exception as e:
                logging.error(f"Failed to minimize active window: {e}")
        return False

    def maximize_window(self, title: str = None) -> bool:
        if title:
            win = self.get_window_by_title(title, fuzzy=True)
            if not win:
                return False
            try:
                target = gw.getWindowsWithTitle(win.title)
                if target:
                    target[0].maximize()
                    time.sleep(0.1)
                    self.update(force=True)
                    return True
            except Exception as e:
                logging.error(f"Failed to maximize window '{title}': {e}")
        elif self.active_window:
            try:
                target = gw.getWindowsWithTitle(self.active_window.title)
                if target:
                    target[0].maximize()
                    time.sleep(0.1)
                    self.update(force=True)
                    return True
            except Exception as e:
                logging.error(f"Failed to maximize active window: {e}")
        return False

    def close_window(self, title: str = None) -> bool:
        if title:
            win = self.get_window_by_title(title, fuzzy=True)
            if not win:
                return False
            try:
                target = gw.getWindowsWithTitle(win.title)
                if target:
                    target[0].close()
                    time.sleep(0.2)
                    self.update(force=True)
                    return True
            except Exception as e:
                logging.error(f"Failed to close window '{title}': {e}")
        elif self.active_window:
            try:
                target = gw.getWindowsWithTitle(self.active_window.title)
                if target:
                    target[0].close()
                    time.sleep(0.2)
                    self.update(force=True)
                    return True
            except Exception as e:
                logging.error(f"Failed to close active window: {e}")
        return False

    def find_element_in_all_windows(self, description: str, fuzzy: bool = True) -> List[tuple]:
        results = []
        description_lower = description.lower()

        for win_title, win_info in self.windows.items():
            for element in self._flatten_elements(win_info.elements):
                title_lower = element.title.lower()
                if fuzzy:
                    if description_lower in title_lower or any(
                        word in title_lower for word in description_lower.split() if len(word) > 2
                    ):
                        results.append((win_title, element))
                else:
                    if description_lower == title_lower:
                        results.append((win_title, element))
        return results

    def get_window_stack_order(self) -> List[str]:
        try:
            windows = gw.getWindowsInOrder()
            return [w.title for w in windows if w.title and w.width >= 50 and w.height >= 50]
        except Exception as e:
            logging.error(f"Failed to get window stack order: {e}")
            return []

    def bring_to_front(self, title: str = None) -> bool:
        if title:
            win = self.get_window_by_title(title, fuzzy=True)
            if win:
                try:
                    target = gw.getWindowsWithTitle(win.title)
                    if target:
                        target[0].bring_to_front()
                        time.sleep(0.1)
                        self.update(force=True)
                        return True
                except Exception as e:
                    logging.error(f"Failed to bring window to front: {e}")
        return False

    def get_app_windows(self, app_name: str) -> List[WindowInfo]:
        app_name_lower = app_name.lower()
        results = []
        for win_title, win_info in self.windows.items():
            if app_name_lower in win_info.process_name.lower() or app_name_lower in win_title.lower():
                results.append(win_info)
        return results


_desktop_state_instance: Optional[DesktopState] = None


def get_desktop_state() -> DesktopState:
    global _desktop_state_instance
    if _desktop_state_instance is None:
        _desktop_state_instance = DesktopState()
    return _desktop_state_instance