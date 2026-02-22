import time
import threading
import pyautogui
import os
import logging
import asyncio
from pynput import mouse, keyboard
import ai_engine
import shutil
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal
# from pywinauto import Desktop


class WorkflowLearner(QObject):
    finished_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.recording = False
        self.events = []
        self.screenshot_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflow_data")
        self.mouse_listener = None
        self.keyboard_listener = None
        self.start_time = 0

    def start_learning(self):
        if self.recording:
            return
            
        self.recording = True
        self.events = []
        self.start_time = time.time()
        
        # Setup directory
        if os.path.exists(self.screenshot_dir):
            shutil.rmtree(self.screenshot_dir)
        os.makedirs(self.screenshot_dir)
        
        # Start Listeners
        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.keyboard_listener = keyboard.Listener(on_press=self.on_press)
        
        self.mouse_listener.start()
        self.keyboard_listener.start()
        
        logging.info("Workflow Learning Started")

    def stop_learning(self):
        if not self.recording:
            return
            
        self.recording = False
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            
        logging.info("Workflow Learning Stopped. Processing...")
        
        # Process in background
        threading.Thread(target=self._process_workflow, daemon=True).start()

    def on_click(self, x, y, button, pressed):
        if not self.recording or not pressed:
            return
            
        # Capture context
        timestamp = time.time() - self.start_time
        screenshot_path = os.path.join(self.screenshot_dir, f"click_{int(timestamp*1000)}.png")
        pyautogui.screenshot(screenshot_path)
        
        # Get UI Element info using pywinauto (UIA)
        try:
            from pywinauto import Desktop
            # We try UIA first as it gives better info for modern apps
            elem = Desktop(backend="uia").from_point(x, y)
            element_info = {
                "title": elem.window_text(),
                "control_type": elem.element_type,
                "auto_id": elem.automation_id(),
                "class_name": elem.class_name(),
                "parent_title": elem.top_level_parent().window_text() if elem.top_level_parent() else "Unknown"
            }
        except Exception as e:
            element_info = f"Error getting element info: {str(e)}"

        self.events.append({
            "type": "click",
            "x": x,
            "y": y,
            "button": str(button),
            "timestamp": timestamp,
            "screenshot": screenshot_path,
            "element_info": element_info
        })

    def on_press(self, key):
        if not self.recording:
            return
            
        try:
            k = key.char
        except AttributeError:
            k = str(key)
            
        self.events.append({
            "type": "type",
            "key": k,
            "timestamp": time.time() - self.start_time
        })

    def _process_workflow(self):
        try:
            # Generate Script (async)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            script = loop.run_until_complete(ai_engine.generate_workflow_script(self.events))
            
            # Save Script
            filename = f"workspace/learned_workflow_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(script)
                
            self.finished_signal.emit(f"Workflow learned and saved to {filename}")
            
        except Exception as e:
            logging.error(f"Workflow processing failed: {e}")
            self.finished_signal.emit(f"Error processing workflow: {e}")
