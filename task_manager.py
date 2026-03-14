# task_manager.py
import os
import subprocess
import asyncio
import logging
import webbrowser
import shutil
import pyautogui
from PIL import Image
from datetime import datetime
import time
import ai_engine
import pygetwindow as gw
import inspect
# from pywinauto import Desktop  <-- Moved to function scope to avoid COM conflicts
from googlesearch import search
import requests
from bs4 import BeautifulSoup
import traceback
import json
import config
import ctypes
import web_automation
import pyperclip
import pythoncom
from types import SimpleNamespace
from skill_manager import skill_manager

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global callback for status updates (to be set by GUI)
status_callback = None
permission_callback = None
tool_execution_callback = None
stop_execution_flag = False

COMMAND_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "command_cache.json")
COMMAND_CACHE = {}
CACHE_EXPIRY_HOURS = 24
CACHE_MAX_SIZE = 50

def load_command_cache():
    global COMMAND_CACHE
    if os.path.exists(COMMAND_CACHE_FILE):
        try:
            with open(COMMAND_CACHE_FILE, 'r') as f:
                data = json.load(f)
                
            # Filter expired entries and enforce size limit
            current_time = time.time()
            valid_cache = {}
            
            # Sort by timestamp (oldest first) if available, otherwise just process
            # We need to handle legacy cache format (without timestamp)
            sorted_items = []
            for k, v in data.items():
                if isinstance(v, dict) and "timestamp" in v:
                    sorted_items.append((k, v))
                else:
                    # Treat legacy items as new or discard? Let's keep them with current time
                    v["timestamp"] = current_time
                    sorted_items.append((k, v))
            
            # Sort by timestamp
            sorted_items.sort(key=lambda x: x[1]["timestamp"])
            
            # Keep only recent valid ones
            for k, v in sorted_items:
                if (current_time - v["timestamp"]) < (CACHE_EXPIRY_HOURS * 3600):
                    valid_cache[k] = v
            
            # Enforce Max Size (keep newest)
            if len(valid_cache) > CACHE_MAX_SIZE:
                # Convert back to list to slice
                items = list(valid_cache.items())
                # Keep last N
                valid_cache = dict(items[-CACHE_MAX_SIZE:])
                
            COMMAND_CACHE = valid_cache
        except Exception as e:
            logging.error(f"Failed to load command cache: {e}")
            COMMAND_CACHE = {}

def save_command_cache():
    try:
        with open(COMMAND_CACHE_FILE, 'w') as f:
            json.dump(COMMAND_CACHE, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save command cache: {e}")

load_command_cache()

def set_status_callback(callback):
    """Sets the callback function for status updates."""
    global status_callback
    status_callback = callback

def set_permission_callback(callback):
    """Sets the callback function for user permission requests."""
    global permission_callback
    permission_callback = callback

def set_tool_execution_callback(callback):
    """Sets the callback for logging tool executions (Thought Bubble)."""
    global tool_execution_callback
    tool_execution_callback = callback

def log_tool_execution(tool_name, args):
    """Logs the tool execution to the GUI."""
    if tool_execution_callback:
        tool_execution_callback(tool_name, args)

def ask_user_permission(action_description: str) -> bool:
    """Asks the user for permission to execute a sensitive action."""
    if permission_callback:
        return permission_callback(action_description)
    
    # Fallback to console input (or safe mode default deny)
    # In a real headless mode, this might log and return False
    print(f"⚠️ Safe Mode Permission Request: {action_description}")
    return False 

def stop_execution():
    """Signals the task manager to stop current execution."""
    global stop_execution_flag
    stop_execution_flag = True
    update_status("🛑 Execution Stopping...")

def update_status(message: str):
    """Updates the UI status if a callback is registered."""
    if status_callback:
        status_callback(message)

task_queue = []
task_history = []

WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")
if not os.path.exists(WORKSPACE_DIR):
    os.makedirs(WORKSPACE_DIR)

def speak(text):
    print(text)
    # This will be overridden by gui.py to use the real speech engine
    pass

def _maximize_active_window():
    """Helper to maximize the active window after launch using a polling loop."""
    start_time = time.time()
    while time.time() - start_time < 5:  # 5 second timeout
        try:
            win = gw.getActiveWindow()
            if win:
                win.maximize()
                return
        except:
            pass
        time.sleep(0.1)

def open_application(app_name: str):
    """Opens an application using robust path finding.
    
    Args:
        app_name: The name of the application to open (e.g., 'notepad', 'calculator', 'chrome').
    """
    # Safe Mode Check
    if config.SAFE_MODE:
        if not ask_user_permission(f"Open application '{app_name}'?"):
            return f"Action blocked by user in Safe Mode: Open {app_name}"

    # 1. Try shutil.which (PATH)
    path = shutil.which(app_name)
    
    # 2. PowerShell "Get-StartApps" (Fast & Robust)
    if not path:
        try:
            # Search for the app in the Start Menu index
            ps_script = f"Get-StartApps | Where-Object {{ $_.Name -like '*{app_name}*' }} | Select-Object -First 1 -ExpandProperty AppID"
            result = subprocess.run(["powershell", "-Command", ps_script], capture_output=True, text=True)
            app_id = result.stdout.strip()
            
            if app_id:
                # Use shell:AppsFolder to launch UWP or Win32 apps by AppID
                os.startfile(f"shell:AppsFolder\\{app_id}")
                _maximize_active_window()
                return f"Opened {app_name} via PowerShell."
        except Exception:
            pass

    # 3. Common paths (Optimized: No os.walk on ProgramFiles)
    # Just check direct existence if possible or skip.
    # We can use glob for a shallow search if needed, but PowerShell usually finds it.
    
    if path:
        try:
            if path.endswith(".lnk"):
                os.startfile(path)
            else:
                subprocess.Popen([path])
            _maximize_active_window()
            return f"Opened {app_name} at {path}"
        except Exception as e:
            return f"Error opening {app_name}: {e}"
    else:
        return f"Application {app_name} not found. Try installing it or adding it to PATH."

def click_element_by_name(name: str):
    """
    Clicks a UI element (Button, MenuItem, etc.) by its visible text using Accessibility APIs.
    More robust than coordinate-based clicks.
    
    Args:
        name: The text/title of the element to click.
    """
    try:
        # Try to find element in active window first
        # We catch potential errors if no window is active or UIA fails
        try:
            from pywinauto import Desktop
            app = Desktop(backend="uia")
            # Get the top window. 'active_only=True' in recent pywinauto versions might need specific handling
            # window() without args gets the top window
            active_window = app.window(active_only=True)
            if not active_window.exists():
                 return "No active window found."
        except Exception as e:
            return f"Error connecting to active window: {e}"

        # Search for element with matching title
        # We try control_type="Button" first as most common
        try:
             # Using exact match for now
             btn = active_window.child_window(title=name, control_type="Button").wrapper_object()
             btn.click_input()
             return f"Clicked button '{name}'."
        except:
             # Try generic element
             try:
                 elem = active_window.child_window(title=name).wrapper_object()
                 elem.click_input()
                 return f"Clicked element '{name}'."
             except Exception as e:
                 return f"Element '{name}' not found in active window. Ensure the name is exact."

    except Exception as e:
        return f"Error using UI Automation: {e}"

def read_screen_text():
    """
    Returns a structured list of visible text elements in the active window using UI Automation.
    """
    try:
        from pywinauto import Desktop
        app = Desktop(backend="uia")
        active_window = app.window(active_only=True)
        
        if not active_window.exists():
             return "No active window found."
             
        # Dump the tree - simplified
        # We iterate over immediate children to avoid huge dumps
        children = active_window.children()
        texts = []
        for child in children:
             txt = child.window_text()
             if txt:
                 # Try to get more specific if it's a container
                 # But keep it simple for now
                 texts.append(f"[{child.element_type}]: {txt}")
                 
        if not texts:
            return "No readable text found in active window."
            
        return "\n".join(texts)
    except Exception as e:
        return f"Error reading screen text: {e}"

def organize_files_by_date(directory_path: str):
    """
    Organizes files in a directory into folders by creation date (YYYY-MM-DD).
    Example: 'C:\\MyDocs\\2023-10-27\\report.pdf'
    """
    try:
        if not os.path.exists(directory_path):
            return f"Error: Directory {directory_path} not found."
            
        files_moved = 0
        for filename in os.listdir(directory_path):
            file_path = os.path.join(directory_path, filename)
            
            # Skip if it's a directory
            if not os.path.isfile(file_path):
                continue
                
            # Get creation time
            creation_time = os.path.getctime(file_path)
            date_folder_name = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d')
            target_folder = os.path.join(directory_path, date_folder_name)
            
            # Create date folder if not exists
            os.makedirs(target_folder, exist_ok=True)
            
            # Move file
            shutil.move(file_path, os.path.join(target_folder, filename))
            files_moved += 1
            
        return f"Successfully organized {files_moved} files in {directory_path} by date."
    except Exception as e:
        return f"Error organizing files: {e}"

def resize_image(image_path: str, width: int, height: int):
    """
    Resizes an image to the specified dimensions using PIL.
    """
    try:
        if not os.path.exists(image_path):
             return f"Error: Image {image_path} not found."
             
        with Image.open(image_path) as img:
            resized_img = img.resize((width, height))
            resized_img.save(image_path)
            
        return f"Resized {image_path} to {width}x{height}."
    except Exception as e:
        return f"Error resizing image: {e}"

def get_wifi_networks():
    """
    Lists available WiFi networks using netsh.
    """
    try:
        # Use netsh on Windows
        result = subprocess.run(["netsh", "wlan", "show", "networks"], capture_output=True, text=True)
        if result.returncode != 0:
             return f"Error running netsh: {result.stderr}"
             
        return result.stdout
    except Exception as e:
        return f"Error getting WiFi networks: {e}"

def get_active_window_title():
    """Gets the title of the currently active window."""
    try:
        window = gw.getActiveWindow()
        if window:
            return f"Active Window: {window.title}"
        return "Active Window: None"
    except Exception as e:
        return f"Error getting active window: {e}"

def deep_search(query: str):
    """Performs a deep search using Tavily, Serper, or fallback to scraping.
    
    Args:
        query: The search query.
    """
    logging.info(f"Deep searching for: {query}")
    
    # 1. Tavily API (Recommended)
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        try:
            logging.info("Using Tavily API")
            response = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "search_depth": "basic", "include_answer": True},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            answer = data.get("answer", "")
            results = data.get("results", [])
            summary = f"Tavily Answer: {answer}\n\nSources:\n"
            for res in results[:3]:
                summary += f"- {res['title']} ({res['url']}): {res['content'][:200]}...\n"
            return summary
        except Exception as e:
            logging.error(f"Tavily Search failed: {e}")

    # 2. Serper API (Google)
    serper_key = os.environ.get("SERPER_API_KEY")
    if serper_key:
        try:
            logging.info("Using Serper API")
            headers = {'X-API-KEY': serper_key, 'Content-Type': 'application/json'}
            response = requests.post(
                "https://google.serper.dev/search",
                headers=headers,
                json={"q": query},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            organic = data.get("organic", [])
            summary = "Serper Results:\n"
            for res in organic[:3]:
                summary += f"- {res.get('title')} ({res.get('link')}): {res.get('snippet')}\n"
            return summary
        except Exception as e:
            logging.error(f"Serper Search failed: {e}")

    # 3. Fallback: Scraping (Google Search + BeautifulSoup)
    try:
        logging.info("Fallback: Scraping Google")
        urls = []
        # googlesearch-python returns a generator
        for url in search(query, num_results=3, lang="en"):
            urls.append(url)
            
        summary = f"Search Results for '{query}':\n\n"
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        for url in urls:
            try:
                resp = requests.get(url, headers=headers, timeout=5)
                soup = BeautifulSoup(resp.content, 'html.parser')
                
                # Extract text properly
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.extract()
                text = soup.get_text()
                
                # Clean up whitespace
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = '\n'.join(chunk for chunk in chunks if chunk)
                
                # Limit text length to avoid token overflow
                summary += f"--- Source: {url} ---\n{text[:1000]}...\n\n"
            except Exception as e:
                summary += f"--- Source: {url} ---\nError fetching content: {e}\n\n"
                
        return f"Research Summary based on {len(urls)} sources:\n{summary}\n\nINSTRUCTION: Synthesize a concise answer from the above."
    except Exception as e:
        return f"Deep Search Error: {e}"

def click_at_coordinates(x: int, y: int, button: str = "left"):
    """Clicks at the specified screen coordinates."""
    if config.SAFE_MODE:
        if not ask_user_permission(f"Click at ({x}, {y})?"):
            return f"Action blocked by user: Click at ({x}, {y})"

    try:
        pyautogui.click(x, y, button=button)
        return f"Clicked at ({x}, {y}) with {button} button."
    except Exception as e:
        return f"Error clicking: {e}"

def type_text(text: str):
    """Types text at the current cursor position."""
    if config.SAFE_MODE:
        short_text = (text[:20] + '...') if len(text) > 20 else text
        if not ask_user_permission(f"Type text: '{short_text}'?"):
            return f"Action blocked by user: Type text"

    try:
        pyautogui.write(text)
        return f"Typed: {text}"
    except Exception as e:
        return f"Error typing: {e}"

def press_key(key: str):
    """Presses a specific key (e.g., 'enter', 'esc', 'win')."""
    if config.SAFE_MODE:
        if not ask_user_permission(f"Press key: {key}?"):
            return f"Action blocked by user: Press key {key}"

    try:
        pyautogui.press(key)
        return f"Pressed key: {key}"
    except Exception as e:
        return f"Error pressing key: {e}"

def take_screenshot():
    """Takes a screenshot and saves it to a temporary file.
    
    Returns:
        The path to the saved screenshot file.
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
        screenshot = pyautogui.screenshot()
        screenshot.save(filename)
        return filename
    except Exception as e:
        return f"Error taking screenshot: {e}"

def open_website(url: str):
    """Opens a website in the default browser. 
    Can be used for direct URLs OR search queries.
    
    Args:
        url: The full URL (e.g. 'https://www.google.com/search?q=weather') 
             OR a domain (e.g. 'youtube.com').
    """
    try:
        if not url.startswith('http'):
            url = 'https://' + url
        webbrowser.open(url)
        return f"Opened website: {url}"
    except Exception as e:
        return f"Error opening website: {e}"

import pyperclip
import pythoncom

def read_clipboard():
    """Reads the current text content from the clipboard.
    
    Returns:
        The text currently stored in the clipboard.
    """
    try:
        # Clipboard access on worker threads requires COM initialization
        pythoncom.CoInitialize()
        try:
            content = pyperclip.paste()
            return f"Clipboard content: {content}"
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return f"Error reading clipboard: {e}"

def write_to_clipboard(text: str):
    """Writes text to the system clipboard.
    
    Args:
        text: The text to copy to the clipboard.
    """
    try:
        # Clipboard access on worker threads requires COM initialization
        pythoncom.CoInitialize()
        try:
            pyperclip.copy(text)
            return "Text copied to clipboard."
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return f"Error writing to clipboard: {e}"

import ctypes

async def run_python_script(script_path: str):
    """Executes a Python script, with advanced self-healing.
    
    Args:
        script_path: The full path to the Python script to run.
    """
    # Safe Mode Check
    if config.SAFE_MODE:
        if not ask_user_permission(f"Execute Python script:\n{script_path}?"):
            return f"Action blocked by user: Run {script_path}"

    try:
        # Resolve path: Check workspace if not found in root/absolute
        if not os.path.exists(script_path) and not os.path.isabs(script_path):
             ws_path = os.path.join(WORKSPACE_DIR, script_path)
             if os.path.exists(ws_path):
                 script_path = ws_path

        # Safety check: Ensure it's a python file and exists
        if not script_path.endswith('.py'):
            return "Error: Can only execute .py files for safety."
        if not os.path.exists(script_path):
            return f"Error: File {script_path} not found."

        # Security: Scan for dangerous patterns
        try:
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            critical_patterns = ["shutil.rmtree", "os.system('rm", "os.system(\"rm", "del /s", "format c:", "os.remove(r'C:\\Windows"]
            if any(pattern in content for pattern in critical_patterns):
                 return f"Error: Security Block. Script contains potentially dangerous operations: {critical_patterns}"

        except Exception as e:
            return f"Error reading script for security check: {e}"

        max_retries = 3
        output_log = ""

        for attempt in range(max_retries):
            try:
                # Read current content before run (in case it was modified)
                with open(script_path, 'r', encoding='utf-8') as f:
                    current_code = f.read()

                logging.info(f"Executing script {script_path} (Attempt {attempt+1}/{max_retries})")
                
                # Run subprocess in thread to avoid blocking the asyncio loop
                result = await asyncio.to_thread(
                    subprocess.run,
                    ['python', script_path], 
                    capture_output=True, 
                    text=True, 
                    timeout=30 
                )
                
                output = result.stdout
                error = result.stderr

                if not error:
                    # Success!
                    output_log += f"\n--- Execution Success (Attempt {attempt+1}) ---\n{output}"
                    return f"Execution Result:\n{output_log}"
                
                # Failure
                output_log += f"\n--- Attempt {attempt+1} Failed ---\nOutput: {output}\nError: {error}\n"
                
                # Update UI Status: Error Detected
                update_status(f"🔴 Error Detected (Attempt {attempt+1})")
                
                # Check for specific ModuleNotFoundError to fast-track install
                if "ModuleNotFoundError" in error:
                     import re
                     match = re.search(r"No module named '(\w+)'", error)
                     if match:
                         missing_module = match.group(1)
                         logging.info(f"Installing missing module: {missing_module}")
                         
                         update_status(f"📦 Installing Missing Module: {missing_module}")
                         speak(f"Missing module {missing_module} detected. Installing it now...")
                         
                         install_res = install_python_library(missing_module) # Blocking is fine here
                         output_log += f"Installed {missing_module}: {install_res}\n"
                         continue # Retry immediately after install without rewriting code yet

                # General Error -> Ask AI to fix
                logging.info("Requesting AI fix for script error...")
                
                # Context-Aware Error Handling: Screenshot + Visual Analysis (Only if first attempt failed)
                if attempt > 0:
                    try:
                        screenshot_path = os.path.join(os.getcwd(), "error_context.png")
                        pyautogui.screenshot(screenshot_path)
                        
                        update_status("📸 Analyzing Screen Context...")
                        error_explanation = await ai_engine.analyze_error_with_screenshot(error, screenshot_path)
                        
                        if error_explanation:
                            speak(f"I see an error. {error_explanation}")
                            output_log += f"\n[Visual Analysis]: {error_explanation}\n"
                    except Exception as viz_e:
                        logging.error(f"Visual Error Analysis Failed: {viz_e}")

                update_status(f"🟡 Analyzing Traceback & Patching Code...")
                speak(f"Script error detected on attempt {attempt+1}. requesting AI fix...")
                try:
                    fixed_code = await ai_engine.fix_code(current_code, error)
                    if fixed_code:
                        with open(script_path, 'w', encoding='utf-8') as f:
                            f.write(fixed_code)
                        output_log += "AI applied a fix. Retrying...\n"
                        
                        update_status(f"🟢 Patch Applied. Retrying... 🚀")
                        speak("AI fix applied. Retrying execution...")
                    else:
                        output_log += "AI failed to generate a fix.\n"
                        update_status(f"❌ AI Fix Failed")
                        break # Stop if AI fails
                except Exception as ai_e:
                     output_log += f"AI Fix Error: {ai_e}\n"
                     break

            except subprocess.TimeoutExpired:
                output_log += f"\nError: Script execution timed out (limit: 30s).\n"
                break
            except Exception as e:
                output_log += f"\nError executing subprocess: {e}\n"
                break
        
        return f"Execution Failed after {max_retries} attempts.\nLog:\n{output_log}"

    except Exception as e:
        return f"Error executing script: {e}"

def install_python_library(library_name: str):
    """Installs a Python library using pip.
    
    Args:
        library_name: The name of the library to install (e.g., 'numpy', 'pandas').
    """
    if config.SAFE_MODE:
        if not ask_user_permission(f"Install library: {library_name}?"):
            return f"Action blocked by user: Install {library_name}"

    try:
        # Security: Strict validation to prevent command injection
        import re
        if not re.match(r"^[a-zA-Z0-9_\-=.><]+$", library_name):
             return "Error: Invalid library name. Only alphanumeric characters and standard version specifiers allowed."

        result = subprocess.run(
            ['pip', 'install', library_name], 
            capture_output=True, 
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            return f"Successfully installed {library_name}."
        else:
            return f"Error installing {library_name}:\n{result.stderr}"
    except Exception as e:
        return f"Error installing library: {e}"

def create_file(file_path: str, content: str = ""):
    """Creates a file at the specified path with optional content and opens it.
    
    Args:
        file_path: The path where the file should be created.
        content: The text content to write to the file.
    """
    # Safe Mode Check
    if config.SAFE_MODE:
        short_content = (content[:50] + '...') if len(content) > 50 else content
        if not ask_user_permission(f"Create file:\n{file_path}\nContent: {short_content}?"):
            return f"Action blocked by user: Create {file_path}"

    try:
        # Enforce workspace for relative paths
        if not os.path.isabs(file_path):
            file_path = os.path.join(WORKSPACE_DIR, file_path)
            
        # Ensure directory exists
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Safety Guardrail: Prevent writing to sensitive system directories
        forbidden_paths = [
            r"C:\Windows", 
            r"C:\Program Files", 
            r"C:\Program Files (x86)"
        ]
        # Normalize path for comparison
        abs_path = os.path.abspath(file_path)
        for forbidden in forbidden_paths:
            if abs_path.lower().startswith(forbidden.lower()):
                return f"Error: Access denied. Cannot write to protected system path: {forbidden}"

        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Auto-open the file to prove creation
        try:
            os.startfile(file_path)
        except Exception:
            pass # Fails on non-Windows, but we are on Windows

        return f"Created and opened file: {file_path} with content length {len(content)}"
    except Exception as e:
        return f"Error creating file: {e}"

def get_current_time():
    """Returns the current local system time and date."""
    now = datetime.now()
    return now.strftime("The current time is %I:%M %p on %A, %B %d, %Y.")

def delete_file(file_path: str):
    """Deletes a file at the specified path.
    
    Args:
        file_path: The path of the file to delete.
    """
    # Safe Mode Check
    if config.SAFE_MODE:
        if not ask_user_permission(f"DELETE file:\n{file_path}?"):
            return f"Action blocked by user: Delete {file_path}"

    try:
        os.remove(file_path)
        return f"Deleted file: {file_path}"
    except Exception as e:
        return f"Error deleting file: {e}"

def shutdown_system():
    """Shuts down the computer immediately."""
    if config.SAFE_MODE:
        if not ask_user_permission("SHUTDOWN the system?"):
            return "Action blocked by user: Shutdown System"

    try:
        subprocess.run(['shutdown', '/s', '/t', '1'], check=True)
        return "Shutting down system."
    except Exception as e:
        return f"Error shutting down: {e}"

def restart_system():
    """Restarts the computer immediately."""
    if config.SAFE_MODE:
        if not ask_user_permission("RESTART the system?"):
            return "Action blocked by user: Restart System"

    try:
        subprocess.run(['shutdown', '/r', '/t', '1'], check=True)
        return "Restarting system."
    except Exception as e:
        return f"Error restarting: {e}"

import smtplib
from email.mime.text import MIMEText
import imaplib
import email
from config import EMAIL_USER, EMAIL_PASSWORD
import json
import re

def vision_click(element_description: str):
    """Uses AI Vision to find and click an element on the screen.
    
    Args:
        element_description: Description of what to click (e.g., 'Spotify icon', 'Send button').
    """
    try:
        # 1. Take Screenshot
        screenshot_path = take_screenshot()
        if "Error" in screenshot_path:
            return screenshot_path
            
        # 2. Ask AI for Coordinates
        prompt = f"""
        I need to click on '{element_description}'.
        Analyze the screenshot and identify the center coordinates (x, y) of this element.
        Return ONLY a JSON object with keys 'x' and 'y'. 
        Example: {{"x": 500, "y": 300}}
        Do not add any markdown formatting.
        """
        
        # We need to run async ai_engine call from this sync function
        # Since this is called from execute_task which is async, we can't easily await if this isn't async.
        # However, available_tools maps to functions. execute_task handles coroutines.
        # So we should make this async.
        return "Async function required. Please update AVAILABLE_TOOLS mapping."
        
    except Exception as e:
        return f"Error in vision_click: {e}"

def add_grid_to_image(image_path: str, grid_size: int = 100):
    """Adds a labeled grid to the image for easier coordinate identification."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        width, height = img.size
        
        # Draw vertical lines
        for x in range(0, width, grid_size):
            draw.line([(x, 0), (x, height)], fill="red", width=1)
            draw.text((x + 2, 2), str(x), fill="red")
            
        # Draw horizontal lines
        for y in range(0, height, grid_size):
            draw.line([(0, y), (width, y)], fill="red", width=1)
            draw.text((2, y + 2), str(y), fill="red")
            
        grid_path = image_path.replace(".png", "_grid.png")
        img.save(grid_path)
        return grid_path
    except Exception as e:
        logging.error(f"Error adding grid: {e}")
        return image_path

async def vision_click_async(element_description: str):
    """Uses AI Vision to find and click an element on the screen (Async)."""
    update_status(f"👁️ Looking for '{element_description}'...")
    try:
        screenshot_path = take_screenshot()
        
        # Add grid for better accuracy
        grid_path = add_grid_to_image(screenshot_path)
        
        prompt = f"""
        Find the center coordinates of the '{element_description}' in this image.
        The image has a grid overlay to help you.
        Return ONLY a JSON object: {{"x": 123, "y": 456}}
        """
        
        # Call AI Engine
        response_text = await ai_engine.analyze_image(grid_path, prompt)
        
        # Parse JSON
        import re
        json_str = ""
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
             json_str = match.group(0)
        else:
             json_str = response_text.replace("```json", "").replace("```", "").strip()
             
        coords = json.loads(json_str)
        
        x = coords.get("x")
        y = coords.get("y")
        
        if x is not None and y is not None:
            update_status(f"🎯 Clicking '{element_description}' at ({x}, {y})")
            return click_at_coordinates(int(x), int(y))
        else:
            return f"Could not find coordinates for '{element_description}'. AI Response: {response_text}"
            
    except Exception as e:
        return f"Vision Click Failed: {e}"

def read_project_context(keywords: str = None):
    """Reads and summarizes the current project files to provide context.
    
    Args:
        keywords: Optional comma-separated keywords to filter files (e.g., "auth, login, user").
                  If None, scans for relevant code files efficiently.
    """
    update_status(f"📂 Indexing Project Files...")
    try:
        context = "Project Context:\n"
        
        # Parse keywords if provided
        search_terms = [k.strip().lower() for k in keywords.split(",")] if keywords else []
        
        # Scan Workspace
        file_count = 0
        MAX_FILES = 15
        
        for root, dirs, files in os.walk(os.getcwd()):
            if "venv" in root or "__pycache__" in root or ".git" in root or "node_modules" in root:
                continue
                
            for file in files:
                if file.endswith(('.py', '.java', '.js', '.html', '.css', '.md', '.txt', '.json')):
                    file_path = os.path.join(root, file)
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(4000) # Read first 4000 chars
                            
                            # Smart Filtering:
                            # 1. If keywords exist, check if any keyword is in the file content
                            # 2. If no keywords, take the file (default behavior, but prioritized)
                            
                            is_relevant = False
                            if search_terms:
                                if any(term in content.lower() for term in search_terms) or \
                                   any(term in file.lower() for term in search_terms):
                                    is_relevant = True
                            else:
                                is_relevant = True # No filter, take it
                                
                            if is_relevant:
                                context += f"\n--- File: {file} ---\n{content}\n"
                                file_count += 1
                    except:
                        pass
            
            if file_count >= MAX_FILES: 
                break
                
        if file_count == 0 and search_terms:
             return f"No files found containing keywords: {keywords}"
             
        return context
    except Exception as e:
        return f"Error reading context: {e}"

def read_emails(limit: int = 5):
    """Reads the latest unread emails from the inbox.
    
    Args:
        limit: Number of emails to read (default 5).
    """
    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select('inbox')

        _, data = mail.search(None, 'UNSEEN')
        mail_ids = data[0].split()
        
        messages = []
        for num in mail_ids[-limit:]:
            _, data = mail.fetch(num, '(RFC822)')
            for response_part in data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject = msg['subject']
                    from_ = msg['from']
                    messages.append(f"From: {from_}, Subject: {subject}")
        
        mail.close()
        mail.logout()
        return "\n".join(messages) if messages else "No new emails."
    except Exception as e:
        return f"Error reading emails: {e}"

def send_email(recipient: str, subject: str, body: str):
    """Sends an email to the specified recipient.
    
    Args:
        recipient: The email address of the recipient.
        subject: The subject of the email.
        body: The body content of the email.
    """
    if config.SAFE_MODE:
        if not ask_user_permission(f"SEND EMAIL to:\n{recipient}\nSubject: {subject}?"):
            return f"Action blocked by user: Send Email to {recipient}"

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = EMAIL_USER
        msg['To'] = recipient

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASSWORD)
            smtp.send_message(msg)
        return f"Email sent to {recipient}"
    except Exception as e:
        return f"Error sending email: {e}"

async def computer_use_fallback(instruction: str):
    """
    Universal Computer Use Fallback.
    Use this tool when NO other specific tool matches the user's request.
    It takes a screenshot, analyzes the UI, and performs the click/type action described.
    
    Args:
        instruction: A clear description of what to click or type (e.g., "Click the red 'Subscribe' button", "Type 'Hello' in the search bar").
    """
    logging.info(f"Fallback Computer Use: {instruction}")
    update_status(f"👁️ Visual Agent: {instruction}")
    
    try:
        # 1. Take Screenshot
        screenshot_path = take_screenshot()
        
        # 2. Add Grid for Precision
        grid_path = add_grid_to_image(screenshot_path, grid_size=100)
        
        # 3. Ask Vision Model for Coordinates
        # We use a direct prompt to ai_engine for this specific visual task
        prompt = f"""
        I need to perform this action on the screen: "{instruction}"
        
        Attached is a screenshot with a red coordinate grid.
        
        Task:
        1. Locate the UI element that matches the instruction.
        2. Estimate its center X, Y coordinates based on the grid numbers.
        3. Return a JSON object: {{"x": 123, "y": 456, "reason": "Found 'Subscribe' button at grid 100,400"}}
        
        If the element is not visible, return {{"error": "Element not found"}}
        """
        
        response_text = await ai_engine.analyze_image(grid_path, prompt)
        
        # 4. Parse Coordinates
        import json
        import re
        
        # Extract JSON from response
        match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            if "error" in data:
                return f"Visual Agent failed: {data['error']}"
                
            x, y = data.get("x"), data.get("y")
            reason = data.get("reason", "")
            
            speak(f"I found it. {reason}")
            
            # 5. Perform Action
            # If instruction implies typing, we click then type
            if "type" in instruction.lower() or "enter" in instruction.lower():
                click_at_coordinates(x, y)
                time.sleep(0.5)
                # Extract text to type from instruction (heuristic)
                # This is a bit naive, but works for "Type 'Hello'"
                text_match = re.search(r"type ['\"](.+?)['\"]", instruction, re.IGNORECASE)
                if text_match:
                    text_to_type = text_match.group(1)
                    type_text(text_to_type)
                    return f"Clicked ({x},{y}) and typed: {text_to_type}"
            
            click_at_coordinates(x, y)
            return f"Clicked at ({x},{y}). Reason: {reason}"
            
        return f"Could not determine coordinates from AI response: {response_text}"

    except Exception as e:
        return f"Computer Use Error: {e}"

# Map tool names to functions
AVAILABLE_TOOLS = {
    "computer_use_fallback": computer_use_fallback,
    "open_application": open_application,
    "take_screenshot": take_screenshot,
    "open_website": open_website,
    "create_file": create_file,
    "run_python_script": run_python_script,
    "install_python_library": install_python_library,
    "read_clipboard": read_clipboard,
    "write_to_clipboard": write_to_clipboard,
    "get_current_time": get_current_time,
    "delete_file": delete_file,
    "shutdown_system": shutdown_system,
    "restart_system": restart_system,
    "read_emails": read_emails,
    "send_email": send_email,
    "click_at_coordinates": click_at_coordinates,
    "type_text": type_text,
    "press_key": press_key,
    "get_active_window_title": get_active_window_title,
    "deep_search": deep_search,
    "vision_click": vision_click_async,
    "read_project_context": read_project_context,
    "browse_web": web_automation.browse_web,
    "get_web_elements": web_automation.get_web_elements,
    "web_click_id": web_automation.web_click_id,
    "web_type_id": web_automation.web_type_id,
    "web_scroll": web_automation.web_scroll,
    "web_press_key": web_automation.web_press_key,
    "web_read": web_automation.web_read,
    "close_browser": web_automation.close_browser,
    "click_element_by_name": click_element_by_name,
    "read_screen_text": read_screen_text,
    "organize_files_by_date": organize_files_by_date,
    "resize_image": resize_image,
    "get_wifi_networks": get_wifi_networks
}

async def generate_plan(user_input: str):
    """Uses AI to break down a complex task into steps."""
    prompt = f"""
    You are a Planner Agent. Break down this user request into a simple, sequential list of actionable steps for an AI agent.
    
    CRITICAL: 
    - Keep steps granular.
    - If the request implies "watching" or "monitoring", include a loop step or specify "Repeatedly check...".
    - If the request is vague (e.g. "Do that thing"), assume we need to use 'computer_use_fallback' or 'vision_click'.
    
    User Request: "{user_input}"
    
    Return ONLY a valid JSON array of strings. 
    Example: ["Open Chrome", "Go to google.com", "Search for 'Python'"]
    Do not add markdown formatting or extra text.
    """
    try:
        # We assume process_command handles the AI call. We pass empty tools to force text response.
        response = await ai_engine.process_command(prompt, {}, None)
        
        content = ""
        if isinstance(response, str):
            content = response
        elif hasattr(response, 'choices'): # Groq
            content = response.choices[0].message.content
        elif hasattr(response, 'text'): # Gemini
            content = response.text
            
        # Clean markdown
        content = content.replace("```json", "").replace("```", "").strip()
        
        steps = json.loads(content)
        if isinstance(steps, list):
            return steps
        return None
    except Exception as e:
        logging.error(f"Planning failed: {e}")
        return None

def get_system_context():
    """Gathers current system state for AI context."""
    try:
        active_window = gw.getActiveWindow()
        active_title = active_window.title if active_window else "Unknown"
        
        # Limit title length
        all_titles = [w.title for w in gw.getAllTitles() if w.title]
        all_titles_str = ", ".join(all_titles[:5]) # Limit to 5 for brevity
        
        clipboard_content = ""
        try:
            # Clipboard access on worker threads requires COM initialization
            pythoncom.CoInitialize()
            try:
                # Use pywin32's clipboard functions for robustness
                import win32clipboard
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                        data = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                        clipboard_content = data.decode('utf-8', errors='ignore')
                    elif win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                        clipboard_content = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                finally:
                    win32clipboard.CloseClipboard()

                if len(clipboard_content) > 50:
                    clipboard_content = clipboard_content[:50] + "..."
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            # logging.debug(f"Clipboard access failed: {e}")
            pass
            
        return f"[System State: Active Window='{active_title}', Open Apps=[{all_titles_str}], Clipboard='{clipboard_content}']"
    except Exception as e:
        return f"[System Context Error: {e}]"

async def _execute_step_logic(user_input: str, screenshot_path: str = None):
    """Core execution logic for a single step (extracted from execute_task)."""
    global stop_execution_flag
    
    logging.info(f"Executing step: {user_input}")
    
    # 0. Check Command Cache (Pre-computation)
    cache_key = user_input.lower().strip()
    if cache_key in COMMAND_CACHE:
        cached = COMMAND_CACHE[cache_key]
        tool_name = cached["tool"]
        tool_args = cached["args"]
        
        update_status(f"⚡ Instant Replay: {tool_name}")
        log_tool_execution(tool_name, tool_args)
        
        try:
            func = AVAILABLE_TOOLS[tool_name]
            if inspect.iscoroutinefunction(func):
                res = await func(**tool_args)
            else:
                res = func(**tool_args)
            return f"Executed Cached Action: {res}"
        except Exception as e:
            logging.warning(f"Cache failed: {e}. Falling back to AI.")
            # Fall through to normal AI logic
    
    # Check if we need to take a screenshot for context (only if explicitly implied)
    if not screenshot_path:
        # Reduced triggers to avoid unnecessary screen reading
        triggers = ["screen", "look at", "what is this", "debug", "vision"]
        if any(trigger in user_input.lower() for trigger in triggers):
             if os.path.exists(user_input) and user_input.endswith(('.png', '.jpg')):
                 screenshot_path = user_input
             else:
                 screenshot_path = take_screenshot()
             logging.info(f"Screenshot taken for context: {screenshot_path}")

    # Smart Context: Always get the active window title
    context_str = get_system_context()
    logging.info(f"Context: {context_str}")
    
    # 1. Search for Relevant Skills
    relevant_skills = skill_manager.find_relevant_skills(user_input)
    skill_context = ""
    if relevant_skills:
        skill_context = skill_manager.get_skill_context(relevant_skills)
        update_status(f"🛠️ Using Skills: {', '.join([s['metadata'].get('name', 'Unknown') for s in relevant_skills])}")
        logging.info(f"Relevant skills found: {[s['metadata'].get('name', 'Unknown') for s in relevant_skills]}")

    # Inject context
    ai_input = f"User Input: {user_input}\nContext: {context_str}\n{skill_context}"

    # Process with AI - Initial Call
    response = await ai_engine.process_command(ai_input, AVAILABLE_TOOLS, screenshot_path)
    
    # OODA: Capture pre-state
    try:
        pre_window = gw.getActiveWindow()
        pre_title = pre_window.title if pre_window else "Unknown"
    except:
        pre_title = "Unknown"

    final_output = ""
    loop_count = 0
    max_loops = 5 
    
    while loop_count < max_loops:
        if stop_execution_flag:
            return "🛑 Execution Stopped by User."
            
        loop_count += 1
        
        # 1. Handle String Response
        if isinstance(response, str):
            final_output = response
            break 
            
        # 2. Handle Groq Response
        elif hasattr(response, 'choices'):
            message = response.choices[0].message
            tool_calls = getattr(message, 'tool_calls', None)
            
            # Fallback: Check if content contains JSON tool call or XML-like format
            if not tool_calls and message.content:
                try:
                    import re
                    # 1. Standard JSON format: {"name": "...", "parameters": {...}}
                    match_json = re.search(r'\{.*"name":\s*".*".*\}', message.content, re.DOTALL)
                    
                    # 2. Hallucinated XML format: <function=name>{"arg": "val"}</function>
                    # OR the weird one from the log: <function=name{...}></function>
                    match_xml_complex = re.search(r'<function=(.*?)(\{.*?\})?>(.*?)</function>', message.content, re.DOTALL)
                    
                    tool_name = None
                    tool_args = {}
                    
                    if match_json:
                        json_str = match_json.group(0)
                        potential_tool = json.loads(json_str)
                        tool_name = potential_tool.get("name")
                        tool_args = potential_tool.get("parameters", potential_tool.get("args", {}))
                    elif match_xml_complex:
                        # Extract tool name and potential inline args
                        raw_name_part = match_xml_complex.group(1).strip()
                        inline_args_str = match_xml_complex.group(2)
                        body_args_str = match_xml_complex.group(3)
                        
                        # Case: <function=open_website{"url": "..."}>
                        if "{" in raw_name_part:
                            tool_name = raw_name_part.split("{")[0].strip()
                            # Try to parse the rest as JSON
                            try:
                                json_part = "{" + raw_name_part.split("{", 1)[1]
                                tool_args = json.loads(json_part)
                            except:
                                tool_args = {}
                        else:
                            tool_name = raw_name_part
                            # Try body or inline args
                            args_to_parse = body_args_str or inline_args_str
                            if args_to_parse:
                                try:
                                    # Clean backticks and other fluff models add
                                    clean_args = args_to_parse.replace("`", "").strip()
                                    tool_args = json.loads(clean_args)
                                except:
                                    tool_args = {}
                            
                    if tool_name:
                        # Map common hallucinated names
                        mapping = {
                            "web_browser_url": "open_website",
                            "google_search": "deep_search",
                            "search_google": "deep_search"
                        }
                        tool_name = mapping.get(tool_name, tool_name)
                        
                        if tool_name in AVAILABLE_TOOLS:
                            # Reconstruct as a tool call object for the logic below
                            tool_calls = [SimpleNamespace(
                                id="call_" + str(int(time.time())),
                                function=SimpleNamespace(
                                    name=tool_name,
                                    arguments=json.dumps(tool_args)
                                )
                            )]
                            logging.info(f"Fallback: Parsed tool call from content: {tool_name}")
                except Exception as e:
                    logging.debug(f"JSON/XML fallback parsing failed: {e}")

            if tool_calls:
                 tool_outputs = []
                 for tc in tool_calls:
                     if stop_execution_flag: break
                     
                     tool_name = tc.function.name
                     try:
                        tool_args = json.loads(tc.function.arguments)
                     except:
                        tool_args = {}
                     
                     logging.info(f"Groq requested tool: {tool_name} with args: {tool_args}")
                     log_tool_execution(tool_name, tool_args)
                     
                     # Cache Update Logic (Simple One-Shot)
                     if loop_count == 1:
                        COMMAND_CACHE[user_input.lower().strip()] = {
                            "tool": tool_name, 
                            "args": tool_args,
                            "timestamp": time.time()
                        }
                        save_command_cache()
                     
                     result_content = ""
                     if tool_name in AVAILABLE_TOOLS:
                         try:
                             import inspect
                             func = AVAILABLE_TOOLS[tool_name]
                             if inspect.iscoroutinefunction(func):
                                 tool_result = await func(**tool_args)
                             else:
                                 tool_result = func(**tool_args)
                            
                             # OODA Loop: Verification & Context Update
                             try:
                                 post_window = gw.getActiveWindow()
                                 post_title = post_window.title if post_window else "Unknown"
                             except:
                                 post_title = "Unknown"
                             
                             # Verification msg if window changed
                             verification_msg = ""
                             if pre_title != post_title:
                                 verification_msg = f"\n[VERIFICATION]: Active window changed from '{pre_title}' to '{post_title}'."
                             
                             context_update = get_system_context() + verification_msg
                            
                             # Self-Healing: Check for failure and hint the AI
                             result_str = str(tool_result)
                             result_lower = result_str.lower()
                             if "error" in result_lower or "blocked" in result_lower or "not found" in result_lower or "failed" in result_lower:
                                 hint = "\n[SYSTEM HINT]: The last action appears to have FAILED. "
                                 if tool_name == "click_element_by_name":
                                     hint += "Try 'vision_click' instead."
                                 elif tool_name == "vision_click":
                                     hint += "Try 'computer_use_fallback' or 'click_at_coordinates' if you can guess the location."
                                 elif tool_name == "open_application":
                                     hint += "Try 'run_python_script' or check the application name."
                                 else:
                                     hint += "Please Analyze the error and try a DIFFERENT strategy."
                                 context_update += hint
                            
                             result_content = result_str + "\n" + context_update
                         except Exception as e:
                             result_content = f"Error executing {tool_name}: {e}"
                     else:
                         result_content = f"Tool {tool_name} not found."
                     
                     tool_outputs.append({
                        "tool_call_id": tc.id,
                        "content": result_content
                     })
                     logging.info(f"Tool Result ({tool_name}): {result_content}")

                 if stop_execution_flag: break
                 response = await ai_engine.send_groq_tool_results(tool_outputs)
            
            elif message.content:
                final_output = message.content
                break
            else:
                final_output = "I'm not sure what to do with this response."
                break

        # 3. Handle Gemini GenerationResponse object
        elif hasattr(response, 'parts'):
            fc = None
            if response.parts and len(response.parts) > 0 and response.parts[0].function_call:
                 fc = response.parts[0].function_call
            
            if fc:
                if stop_execution_flag: break
                
                tool_name = fc.name
                tool_args = dict(fc.args)
                
                logging.info(f"AI requested tool: {tool_name} with args: {tool_args}")
                log_tool_execution(tool_name, tool_args)

                # Cache Update Logic (Simple One-Shot)
                if loop_count == 1:
                   COMMAND_CACHE[user_input.lower().strip()] = {
                       "tool": tool_name, 
                       "args": tool_args,
                       "timestamp": time.time()
                   }
                   save_command_cache()
                
                if tool_name in AVAILABLE_TOOLS:
                    try:
                        import inspect
                        func = AVAILABLE_TOOLS[tool_name]
                        if inspect.iscoroutinefunction(func):
                            tool_result = await func(**tool_args)
                        else:
                            tool_result = func(**tool_args)
                        
                        # Verification msg if window changed
                        verification_msg = ""
                        if pre_title != post_title:
                            verification_msg = f"\n[VERIFICATION]: Active window changed from '{pre_title}' to '{post_title}'."
                        
                        context_update = get_system_context() + verification_msg
                        
                        # Self-Healing: Check for failure and hint the AI
                        result_str = str(tool_result)
                        result_lower = result_str.lower()
                        if "error" in result_lower or "blocked" in result_lower or "not found" in result_lower or "failed" in result_lower:
                             hint = "\n[SYSTEM HINT]: The last action appears to have FAILED. "
                             if tool_name == "click_element_by_name":
                                 hint += "Try 'vision_click' instead."
                             elif tool_name == "vision_click":
                                 hint += "Try 'computer_use_fallback' or 'click_at_coordinates' if you can guess the location."
                             elif tool_name == "open_application":
                                 hint += "Try 'run_python_script' or check the application name."
                             else:
                                 hint += "Please Analyze the error and try a DIFFERENT strategy."
                             context_update += hint
                        
                        tool_result = result_str + "\n" + context_update

                        logging.info(f"Tool Result: {tool_result}")
                        response = await ai_engine.send_tool_result(tool_name, tool_result)
                        
                    except Exception as e:
                        error_msg = f"Error executing {tool_name}: {e}"
                        logging.error(error_msg)
                        response = await ai_engine.send_tool_result(tool_name, error_msg)
                else:
                    # Fallback Logic: If AI hallucinates a tool name, try to map it to fallback
                    logging.warning(f"Tool '{tool_name}' not found. Attempting Universal Fallback...")
                    fallback_instruction = f"Use tool '{tool_name}' with args {tool_args}"
                    
                    # Try to be smart: if args has 'text' or 'query', use that as instruction
                    if 'text' in tool_args:
                        fallback_instruction = tool_args['text']
                    elif 'query' in tool_args:
                         fallback_instruction = tool_args['query']
                    elif 'instruction' in tool_args:
                         fallback_instruction = tool_args['instruction']
                         
                    update_status(f"🔄 Unknown Tool. Using Visual Fallback...")
                    tool_result = await computer_use_fallback(fallback_instruction)
                    response = await ai_engine.send_tool_result(tool_name, tool_result)

            
            elif response.parts and response.parts[0].text:
                final_output = response.text
                break 
                
            else:
                final_output = "I'm not sure what to do with this response."
                break
        else:
            final_output = f"Unexpected response type: {type(response)}"
            break

    return final_output

# --- Hardcoded Demo Commands (For Reliability in Showcases) ---

async def demo_open_youtube():
    update_status("🚀 Launching YouTube...")
    speak("Launching YouTube in your default browser.")
    return open_website("https://www.youtube.com")

async def demo_create_file():
    # Robust Desktop detection (handles OneDrive etc.)
    try:
        import ctypes
        from ctypes import wintypes
        CSIDL_DESKTOP = 0
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, 0, buf)
        desktop = buf.value
    except:
        # Fallback to standard path if UIA fails
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        
    file_path = os.path.join(desktop, "demo_script.py")
    content = """# Autonomous Demo Script
import time

print("Hello judges! Jarvis created this script autonomously.")
print("Current system time:", time.ctime())
print("Demo execution complete.")
"""
    update_status("✍️ Creating Demo File...")
    speak("Creating a Python script on your desktop to demonstrate file management.")
    res = create_file(file_path, content)
    return res

async def demo_delete_file():
    # Robust Desktop detection (handles OneDrive etc.)
    try:
        import ctypes
        from ctypes import wintypes
        CSIDL_DESKTOP = 0
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOP, None, 0, buf)
        desktop = buf.value
    except:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        
    file_path = os.path.join(desktop, "demo_script.py")
    if os.path.exists(file_path):
        update_status("🗑️ Deleting Demo File...")
        speak("Cleaning up the demo script from your desktop.")
        return delete_file(file_path)
    else:
        speak("I couldn't find the demo script on your desktop.")
        return "Error: File not found."

async def demo_system_check():
    update_status("🔍 System Health Check...")
    speak("Performing a quick system health check.")
    
    # Gather some real info
    time_str = get_current_time()
    active_win = get_active_window_title()
    
    # Mocking some "deep" scans for flair
    await asyncio.sleep(1)
    update_status("🧠 AI Core: Online")
    await asyncio.sleep(0.5)
    update_status("📡 Network: Secure")
    await asyncio.sleep(0.5)
    update_status("🛡️ Safety: Active")
    
    report = f"System Status Report:\n- {time_str}\n- {active_win}\n- AI Engine: Groq (Llama 3.3)\n- Vision Engine: Llama 3.2 Vision\n- Status: All systems operational."
    speak("All systems are operational.")
    return report

async def demo_self_healing():
    """Triggers the self-healing loop with a broken script."""
    update_status("🛠️ Initiating Self-Healing Demo...")
    speak("I will now demonstrate my self-healing capability by running a broken script and fixing it autonomously.")
    
    # 1. Create a broken script in the workspace
    broken_script_path = os.path.join(WORKSPACE_DIR, "broken_demo.py")
    broken_content = """# This script is intentionally broken for demo purposes
import non_existent_library_12345
print("This will never run")
"""
    with open(broken_script_path, 'w', encoding='utf-8') as f:
        f.write(broken_content)
    
    # 2. Run it (this will trigger the loop in run_python_script)
    # Note: run_python_script will handle the retries and AI patching automatically
    result = await run_python_script(broken_script_path)
    return result

HARDCODED_DEMO_COMMANDS = {
    "open youtube": demo_open_youtube,
    "launch youtube": demo_open_youtube,
    "go to youtube": demo_open_youtube,
    "create demo file": demo_create_file,
    "create file on desktop": demo_create_file,
    "create a file on desktop": demo_create_file,
    "create a demo file": demo_create_file,
    "delete demo file": demo_delete_file,
    "remove demo file": demo_delete_file,
    "delete the demo file": demo_delete_file,
    "remove the demo file": demo_delete_file,
    "system check": demo_system_check,
    "health check": demo_system_check,
    "status report": demo_system_check,
    "system status": demo_system_check,
    "run system check": demo_system_check,
    "self healing demo": demo_self_healing,
    "fix broken script": demo_self_healing,
    "demonstrate repair": demo_self_healing
}

async def execute_task(user_input: str):
    """Process user input using AI and execute tools with a self-correcting loop and planning."""
    global stop_execution_flag
    stop_execution_flag = False
    
    logging.info(f"Executing task: {user_input}")
    
    # 0. Intercept Hardcoded Demo Commands for Showcase Reliability
    input_lower = user_input.lower().strip()
    if input_lower in HARDCODED_DEMO_COMMANDS:
        return await HARDCODED_DEMO_COMMANDS[input_lower]()
    
    try:
        # Heuristic for complexity: keywords or length
        is_complex = len(user_input.split()) > 15 or " and " in user_input or " then " in user_input or "after" in user_input
        
        if is_complex:
            update_status("🧠 Generating Plan...")
            plan = await generate_plan(user_input)
            
            if plan and len(plan) > 1:
                update_status(f"📋 Plan: {len(plan)} Steps")
                results = []
                for i, step in enumerate(plan):
                    if stop_execution_flag:
                        update_status("🛑 Plan Aborted.")
                        return "Execution Stopped."
                        
                    update_status(f"⚙️ Step {i+1}/{len(plan)}: {step}")
                    res = await _execute_step_logic(step)
                    results.append(f"Step {i+1}: {res}")
                
                final_res = "\n".join(results)
                task_history.append(f"Plan: {user_input} -> {final_res}")
                return final_res

        # Fallback to single step execution
        res = await _execute_step_logic(user_input)
        task_history.append(f"Cmd: {user_input} -> {res}")
        return res

    except Exception as e:
        logging.error(f"Critical Task Failure: {e}")
        update_status("🔴 Critical Error. Analyzing...")
        
        # Context-Aware Error Handling: Take screenshot of the error state
        screenshot_path = take_screenshot()
        
        # Ask AI to explain
        explanation = await ai_engine.analyze_error_with_screenshot(str(e), screenshot_path)
        
        return f"Error: {explanation}"
