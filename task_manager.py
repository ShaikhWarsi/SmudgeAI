# task_manager.py
import os
import subprocess
import asyncio
import logging
import webbrowser
import shutil
import pyautogui
from datetime import datetime
import time
import ai_engine
import pygetwindow as gw
from googlesearch import search
import requests
from bs4 import BeautifulSoup
import traceback
import json
import config
import ctypes

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

task_queue = []
task_history = []

WORKSPACE_DIR = os.path.join(os.getcwd(), "workspace")
if not os.path.exists(WORKSPACE_DIR):
    os.makedirs(WORKSPACE_DIR)

def speak(text):
    print(text)
    # Ideally integrate pyttsx3 here for TTS
    pass

def _maximize_active_window():
    """Helper to maximize the active window after launch."""
    time.sleep(1.5) # Wait for window to appear
    try:
        win = gw.getActiveWindow()
        if win:
            win.maximize()
    except:
        pass

def open_application(app_name: str):
    """Opens an application using robust path finding.
    
    Args:
        app_name: The name of the application to open (e.g., 'notepad', 'calculator', 'chrome').
    """
    # Safe Mode Check
    if config.SAFE_MODE:
        # 1 = OK/Cancel. Return 1 (IDOK) or 2 (IDCANCEL)
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to open '{app_name}'?", "Safe Mode Confirmation", 1 | 0x40000) # MB_OKCANCEL | MB_TOPMOST
        if resp == 2:
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
        # Minimal blocking for UI, maybe just a quick confirmation or log
        # For a hackathon, explicit is better.
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to CLICK at ({x}, {y})?", "Safe Mode UI Interaction", 1 | 0x40000)
        if resp == 2:
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
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to TYPE:\n'{short_text}'?", "Safe Mode UI Interaction", 1 | 0x40000)
        if resp == 2:
            return f"Action blocked by user: Type text"

    try:
        pyautogui.write(text)
        return f"Typed: {text}"
    except Exception as e:
        return f"Error typing: {e}"

def press_key(key: str):
    """Presses a specific key (e.g., 'enter', 'esc', 'win')."""
    if config.SAFE_MODE:
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to PRESS KEY: {key}?", "Safe Mode UI Interaction", 1 | 0x40000)
        if resp == 2:
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

def read_clipboard():
    """Reads the current text content from the clipboard.
    
    Returns:
        The text currently stored in the clipboard.
    """
    try:
        content = pyperclip.paste()
        return f"Clipboard content: {content}"
    except Exception as e:
        return f"Error reading clipboard: {e}"

def write_to_clipboard(text: str):
    """Writes text to the system clipboard.
    
    Args:
        text: The text to copy to the clipboard.
    """
    try:
        pyperclip.copy(text)
        return "Text copied to clipboard."
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
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to EXECUTE script:\n{script_path}?", "Safe Mode Confirmation", 1 | 0x40000)
        if resp == 2:
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
                
                # Check for specific ModuleNotFoundError to fast-track install
                if "ModuleNotFoundError" in error:
                     import re
                     match = re.search(r"No module named '(\w+)'", error)
                     if match:
                         missing_module = match.group(1)
                         logging.info(f"Installing missing module: {missing_module}")
                         install_res = install_python_library(missing_module) # Blocking is fine here
                         output_log += f"Installed {missing_module}: {install_res}\n"
                         continue # Retry immediately after install without rewriting code yet

                # General Error -> Ask AI to fix
                logging.info("Requesting AI fix for script error...")
                try:
                    fixed_code = await ai_engine.fix_code(current_code, error)
                    if fixed_code:
                        with open(script_path, 'w', encoding='utf-8') as f:
                            f.write(fixed_code)
                        output_log += "AI applied a fix. Retrying...\n"
                    else:
                        output_log += "AI failed to generate a fix.\n"
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
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to INSTALL library:\n{library_name}?", "Safe Mode Confirmation", 1 | 0x40000)
        if resp == 2:
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
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to CREATE file:\n{file_path}\nContent: {short_content}", "Safe Mode Confirmation", 1 | 0x40000)
        if resp == 2:
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
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to DELETE file:\n{file_path}?", "Safe Mode Warning", 1 | 0x30 | 0x40000) # MB_OKCANCEL | MB_ICONWARNING
        if resp == 2:
            return f"Action blocked by user: Delete {file_path}"

    try:
        os.remove(file_path)
        return f"Deleted file: {file_path}"
    except Exception as e:
        return f"Error deleting file: {e}"

def shutdown_system():
    """Shuts down the computer immediately."""
    if config.SAFE_MODE:
        resp = ctypes.windll.user32.MessageBoxW(0, "Allow Jarvis to SHUTDOWN the system?", "Safe Mode Warning", 1 | 0x30 | 0x40000)
        if resp == 2:
            return "Action blocked by user: Shutdown System"

    try:
        subprocess.run(['shutdown', '/s', '/t', '1'], check=True)
        return "Shutting down system."
    except Exception as e:
        return f"Error shutting down: {e}"

def restart_system():
    """Restarts the computer immediately."""
    if config.SAFE_MODE:
        resp = ctypes.windll.user32.MessageBoxW(0, "Allow Jarvis to RESTART the system?", "Safe Mode Warning", 1 | 0x30 | 0x40000)
        if resp == 2:
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
        resp = ctypes.windll.user32.MessageBoxW(0, f"Allow Jarvis to SEND EMAIL to:\n{recipient}\nSubject: {subject}?", "Safe Mode Confirmation", 1 | 0x40000)
        if resp == 2:
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

# Map tool names to functions
AVAILABLE_TOOLS = {
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
    "deep_search": deep_search
}

async def execute_task(user_input: str):
    """Process user input using AI and execute tools with a self-correcting loop."""
    logging.info(f"Executing task: {user_input}")
    
    # Check if we need to take a screenshot for context
    screenshot_path = None
    triggers = ["screen", "look at", "what is this", "debug", "fix", "error", "failing", "click", "interact", "mute"]
    
    # Smart Context: Always get the active window title
    window_title = get_active_window_title()
    logging.info(f"Context: {window_title}")
    
    # Inject context into the user input for the AI
    # We don't change the original user_input for logging, but for the AI processing
    ai_input = f"User Input: {user_input}\nContext: {window_title}"

    if any(trigger in user_input.lower() for trigger in triggers):
        # We check if it's already a file path, otherwise take a fresh one
        if os.path.exists(user_input) and user_input.endswith(('.png', '.jpg')):
             screenshot_path = user_input
        else:
             screenshot_path = take_screenshot()
        logging.info(f"Screenshot taken for context: {screenshot_path}")

    # Process with AI - Initial Call
    response = await ai_engine.process_command(ai_input, AVAILABLE_TOOLS, screenshot_path)
    
    final_output = ""
    loop_count = 0
    max_loops = 5 # Prevent infinite loops
    
    while loop_count < max_loops:
        loop_count += 1
        
        # 1. Handle String Response (Error or direct text from process_command fallback)
        if isinstance(response, str):
            final_output = response
            break # Exit loop
            
        # 2. Handle Groq Response (Check for 'choices' attribute)
        elif hasattr(response, 'choices'):
            message = response.choices[0].message
            if message.tool_calls:
                 # Execute ALL tools requested in parallel
                 tool_outputs = []
                 for tc in message.tool_calls:
                     tool_name = tc.function.name
                     try:
                        tool_args = json.loads(tc.function.arguments)
                     except:
                        tool_args = {}
                     
                     logging.info(f"Groq requested tool: {tool_name} with args: {tool_args}")
                     
                     result_content = ""
                     if tool_name in AVAILABLE_TOOLS:
                         try:
                            # Execute the tool
                            import inspect
                            func = AVAILABLE_TOOLS[tool_name]
                            if inspect.iscoroutinefunction(func):
                                tool_result = await func(**tool_args)
                            else:
                                tool_result = func(**tool_args)
                            result_content = str(tool_result)
                         except Exception as e:
                            result_content = f"Error executing {tool_name}: {e}"
                     else:
                         result_content = f"Tool {tool_name} not found."
                     
                     tool_outputs.append({
                        "tool_call_id": tc.id,
                        "content": result_content
                     })
                     logging.info(f"Tool Result ({tool_name}): {result_content}")

                 # Send batch results back to Groq
                 response = await ai_engine.send_groq_tool_results(tool_outputs)
            
            elif message.content:
                final_output = message.content
                break # Exit loop
            else:
                final_output = "I'm not sure what to do with this response."
                break

        # 3. Handle Gemini GenerationResponse object
        elif hasattr(response, 'parts'):
            # Check for function call
            fc = None
            if response.parts and len(response.parts) > 0 and response.parts[0].function_call:
                 fc = response.parts[0].function_call
            
            if fc:
                tool_name = fc.name
                tool_args = dict(fc.args)
                
                logging.info(f"AI requested tool: {tool_name} with args: {tool_args}")
                
                if tool_name in AVAILABLE_TOOLS:
                    try:
                        # Execute the tool
                        import inspect
                        func = AVAILABLE_TOOLS[tool_name]
                        
                        if inspect.iscoroutinefunction(func):
                            tool_result = await func(**tool_args)
                        else:
                            tool_result = func(**tool_args)
                        
                        logging.info(f"Tool Result: {tool_result}")
                        
                        # FEEDBACK LOOP: Send result back to AI
                        response = await ai_engine.send_tool_result(tool_name, tool_result)
                        
                    except Exception as e:
                        error_msg = f"Error executing {tool_name}: {e}"
                        logging.error(error_msg)
                        response = await ai_engine.send_tool_result(tool_name, error_msg)
                else:
                    error_msg = f"Tool {tool_name} not found."
                    logging.error(error_msg)
                    response = await ai_engine.send_tool_result(tool_name, error_msg)
            
            # Check for text response (Final Answer)
            elif response.parts and response.parts[0].text:
                final_output = response.text
                break # Exit loop
                
            else:
                final_output = "I'm not sure what to do with this response."
                break
        else:
            final_output = f"Unexpected response type: {type(response)}"
            break

    task_history.append(f"Cmd: {user_input} -> {final_output}")
    return final_output
