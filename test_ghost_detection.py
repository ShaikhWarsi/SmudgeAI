
import monitoring
import time
import threading
import tkinter as tk
from PyQt5.QtCore import QCoreApplication

# Mock Signal
class MockSignal:
    def emit(self, msg):
        print(f"ALERT RECEIVED: {msg}")

def create_error_window():
    root = tk.Tk()
    root.title("Fatal Error - Test")
    root.geometry("200x100")
    # Keep it open for a few seconds
    root.after(5000, root.destroy)
    root.mainloop()

def test_ghost():
    print("Testing Ghost Mode Detection...")
    
    # Initialize Monitor
    mon = monitoring.Monitor()
    mon.alert_signal = MockSignal() # Monkey patch signal
    
    mon.start()
    print("Monitor started.")
    
    # Create a fake error window in a separate thread
    t = threading.Thread(target=create_error_window)
    t.start()
    
    # Wait for detection
    start_time = time.time()
    detected = False
    
    # Monitor loop sleeps 2s, cooldown 10s.
    # We should see it within ~3-4 seconds.
    # We will just sleep and watch stdout (captured by RunCommand)
    time.sleep(6)
    
    mon.stop()
    t.join()
    print("Test finished.")

if __name__ == "__main__":
    test_ghost()
