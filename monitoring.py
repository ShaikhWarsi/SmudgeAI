import threading
import time
import psutil
import pygetwindow as gw
from PyQt5.QtCore import QObject, pyqtSignal

class Monitor(QObject):
    alert_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.thread = None

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)

    def _monitor_loop(self):
        while self.running:
            try:
                # 1. Check System Health
                # interval=0.5 to be responsive but not hog CPU
                cpu = psutil.cpu_percent(interval=0.5) 
                ram = psutil.virtual_memory().percent
                
                if cpu > 95:
                    self.alert_signal.emit(f"CPU Critical: {cpu}%")
                    time.sleep(5) # Cooldown
                elif ram > 95:
                    self.alert_signal.emit(f"RAM Critical: {ram}%")
                    time.sleep(5)

                # 2. Check for Error Windows
                try:
                    windows = gw.getAllTitles()
                    for title in windows:
                        if not title: continue
                        title_lower = title.lower()
                        
                        # Keywords that indicate a problem
                        error_keywords = ["error", "exception", "fatal", "crash", "not responding"]
                        
                        if any(k in title_lower for k in error_keywords):
                            # Filter out false positives (e.g., IDEs, Browser tabs with 'error' in title)
                            if any(fp in title_lower for fp in ["visual studio code", "chrome", "edge", "firefox", "search"]):
                                continue
                                
                            self.alert_signal.emit(f"I see an error: '{title}'. Want me to fix it?")
                            time.sleep(10) # Don't spam alerts for the same window
                except Exception as e:
                    pass # Window enumeration might fail temporarily

                time.sleep(2)
            except Exception as e:
                print(f"Monitor Error: {e}")
                time.sleep(5)
