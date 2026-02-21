import sys
import threading
import asyncio
import logging
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QLineEdit, QVBoxLayout, QWidget, 
    QPushButton, QHBoxLayout, QGraphicsDropShadowEffect, QSystemTrayIcon, QMenu, QAction, QTextEdit
)
from PyQt5.QtGui import QFont, QColor, QPainter, QBrush, QPen, QIcon, QLinearGradient, QTextCursor
from PyQt5.QtCore import Qt, QSize, QPoint, pyqtSignal, QObject, QTimer, QPropertyAnimation, QEasingCurve, QRect, QDateTime

import speech_engine
import task_manager
import config

# Configure Logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)

class Worker(QObject):
    """Async Worker for handling background tasks without freezing UI."""
    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.start_loop, daemon=True).start()

    def start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_task(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop)

class ModernAssistant(QMainWindow):
    update_status_signal = pyqtSignal(str)
    update_result_signal = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.listening = True
        self.is_processing = False
        self.drag_pos = None
        
        # Async Worker
        self.worker = Worker()
        
        # UI Setup
        self.init_ui()
        self.init_tray()
        
        # Signals
        self.update_status_signal.connect(self.update_status)
        self.update_result_signal.connect(self.show_result)

        # Breathing Animation Timer
        self.glow_timer = QTimer(self)
        self.glow_timer.timeout.connect(self.animate_glow)
        self.glow_value = 0
        self.glow_direction = 1

        # Status Cycle Timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.cycle_status_message)
        self.status_messages = ["Reading Screen...", "Analyzing Error...", "Generating Patch...", "Verifying Fix..."]
        self.status_msg_index = 0

        # Start Listening Thread
        threading.Thread(target=self.listen_loop, daemon=True).start()

    def init_ui(self):
        """Initialize the Modern Spotlight-style UI."""
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Dimensions
        screen = QApplication.primaryScreen().geometry()
        width = 850
        height = 500 # Increased height for output area
        self.setGeometry(
            (screen.width() - width) // 2,
            screen.height() - 600, 
            width,
            height
        )

        # Main Container
        self.central_widget = QWidget(self)
        self.central_widget.setObjectName("central_widget")
        self.setCentralWidget(self.central_widget)
        
        # Main Layout (Vertical)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(20, 20, 20, 20)
        self.main_layout.setSpacing(10)
        
        # Top Bar (Input Area)
        self.top_bar_widget = QWidget()
        self.top_layout = QHBoxLayout(self.top_bar_widget)
        self.top_layout.setContentsMargins(0, 0, 0, 0)
        self.top_layout.setSpacing(15)
        
        # 1. Status Indicator (Pulsing Orb)
        self.status_indicator = QLabel("●", self)
        self.status_indicator.setFont(QFont("Arial", 28))
        self.status_indicator.setStyleSheet("color: #00FF00;") # Default Green
        self.top_layout.addWidget(self.status_indicator)

        # 2. Input Field
        self.input_field = QLineEdit(self)
        self.input_field.setPlaceholderText("Ask Jarvis...")
        self.input_field.setFont(QFont("Segoe UI", 16))
        self.input_field.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: white;
                selection-background-color: #444444;
            }
        """)
        self.input_field.returnPressed.connect(self.process_text_input)
        self.top_layout.addWidget(self.input_field)

        # 3. Action Buttons
        self.mic_btn = QPushButton("🎤", self)
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.clicked.connect(self.toggle_listening)
        self.style_button(self.mic_btn)
        self.top_layout.addWidget(self.mic_btn)

        self.stop_btn = QPushButton("🛑", self)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.clicked.connect(self.stop_action)
        self.style_button(self.stop_btn, color="#FF5555")
        self.top_layout.addWidget(self.stop_btn)

        self.ghost_btn = QPushButton("👻", self)
        self.ghost_btn.setCursor(Qt.PointingHandCursor)
        self.ghost_btn.clicked.connect(self.toggle_monitor)
        self.style_button(self.ghost_btn, color="#AAAAAA")
        self.ghost_btn.setToolTip("Active Monitoring (Ghost Mode)")
        self.top_layout.addWidget(self.ghost_btn)

        self.learn_btn = QPushButton("👁️", self)
        self.learn_btn.setCursor(Qt.PointingHandCursor)
        self.learn_btn.clicked.connect(self.toggle_learning)
        self.style_button(self.learn_btn, color="#AAAAAA")
        self.learn_btn.setToolTip("One-Shot Learning (Watch & Learn)")
        self.top_layout.addWidget(self.learn_btn)
        
        # Safe Mode Toggle
        self.safe_btn = QPushButton("🛡️", self)
        self.safe_btn.setCursor(Qt.PointingHandCursor)
        self.safe_btn.setCheckable(True)
        self.safe_btn.setChecked(config.SAFE_MODE)
        self.safe_btn.clicked.connect(self.toggle_safe_mode)
        self.style_button(self.safe_btn, color="#00FF00" if config.SAFE_MODE else "#AAAAAA")
        self.safe_btn.setToolTip("Safe Mode (Human-in-the-Loop)")
        self.top_layout.addWidget(self.safe_btn)

        self.min_btn = QPushButton("➖", self)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)
        self.style_button(self.min_btn, color="#AAAAAA")
        self.top_layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("✖", self)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        self.style_button(self.close_btn, color="#FF5555")
        self.top_layout.addWidget(self.close_btn)
        
        self.main_layout.addWidget(self.top_bar_widget)

        # 4. Output Area (TextEdit)
        self.output_area = QTextEdit(self)
        self.output_area.setReadOnly(True)
        self.output_area.setFont(QFont("Consolas", 10))
        self.output_area.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0, 0, 0, 50);
                color: #00FF00;
                border: 1px solid rgba(255, 255, 255, 10);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        self.main_layout.addWidget(self.output_area)

        # Glassmorphism Background
        self.central_widget.setStyleSheet("""
            QWidget#central_widget {
                background-color: rgba(20, 20, 20, 240);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 30);
            }
        """)

        # Drop Shadow (Glow)
        self.shadow = QGraphicsDropShadowEffect()
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QColor(0, 255, 0, 100)) # Green Glow
        self.shadow.setOffset(0, 0)
        self.central_widget.setGraphicsEffect(self.shadow)

    def style_button(self, btn, color="#FFFFFF"):
        btn.setFont(QFont("Segoe UI", 16))
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                color: {color};
            }}
            QPushButton:hover {{
                color: white;
                background-color: rgba(255, 255, 255, 20);
                border-radius: 15px;
            }}
        """)
        btn.setFixedSize(40, 40)

    def init_tray(self):
        """Initialize System Tray Icon."""
        self.tray_icon = QSystemTrayIcon(self)
        
        # Create a simple icon (placeholder)
        # In production, load a .png or .ico
        # For now, we rely on the default or system icon if image missing
        self.tray_icon.setIcon(QIcon("icon.png")) 
        
        menu = QMenu()
        restore_action = QAction("Show Jarvis", self)
        restore_action.triggered.connect(self.show_normal)
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(QApplication.instance().quit)
        
        menu.addAction(restore_action)
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()
        
        # Double click to restore
        self.tray_icon.activated.connect(self.on_tray_activated)

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_normal()
            self.activateWindow()

    def show_normal(self):
        self.show()
        self.activateWindow()
        self.input_field.setFocus()

    # --- Event Handlers ---

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and self.drag_pos:
            self.move(event.globalPos() - self.drag_pos)

    def mouseReleaseEvent(self, event):
        self.drag_pos = None

    def paintEvent(self, event):
        # Optional: Custom painting if stylesheet isn't enough
        pass

    # --- Logic ---

    def animate_glow(self):
        """Breathing animation for the shadow/glow."""
        if self.glow_direction == 1:
            self.glow_value += 2
            if self.glow_value >= 50:
                self.glow_direction = -1
        else:
            self.glow_value -= 2
            if self.glow_value <= 10:
                self.glow_direction = 1
        
        self.shadow.setBlurRadius(self.glow_value)

    def update_status(self, text):
        self.input_field.setPlaceholderText(text)
        
        if "Listening" in text:
            self.status_indicator.setStyleSheet("color: #00FF00;") # Green
            self.shadow.setColor(QColor(0, 255, 0, 150))
            self.glow_timer.stop()
            self.status_timer.stop() # Stop cycling
            self.shadow.setBlurRadius(20)
            
        elif "Processing" in text or "Thinking" in text:
            self.status_indicator.setStyleSheet("color: #00FFFF;") # Cyan
            self.shadow.setColor(QColor(0, 255, 255, 200))
            if not self.glow_timer.isActive():
                self.glow_timer.start(50) # Start breathing
            if not self.status_timer.isActive():
                self.status_timer.start(2000) # Change message every 2s
                
        elif "Speaking" in text:
            self.status_indicator.setStyleSheet("color: #FF00FF;") # Magenta
            self.shadow.setColor(QColor(255, 0, 255, 150))
            self.glow_timer.stop()
            self.status_timer.stop()
            self.shadow.setBlurRadius(30)

        elif "Stopped" in text:
            self.status_indicator.setStyleSheet("color: #FF5555;") # Red
            self.shadow.setColor(QColor(255, 85, 85, 150))
            self.glow_timer.stop()
            self.status_timer.stop()

    def cycle_status_message(self):
        self.status_msg_index = (self.status_msg_index + 1) % len(self.status_messages)
        msg = self.status_messages[self.status_msg_index]
        self.input_field.setPlaceholderText(msg)

    def toggle_safe_mode(self):
        config.SAFE_MODE = self.safe_btn.isChecked()
        if config.SAFE_MODE:
            self.style_button(self.safe_btn, color="#00FF00")
            self.show_result("Safe Mode Enabled. Dangerous actions will require confirmation.")
        else:
            self.style_button(self.safe_btn, color="#AAAAAA")
            self.show_result("Safe Mode Disabled. Running in Autonomous Mode.")

    def toggle_monitor(self):
        if not hasattr(self, 'monitor'):
            from monitoring import Monitor
            self.monitor = Monitor()
            self.monitor.alert_signal.connect(self.handle_monitor_alert)
            
        if self.monitor.running:
            self.monitor.stop()
            self.ghost_btn.setStyleSheet("color: #AAAAAA;") # Gray
            self.show_result("Active Monitoring Stopped.")
        else:
            self.monitor.start()
            self.ghost_btn.setStyleSheet("color: #00FF00;") # Green
            self.show_result("Active Monitoring Started (Ghost Mode). I'll watch for errors.")

    def handle_monitor_alert(self, message):
        self.show()
        self.show_normal()
        self.activateWindow()
        self.show_result(f"👻 {message}")
        # Speak it
        self.worker.run_task(speech_engine.speak(message))

    def toggle_learning(self):
        if not hasattr(self, 'learner'):
            from workflow_learner import WorkflowLearner
            self.learner = WorkflowLearner()
            self.learner.finished_signal.connect(self.handle_learning_finished)
            
        if self.learner.recording:
            self.learner.stop_learning()
            self.learn_btn.setStyleSheet("color: #AAAAAA;") # Gray
            self.show_result("Stopped watching. Analyzing workflow... (this may take a moment)")
        else:
            self.learner.start_learning()
            self.learn_btn.setStyleSheet("color: #FF0000;") # Red for recording
            self.show_result("I am watching. I will minimize now. Restore me to stop.")
            QTimer.singleShot(2000, self.showMinimized) # Give time to read
            
    def handle_learning_finished(self, message):
        self.show()
        self.show_normal()
        self.activateWindow()
        self.show_result(f"🧠 {message}")
        self.worker.run_task(speech_engine.speak("I have learned the workflow."))
        self.input_field.setToolTip(message)

    def show_result(self, text):
        if text.startswith("Error:"):
            color = "#FF5555" # Red
        else:
            color = "#00FF00" # Green

        # Append to Output Area with Timestamp
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        # Ensure HTML is safe or escaped if needed, but for simple text it's fine.
        # Using insertHtml to append colored text
        self.output_area.moveCursor(QTextCursor.End)
        self.output_area.insertHtml(f"<span style='color: #888888;'>[{timestamp}]</span> <span style='color: {color};'>{text}</span><br>")
        self.output_area.moveCursor(QTextCursor.End)
        
        self.input_field.clear()
        self.input_field.setPlaceholderText("Ready...")

    def process_text_input(self):
        text = self.input_field.text()
        if text:
            self.is_processing = True
            self.update_status_signal.emit("Processing...")
            self.worker.run_task(self.execute_async(text))
            self.input_field.clear()

    async def execute_async(self, text):
        try:
            # 0. Intercept Special Commands
            if "watch me" in text.lower() or "start learning" in text.lower():
                self.update_result_signal.emit("Starting One-Shot Learning...")
                # We need to run this on the main thread because it updates UI (minimize)
                # But execute_async is running in a worker thread (asyncio loop)
                # We can use QMetaObject.invokeMethod or just a signal.
                # Since toggle_learning is simple, we can call it if we are careful, 
                # but better to emit a signal if we were strictly following Qt.
                # For now, let's just use QTimer.singleShot(0, ...) to run on main thread
                QTimer.singleShot(0, self.toggle_learning)
                return

            if "stop learning" in text.lower():
                if hasattr(self, 'learner') and self.learner.recording:
                    QTimer.singleShot(0, self.toggle_learning)
                return

            # 1. Execute Logic
            response = await task_manager.execute_task(text)
            self.update_result_signal.emit(str(response))
            
            # 2. Speak Response (Blocking UI update, so we use signal)
            self.update_status_signal.emit("Speaking...")
            await speech_engine.speak(str(response))
            
        except Exception as e:
            self.update_result_signal.emit(f"Error: {e}")
        finally:
            self.is_processing = False
            self.update_status_signal.emit("Ready (Listening)")

    def listen_loop(self):
        """Background thread for always-on listening."""
        while True:
            # Pause listening if we are processing or speaking
            if self.listening and not self.is_processing:
                try:
                    # Run synchronous listen in this thread
                    text = speech_engine.listen_sync() 
                    if text:
                        self.update_status_signal.emit(f"Heard: {text}")
                        self.is_processing = True # Lock
                        self.worker.run_task(self.execute_async(text))
                except Exception as e:
                    logging.error(f"Listening error: {e}")
            
            # Sleep briefly to prevent CPU hogging
            threading.Event().wait(0.5)

    def toggle_listening(self):
        self.listening = not self.listening
        if self.listening:
            self.mic_btn.setText("🎤")
            self.update_status_signal.emit("Listening Mode ON")
        else:
            self.mic_btn.setText("🔇")
            self.update_status_signal.emit("Muted")

    def stop_action(self):
        """Emergency Stop."""
        self.is_processing = False
        speech_engine.stop_speaking()
        self.glow_timer.stop()
        self.update_status_signal.emit("Stopped.")

def run_gui():
    app = QApplication(sys.argv)
    app.setStyle("Fusion") # Better cross-platform look
    
    # Set App Icon
    app_icon = QIcon("icon.png") # Placeholder
    app.setWindowIcon(app_icon)

    window = ModernAssistant()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    run_gui()
