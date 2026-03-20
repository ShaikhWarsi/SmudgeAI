import sys
import threading
import asyncio
import logging
import os
import json
import pyautogui
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QLineEdit, QVBoxLayout, QWidget, 
    QPushButton, QHBoxLayout, QGraphicsDropShadowEffect, QSystemTrayIcon, QMenu, QAction, QTextEdit
)
from PyQt5.QtGui import QFont, QColor, QPainter, QBrush, QPen, QIcon, QLinearGradient, QTextCursor
from PyQt5.QtCore import Qt, QSize, QPoint, pyqtSignal, QObject, QTimer, QPropertyAnimation, QEasingCurve, QRect, QDateTime

import speech_engine
import task_manager
import config
import ai_engine

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
    task_completed_signal = pyqtSignal(object)
    task_error_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.start_loop, daemon=True)
        self._thread.start()
        self._pending_futures = {}

    def start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_task(self, coro):
        future = asyncio.run_coroutine_threadsafe(self._safe_task_wrapper(coro), self.loop)
        return future

    async def _safe_task_wrapper(self, coro):
        try:
            result = await coro
            self.task_completed_signal.emit(result)
            return result
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            logging.error(f"Task failed with error: {error_msg}")
            self.task_error_signal.emit(error_msg)
            return None

class ModernAssistant(QMainWindow):
    update_status_signal = pyqtSignal(str)
    update_result_signal = pyqtSignal(str)
    ask_permission_signal = pyqtSignal(str, object) # text, event
    tool_log_signal = pyqtSignal(str, object) # name, args
    stop_signal = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        
        # Override task_manager.speak to use our GUI method
        # This allows background tasks to speak through the main UI loop
        task_manager.speak = self.handle_external_speech
        
        # Connect task_manager status updates to UI
        task_manager.set_status_callback(self.update_status_signal.emit)
        task_manager.set_permission_callback(self.ask_permission_gui)
        task_manager.set_tool_execution_callback(self.tool_log_signal.emit)

        self.listening = True
        self.is_processing = False
        self.drag_pos = None
        
        # Typewriter Queue
        self.typing_queue = []
        self.typewriter_timer = QTimer(self)
        self.typewriter_timer.timeout.connect(self._process_typewriter_queue)
        
        # Async Worker
        self.worker = Worker()
        
        # UI Setup
        self.init_ui()
        self.init_tray()
        
        # Signals
        self.update_status_signal.connect(self.update_status)
        self.update_result_signal.connect(self.show_result)
        self.ask_permission_signal.connect(self.show_permission_dialog)
        self.tool_log_signal.connect(self.log_tool_execution)
        self.stop_signal.connect(self.stop_action)

        # Worker task signals
        self.worker.task_completed_signal.connect(self.on_task_completed)
        self.worker.task_error_signal.connect(self.on_task_error)

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
        self.vision_btn = QPushButton("📷 Vision", self)
        self.vision_btn.setCursor(Qt.PointingHandCursor)
        self.vision_btn.clicked.connect(self.analyze_screen)
        self.style_button(self.vision_btn, color="#AAAAAA", width=100)
        self.vision_btn.setToolTip("Analyze Screen Context (Vision)")
        self.top_layout.addWidget(self.vision_btn)

        self.mic_btn = QPushButton("🎤 Mic", self)
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.clicked.connect(self.toggle_listening)
        self.style_button(self.mic_btn, width=80)
        self.top_layout.addWidget(self.mic_btn)

        self.stop_btn = QPushButton("🛑 Stop", self)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.clicked.connect(self.stop_action)
        self.style_button(self.stop_btn, color="#FF5555", width=90)
        self.top_layout.addWidget(self.stop_btn)

        # Settings Menu Button
        self.settings_btn = QPushButton("⚙️ Settings", self)
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.style_button(self.settings_btn, color="#AAAAAA", width=110)
        self.settings_btn.setToolTip("Advanced Settings")
        
        self.settings_menu = QMenu(self)
        self.settings_menu.setStyleSheet("""
            QMenu {
                background-color: #222222;
                color: white;
                border: 1px solid #444444;
                border-radius: 5px;
            }
            QMenu::item {
                padding: 10px 30px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #444444;
            }
            QMenu::separator {
                height: 1px;
                background: #444444;
                margin: 5px 10px;
            }
        """)

        # 1. Ghost Mode (Monitor)
        self.monitor_action = QAction("� Ghost Monitor", self)
        self.monitor_action.setCheckable(True)
        self.monitor_action.triggered.connect(self.toggle_monitor)
        self.settings_menu.addAction(self.monitor_action)

        # 2. Learn Workflow
        self.learn_action = QAction("👁️ Watch & Learn", self)
        self.learn_action.triggered.connect(self.toggle_learning)
        self.settings_menu.addAction(self.learn_action)

        self.settings_menu.addSeparator()

        # 3. Silent Mode
        self.silent_action = QAction("🔇 Silent Mode", self)
        self.silent_action.setCheckable(True)
        self.silent_action.triggered.connect(self.toggle_silent_mode)
        self.settings_menu.addAction(self.silent_action)

        # 4. Safe Mode
        self.safe_action = QAction("🛡️ Safe Mode", self)
        self.safe_action.setCheckable(True)
        self.safe_action.setChecked(config.SAFE_MODE)
        self.safe_action.triggered.connect(self.toggle_safe_mode)
        self.settings_menu.addAction(self.safe_action)

        self.settings_btn.setMenu(self.settings_menu)
        self.top_layout.addWidget(self.settings_btn)

        self.min_btn = QPushButton("➖", self)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)
        self.style_button(self.min_btn, color="#AAAAAA", width=40)
        self.top_layout.addWidget(self.min_btn)

        self.close_btn = QPushButton("✖", self)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.force_close)
        self.style_button(self.close_btn, color="#FF5555", width=40)
        self.top_layout.addWidget(self.close_btn)
        
        self.main_layout.addWidget(self.top_bar_widget)

        # Content Area (Horizontal Layout)
        self.content_widget = QWidget()
        self.content_layout = QHBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(10)
        
        # 4. Output Area (Left)
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
        self.content_layout.addWidget(self.output_area, stretch=7)
        
        # 5. Thought Bubble / Tool Log (Right)
        self.tool_log_area = QTextEdit(self)
        self.tool_log_area.setReadOnly(True)
        self.tool_log_area.setPlaceholderText("🧠 Neural Activity...")
        self.tool_log_area.setFont(QFont("Consolas", 9))
        self.tool_log_area.setStyleSheet("""
            QTextEdit {
                background-color: rgba(0, 20, 40, 50);
                color: #00FFFF;
                border: 1px solid rgba(0, 255, 255, 20);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        self.content_layout.addWidget(self.tool_log_area, stretch=3)
        
        self.main_layout.addWidget(self.content_widget)

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

    def style_button(self, btn, color="#FFFFFF", width=40):
        btn.setFont(QFont("Segoe UI", 10 if width > 40 else 16))
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: 1px solid rgba(255, 255, 255, 10);
                color: {color};
                padding: 5px;
            }}
            QPushButton:hover {{
                color: white;
                background-color: rgba(255, 255, 255, 20);
                border-radius: 5px;
            }}
        """)
        btn.setFixedSize(width, 40)

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

    def closeEvent(self, event):
        self.force_close()
        event.accept()

    def paintEvent(self, event):
        # Optional: Custom painting if stylesheet isn't enough
        pass

    # --- Logic ---

    def log_tool_execution(self, tool_name, args):
        """Displays the tool call in the thought bubble."""
        try:
            arg_str = json.dumps(args, indent=2)
            # Remove braces for cleaner look
            arg_str = arg_str.replace('{', '').replace('}', '').strip()
            
            log_entry = f"<b style='color: #00FFFF;'>[TOOL] {tool_name}</b><br><span style='color: #AAAAAA;'>{arg_str}</span><br>"
            self.tool_log_area.append(log_entry)
            
            # Auto scroll
            cursor = self.tool_log_area.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.tool_log_area.setTextCursor(cursor)
        except:
            self.tool_log_area.append(f"[TOOL] {tool_name}")

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

    def ask_permission_gui(self, text):
        """Called by task_manager from a background thread."""
        event = threading.Event()
        self.permission_result = False # Default deny
        self.ask_permission_signal.emit(text, event)
        event.wait() # Block the background thread until UI answers
        return self.permission_result

    def show_permission_dialog(self, text, event):
        """Shows a modal-like overlay for permission."""
        # Create overlay if not exists
        if not hasattr(self, 'perm_overlay'):
             self.perm_overlay = QWidget(self)
             self.perm_overlay.setObjectName("perm_overlay")
             self.perm_overlay.setStyleSheet("background-color: rgba(20, 20, 20, 240); border-radius: 20px; border: 2px solid #FF5555;")
             
             # Center it
             w, h = 400, 250
             self.perm_overlay.setGeometry((self.width() - w)//2, (self.height() - h)//2, w, h)
             
             layout = QVBoxLayout(self.perm_overlay)
             
             title = QLabel("⚠️ PERMISSION REQUIRED", self.perm_overlay)
             title.setStyleSheet("color: #FF5555; font-size: 20px; font-weight: bold;")
             title.setAlignment(Qt.AlignCenter)
             layout.addWidget(title)
             
             self.perm_label = QLabel(text, self.perm_overlay)
             self.perm_label.setWordWrap(True)
             self.perm_label.setStyleSheet("color: white; font-size: 14px;")
             self.perm_label.setAlignment(Qt.AlignCenter)
             layout.addWidget(self.perm_label)
             
             btn_layout = QHBoxLayout()
             
             deny_btn = QPushButton("DENY", self.perm_overlay)
             deny_btn.setCursor(Qt.PointingHandCursor)
             deny_btn.setStyleSheet("background-color: #FF5555; color: white; border-radius: 5px; padding: 8px; font-weight: bold;")
             deny_btn.clicked.connect(self.deny_permission)
             
             allow_btn = QPushButton("ALLOW", self.perm_overlay)
             allow_btn.setCursor(Qt.PointingHandCursor)
             allow_btn.setStyleSheet("background-color: #00FF00; color: black; border-radius: 5px; padding: 8px; font-weight: bold;")
             allow_btn.clicked.connect(self.allow_permission)
             
             btn_layout.addWidget(deny_btn)
             btn_layout.addWidget(allow_btn)
             layout.addLayout(btn_layout)
             
             # Add shadow
             shadow = QGraphicsDropShadowEffect()
             shadow.setBlurRadius(50)
             shadow.setColor(QColor(0, 0, 0, 200))
             self.perm_overlay.setGraphicsEffect(shadow)

        self.perm_label.setText(text)
        self.current_perm_event = event
        self.perm_overlay.show()
        self.perm_overlay.raise_()
        
        # Flash the window to get attention
        QApplication.alert(self)

    def allow_permission(self):
        self.permission_result = True
        self.perm_overlay.hide()
        if hasattr(self, 'current_perm_event'):
            self.current_perm_event.set()

    def deny_permission(self):
        self.permission_result = False
        self.perm_overlay.hide()
        if hasattr(self, 'current_perm_event'):
            self.current_perm_event.set()

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

        elif "Error" in text or "🔴" in text:
            self.status_indicator.setStyleSheet("color: #FF0000;") # Red
            self.shadow.setColor(QColor(255, 0, 0, 200))
            self.glow_timer.start(100) # Fast pulse
            self.shadow.setBlurRadius(40)

        elif "Analyzing" in text or "🟡" in text:
            self.status_indicator.setStyleSheet("color: #FFA500;") # Orange/Yellow
            self.shadow.setColor(QColor(255, 165, 0, 180))
            self.glow_timer.start(50)

        elif "Patch" in text or "🟢" in text:
            self.status_indicator.setStyleSheet("color: #00FF00;") # Green
            self.shadow.setColor(QColor(0, 255, 0, 180))
            self.glow_timer.stop()
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
        config.SAFE_MODE = self.safe_action.isChecked()
        if config.SAFE_MODE:
            self.show_result("Safe Mode Enabled. Dangerous actions will require confirmation.")
            self.update_status_signal.emit("SAFE MODE ACTIVE")
        else:
            self.show_result("Safe Mode Disabled. Running in Autonomous Mode.")
            self.update_status_signal.emit("Autonomous Mode")

    def toggle_silent_mode(self):
        is_silent = self.silent_action.isChecked()
        speech_engine.set_silent_mode(is_silent)
        
        if is_silent:
            self.show_result("Silent Mode Enabled. Shhh.")
        else:
            self.show_result("Silent Mode Disabled. I can speak again.")


    def toggle_monitor(self):
        if not hasattr(self, 'monitor'):
            from monitoring import Monitor
            self.monitor = Monitor()
            self.monitor.alert_signal.connect(self.handle_monitor_alert)
            
        if self.monitor.running:
            self.monitor.stop()
            self.monitor_action.setChecked(False)
            self.show_result("Active Monitoring Stopped.")
        else:
            self.monitor.start()
            self.monitor_action.setChecked(True)
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
            self.show_result("Stopped watching. Analyzing workflow... (this may take a moment)")
        else:
            self.learner.start_learning()
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
        
        # Prepare for typewriter
        prefix_html = f"<span style='color: #888888;'>[{timestamp}]</span> <span style='color: {color};'>"
        
        # Queue the content
        self.typing_queue.append({'type': 'html', 'content': prefix_html})
        for char in text:
            self.typing_queue.append({'type': 'text', 'content': char})
        self.typing_queue.append({'type': 'html', 'content': "</span><br>"})
        self.typing_queue.append({'type': 'ui_update', 'action': 'reset_input'})
        
        if not self.typewriter_timer.isActive():
            self.typewriter_timer.start(10) # 10ms per char

    def _process_typewriter_queue(self):
        if not self.typing_queue:
            self.typewriter_timer.stop()
            return
            
        item = self.typing_queue.pop(0)
        
        self.output_area.moveCursor(QTextCursor.End)
        
        if item['type'] == 'html':
            self.output_area.insertHtml(item['content'])
        elif item['type'] == 'ui_update':
            if item['action'] == 'reset_input':
                self.input_field.setPlaceholderText("Ready...")
        else:
            self.output_area.insertPlainText(item['content'])
            
        self.output_area.moveCursor(QTextCursor.End)
        # Auto-scroll
        sb = self.output_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def handle_external_speech(self, text):
        """Called by task_manager to speak through GUI loop."""
        self.update_status_signal.emit(f"Speaking...")
        self.worker.run_task(speech_engine.speak(text))

    def analyze_screen(self):
        """Takes a screenshot and asks AI to analyze it."""
        self.update_status_signal.emit("Analyzing Screen Context...")
        self.vision_btn.setStyleSheet("color: #00FFFF; border: none; background: transparent;") 
        self.worker.run_task(self._analyze_screen_async())

    async def _analyze_screen_async(self):
        try:
            screenshot_path = os.path.join(os.getcwd(), "screen_context.png")
            # Run screenshot in thread to avoid UI freeze if slow
            await asyncio.to_thread(pyautogui.screenshot, screenshot_path)
            
            prompt = "Analyze this screen. What is the user doing? What is the context? Be brief."
            response = await ai_engine.analyze_image(screenshot_path, prompt)
            
            self.update_result_signal.emit(f"Vision: {response}")
            self.update_status_signal.emit("Analysis Complete")
            
        except Exception as e:
            self.update_result_signal.emit(f"Vision Error: {e}")

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
            # Always listen, even if processing (for interruption)
            if self.listening:
                try:
                    # Run synchronous listen in this thread
                    # Ensure listen_sync is non-blocking or has short timeout
                    text = speech_engine.listen_sync() 
                    
                    if text:
                        text_lower = text.lower()
                        logging.info(f"Heard: {text}")
                        self.update_status_signal.emit(f"Heard: {text}")
                        
                        # 1. Check for Interruption / Stop Command
                        if any(word in text_lower for word in ["stop", "shut up", "cancel", "abort", "quiet"]):
                            self.stop_signal.emit()
                            self.worker.run_task(speech_engine.speak("Stopped."))
                            continue

                        # 2. If processing, ignore other commands (or queue them)
                        if self.is_processing:
                            logging.info("Ignored command while processing (busy).")
                            continue
                            
                        # 3. Process new command
                        self.is_processing = True # Lock
                        self.worker.run_task(self.execute_async(text))
                        
                except Exception as e:
                    logging.error(f"Listening error: {e}")
            
            # Sleep briefly to prevent CPU hogging
            threading.Event().wait(0.1)

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
        task_manager.stop_execution()
        self.glow_timer.stop()
        self.update_status_signal.emit("Stopped.")

    def on_task_completed(self, result):
        if result is None:
            return
        logging.debug(f"Background task completed: {result}")

    def on_task_error(self, error_msg: str):
        logging.error(f"Task error: {error_msg}")
        self.update_status_signal.emit(f"Error: {error_msg}")
        self.show_result(f"Task failed: {error_msg}")

    def force_close(self):
        """Force close both GUI and terminal."""
        print("Shutting down SmudgeAI...")
        speech_engine.stop_speaking()
        try:
            task_manager.stop_execution()
        except:
            pass
        try:
            import os
            os._exit(0)
        except:
            pass
        QApplication.instance().quit()

def run_gui():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName("SmudgeAI")

    app_icon = QIcon("icon.png")
    app.setWindowIcon(app_icon)

    window = ModernAssistant()
    window.setWindowTitle("SmudgeAI")
    print("GUI Started Successfully")
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    run_gui()
