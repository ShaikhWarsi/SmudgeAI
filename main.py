import sys
# Fix COM threading model conflict (WinError -2147417850)
# 0 = COINIT_APARTMENTTHREADED (STA) - Required by PyQt5/SpeechRecognition
# 2 = COINIT_MULTITHREADED (MTA) - Preferred by pywinauto?
# We set it to 0 (STA) to match PyQt5 and avoid "Cannot change thread mode" error.
import sys
sys.coinit_flags = 0 

import gui  # Import the new Sci-Fi UI
if __name__ == "__main__":
    gui.run_gui()  # Start the futuristic UI
