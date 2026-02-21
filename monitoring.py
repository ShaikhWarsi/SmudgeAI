import time
import threading
import pyautogui
import pygetwindow as gw
import logging
import os
import asyncio
from PyQt5.QtCore import QObject, pyqtSignal
import ai_engine

class Monitor(QObject):
    alert_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.last_mouse_pos = pyautogui.position()
        self.last_activity_time = time.time()
        self.check_interval = 5 # seconds
        self.idle_threshold = 60 # seconds
        self.target_apps = ["Visual Studio Code", "Cursor", "PyCharm", "Sublime Text", "Command Prompt", "PowerShell"]

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _loop(self):
        logging.info("Active Monitoring Started")
        while self.running:
            try:
                self._check_activity()
            except Exception as e:
                logging.error(f"Monitor loop error: {e}")
            time.sleep(self.check_interval)

    def _check_activity(self):
        # 1. Check Idle
        current_pos = pyautogui.position()
        if current_pos != self.last_mouse_pos:
            self.last_mouse_pos = current_pos
            self.last_activity_time = time.time()
            return # User is active

        idle_time = time.time() - self.last_activity_time
        if idle_time < self.idle_threshold:
            return # Not idle long enough

        # 2. Check Active Window
        try:
            active_window = gw.getActiveWindow()
            if not active_window:
                return
            
            is_target = any(app in active_window.title for app in self.target_apps)
            if not is_target:
                return
        except Exception:
            return

        # 3. Take Screenshot and Analyze
        logging.info(f"User idle in {active_window.title} for {idle_time:.1f}s. Analyzing screen...")
        
        screenshot_path = "monitor_screenshot.png"
        pyautogui.screenshot(screenshot_path)
        
        prompt = """
        Analyze this screenshot of a code editor or terminal.
        Is there a VISIBLE error message, stack trace, or red squiggle indicating a bug that the user might be stuck on?
        If YES, return a short, friendly, helpful message starting with 'I see you're stuck on...'. 
        If NO, return 'NO'.
        """
        
        # Run async analysis
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(ai_engine.analyze_image(screenshot_path, prompt))
            loop.close()
            
            if result and "NO" not in result and "Error" not in result:
                self.alert_signal.emit(result)
                # Reset activity time to avoid spamming the same error
                self.last_activity_time = time.time() 
        except Exception as e:
            logging.error(f"Analysis failed: {e}")
        finally:
            if os.path.exists(screenshot_path):
                try:
                    os.remove(screenshot_path)
                except:
                    pass
