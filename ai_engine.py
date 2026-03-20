import google.generativeai as genai
from google.api_core import exceptions
from config import GOOGLE_API_KEY, GROQ_API_KEY, AI_PROVIDER, GROQ_MODELS
import logging
from PIL import Image
import os
import asyncio
import inspect
import json
import groq
import time
import random
from types import SimpleNamespace

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

genai.configure(api_key=GOOGLE_API_KEY)

# Global model and chat session
model = None

async def analyze_image(image_path: str, prompt: str):
    """Analyzes an image using Vision capabilities (Prioritizes Groq Llama 3.2 Vision)."""
    global groq_client
    
    # 1. Try Groq Vision (Llama 3.2) First
    if groq_client:
        try:
            import base64
            # Load and encode image
            if not os.path.exists(image_path):
                 return "Error: Image file not found."
            
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
            logging.info("Using Groq Vision (llama-3.2-11b-vision-preview)")
            # Using Llama 3.2 Vision for Groq
            response = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model="llama-3.2-11b-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=1024
            )
            return response.choices[0].message.content
        except Exception as e:
            logging.error(f"Groq Vision analysis failed: {e}. Falling back to Gemini...")
            # Fall through to Gemini if Groq fails
            
    # 2. Fallback to Gemini Vision if Groq is unavailable or fails
    try:
        if not GOOGLE_API_KEY:
             return "Error: Neither Groq Vision nor GOOGLE_API_KEY (Gemini) are available for vision analysis."
        
        logging.info("Using Gemini Vision (gemini-1.5-flash)")
        # Initialize model for vision
        vision_model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Load image
        if not os.path.exists(image_path):
             return "Error: Image file not found."
             
        img = Image.open(image_path)
        
        # Generate content
        response = await asyncio.to_thread(vision_model.generate_content, [prompt, img])
        return response.text
    except Exception as e:
        logging.error(f"Gemini Vision analysis failed: {e}")
        return f"Error analyzing image: {e}"

chat_session = None

# Groq Globals
groq_client = None
groq_history = []
current_groq_model_index = 0
current_tools_list = []


class RateLimiter:
    _instance = None

    def __init__(self):
        self.requests_in_window = 0
        self.window_start = time.time()
        self.window_size = 60.0
        self.max_requests_per_window = 30
        self.blocked_until = 0
        self.consecutive_errors = 0
        self.max_consecutive_errors = 5

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_blocked(self) -> bool:
        if time.time() < self.blocked_until:
            return True
        return False

    def record_success(self):
        self.consecutive_errors = 0

    def record_error(self, is_rate_limit=False):
        self.consecutive_errors += 1
        if is_rate_limit or self.consecutive_errors >= self.max_consecutive_errors:
            self.blocked_until = time.time() + self._calculate_backoff()
            logging.warning(f"Rate limiter triggered. Blocked until {self.blocked_until}")

    def _calculate_backoff(self) -> float:
        base = 30.0
        exponential = min(2 ** self.consecutive_errors, 60)
        jitter = random.random() * 10
        return min(base + exponential + jitter, 300)

    def check_request(self) -> bool:
        if self.is_blocked():
            return False

        now = time.time()
        if now - self.window_start > self.window_size:
            self.requests_in_window = 0
            self.window_start = now

        if self.requests_in_window >= self.max_requests_per_window:
            self.blocked_until = now + (self.window_size - (now - self.window_start))
            logging.warning(f"Rate limit window full. Blocked until {self.blocked_until}")
            return False

        self.requests_in_window += 1
        return True

_rate_limiter = RateLimiter.get_instance()

async def _get_groq_response(messages, tools=None):
    """Internal helper to get response from Groq with model cycling and rate limiting."""
    global current_groq_model_index, groq_client

    if not _rate_limiter.check_request():
        return "Error: Rate limit exceeded. Please wait before making more requests."

    groq_tools = None
    if tools:
        groq_tools = [get_tool_schema(t) for t in tools]

    max_retries = len(GROQ_MODELS)
    start_index = current_groq_model_index

    for attempt in range(max_retries):
        model_name = GROQ_MODELS[current_groq_model_index]

        if groq_tools:
            if "llama-3.3-70b-versatile" in model_name or "gpt-oss" in model_name:
                current_groq_model_index = (current_groq_model_index + 1) % len(GROQ_MODELS)
                if current_groq_model_index == start_index:
                    break
                continue

        try:
            logging.info(f"Using Groq Model: {model_name}")

            kwargs = {
                "model": model_name,
                "messages": messages,
                "max_tokens": 4096
            }
            if groq_tools:
                kwargs["tools"] = groq_tools
                kwargs["tool_choice"] = "auto"

            completion = await asyncio.to_thread(
                groq_client.chat.completions.create,
                **kwargs
            )

            _rate_limiter.record_success()
            return completion

        except groq.RateLimitError:
            logging.warning(f"Rate limit hit for {model_name}. Cycling model...")
            _rate_limiter.record_error(is_rate_limit=True)
            current_groq_model_index = (current_groq_model_index + 1) % len(GROQ_MODELS)
            await asyncio.sleep(2)
            continue

        except Exception as e:
            logging.error(f"Groq Error ({model_name}): {e}")
            _rate_limiter.record_error()
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str or "400" in error_str or "tool_use_failed" in error_str or "not found" in error_str:
                 current_groq_model_index = (current_groq_model_index + 1) % len(GROQ_MODELS)
                 await asyncio.sleep(2)
                 continue
            return f"Error with Groq: {e}"

    return "Error: All Groq models exhausted or failed."

SYSTEM_INSTRUCTION = """
You are Jarvis, an Autonomous Agent specialized in Developer Workflows.
You are an expert pair programmer, debugger, and system architect.
Your goal is to accelerate development tasks: coding, debugging, and file management.

### TOOL CALLING PROTOCOL
- You have access to a variety of tools. Use them when needed to fulfill the user's request.
- When you use a tool, the system will provide the result.
- Once you have the final answer or have completed the task, provide a clear, concise response in natural language.
- **DO NOT** just output raw JSON unless you are specifically asked for it. Use the native tool calling mechanism.

### IDENTITY & BEHAVIOR:
- **Tone**: Professional, precise, and efficient. No fluff. Think "Senior Engineer" or "CTO".
- **Scope**: Focus strictly on software engineering tasks.
- **Honesty**:
  - You operate within a **Workspace Constraint** for this MVP.
  - For dangerous operations (file deletion, shell execution), you respect **Safe Mode** (Human-in-the-Loop).
  - If asked about production scalability, explain that a production version would use isolated Docker containers for task execution.

### CORE PROTOCOLS:

1. **Self-Healing Code Execution**:
   - If a Python script fails, READ the error in stderr.
   - ANALYZE the root cause.
   - REWRITE the script to fix the error.
   - RUN it again.
   - Repeat until success or max retries.
   - DO NOT ask for permission to fix syntax errors. JUST FIX IT.

2. **Universal Computer Use (The "Do Anything" Protocol)**:
   - If a user request matches a specific tool (e.g., "Open Chrome"), USE IT.
   - **CRITICAL**: If NO specific tool matches (e.g., "Change Discord status", "Like this post", "Crop image"), use `computer_use_fallback(instruction)`.
   - `computer_use_fallback` is your **eyes and hands**. It allows you to click/type on ANYTHING by seeing the screen.
   - NEVER say "I can't do that" because of missing tools. Use the fallback.

3. **Smart Context & RAG**:
   - You have access to the active window title. Use it to infer context.
   - Use `read_project_context(keywords="...")` to find relevant files instead of reading everything.
   - If the user says "Fix auth", call `read_project_context(keywords="auth, login")`.

4. **Robust UI Automation**:
   - **PREFERRED**: `smart_click("Save")` or `smart_click_async("Save")` - tries UIA first, then CV, then vision.
   - **ALTERNATIVE**: `click_element_by_name("Save")` (pure UIA).
   - **FALLBACK**: `vision_click_async("Save")` (LLM vision - slow).
   - **NEW**: Use `DesktopActions` for: `double_click`, `right_click`, `hover`, `drag`, `scroll`, `type_text`, `press_key`
   - **AVOID**: Blind guessing of coordinates. Use smart_click.

5. **Web Navigation (Playwright)**:
   - Use `browse_web(url)` to open a page.
   - **CRITICAL**: IMMEDIATELY call `get_web_elements()` to see the page content with IDs (e.g., "[1] Button: Login").
   - Then use `web_click_id(1)` or `web_type_id(2, "text")`.
   - DO NOT guess CSS selectors. Trust the IDs from `get_web_elements()`.
   - If the page changes, call `get_web_elements()` again to get fresh IDs.

6. **Planner Awareness**:
   - Complex tasks are automatically broken down into steps.
   - Execute the current step efficiently.
   - If a task is "Computer Use" heavy (e.g., "Find the meme and send it"), use a loop of:
     - `take_screenshot()`
     - `analyze_image()`
     - Action

7. **Tool Arguments Safety**:
   - NEVER pass code (e.g., `read_clipboard()`, `os.getcwd()`) as a function argument.
   - Arguments must be STATIC strings, integers, or booleans.
   - CORRECT: `open_website(url="https://reddit.com/user/john")`
   - INCORRECT: `open_website(url="https://reddit.com/user/" + read_clipboard())` -> This will FAIL.
   - If you need dynamic data, get it in a separate step (e.g., `clip = read_clipboard()`) then use the value.

Always output clear, executable code or direct answers.
"""

def get_tool_schema(func):
    """Converts a Python function to an OpenAI/Groq JSON schema."""
    sig = inspect.signature(func)
    doc = inspect.getdoc(func)
    
    properties = {}
    required = []
    
    for name, param in sig.parameters.items():
        if param.default == inspect.Parameter.empty:
            required.append(name)
        
        param_type = "string" # Default
        if param.annotation == int:
            param_type = "integer"
        elif param.annotation == bool:
            param_type = "boolean"
            
        properties[name] = {
            "type": param_type,
            "description": f"Parameter {name}"
        }
    
    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": doc or "",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
    }

def _message_to_dict(msg) -> dict:
    """Convert a Groq ChatCompletionMessage to a plain dict for safe history storage."""
    if isinstance(msg, dict):
        return msg
    result = {
        "role": getattr(msg, "role", "assistant"),
        "content": getattr(msg, "content", None)
    }
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        result["tool_calls"] = tool_calls
    tool_call_id = getattr(msg, "tool_call_id", None)
    if tool_call_id:
        result["tool_call_id"] = tool_call_id
    return result

def initialize_model(tools_list):
    """Initializes the AI model (Gemini or Groq) globally."""
    global model, chat_session, groq_client, groq_history
    
    if AI_PROVIDER == "groq":
        if groq_client is None:
            if not GROQ_API_KEY:
                logging.error("GROQ_API_KEY not found in config.")
                return
            try:
                groq_client = groq.Groq(api_key=GROQ_API_KEY)
                groq_history = [
                    {"role": "system", "content": SYSTEM_INSTRUCTION}
                ]
                logging.info(f"Initialized Groq Client with models: {GROQ_MODELS}")
            except Exception as e:
                logging.error(f"Failed to initialize Groq: {e}")
    else:
        # Default to Gemini
        if model is None:
            model = genai.GenerativeModel(
                'gemini-2.0-flash', 
                tools=tools_list,
                system_instruction=SYSTEM_INSTRUCTION
            )
            chat_session = model.start_chat(enable_automatic_function_calling=False) # We handle FC manually for the loop

async def fix_code(original_code, error_message):
    """
    Uses the LLM to fix the broken code based on the error message.
    """
    prompt = f"""
    The following Python script failed to execute:
    ```python
    {original_code}
    ```
    Error Message:
    {error_message}
    
    Task: Rewrite the script to fix the error.
    Return ONLY the corrected Python code in a code block.
    """
    
    # We use the text model for code fixing
    try:
        if AI_PROVIDER == "groq":
             messages = [
                 {"role": "system", "content": SYSTEM_INSTRUCTION},
                 {"role": "user", "content": prompt}
             ]
             completion = await _get_groq_response(messages)
             response_text = completion.choices[0].message.content
        else:
             response = await asyncio.to_thread(model.generate_content, prompt)
             response_text = response.text
             
        # Extract code block
        import re
        match = re.search(r"```python\n(.*?)```", response_text, re.DOTALL)
        if match:
            return match.group(1)
        return response_text # Fallback
        
    except Exception as e:
        logging.error(f"Fix Code Failed: {e}")
        return None

async def analyze_error_with_screenshot(error_log: str, screenshot_path: str):
    """
    Analyzes an error using both the text log and a screenshot of the screen.
    Useful for catching UI blocking errors, dialogs, or visual context.
    """
    prompt = f"""
    A Python script executed by the user failed.
    
    Error Log:
    {error_log}
    
    Attached is a screenshot of the screen at the time of failure.
    
    Task:
    1. Look for any error dialogs, popups, or visual cues in the screenshot that might explain the failure (e.g., "File not found" dialog, "Permission denied", or application in weird state).
    2. Combine visual info with the Error Log.
    3. Explain "What went wrong?" in simple, spoken English (2-3 sentences max).
    4. Suggest a fix.
    """
    
    return await analyze_image(screenshot_path, prompt)

async def analyze_image(image_path: str, prompt: str):
    """Analyzes an image using Gemini 2.0 Flash Vision capabilities."""
    global model
    try:
        if not os.path.exists(image_path):
            return "Error: Image file not found."
            
        # Initialize model if needed (without tools for pure vision task)
        vision_model = genai.GenerativeModel('gemini-2.0-flash')
        
        # Load image
        img = Image.open(image_path)
        
        # Generate content
        response = await asyncio.to_thread(vision_model.generate_content, [prompt, img])
        return response.text
    except Exception as e:
        logging.error(f"Vision analysis failed: {e}")
        return f"Error analyzing image: {e}"

async def process_command(user_input, tools_map, screenshot_path=None):
    """
    Processes the user command using Gemini 2.0 Flash with Function Calling and Vision.
    """
    global chat_session, model, groq_history, current_tools_list
    
    tools_list = list(tools_map.values())
    current_tools_list = tools_list
    initialize_model(tools_list)
    
    if AI_PROVIDER == "groq":
        # Ensure history is initialized
        if not groq_history:
             groq_history = [{"role": "system", "content": SYSTEM_INSTRUCTION}]

        # Sliding Window for History: Keep System (0) + Last 4 Messages (Aggressive truncation for Rate Limits)
        # We rebuild the history to ensure content length is manageable
        
        system_msg = groq_history[0]
        recent_history = groq_history[1:]
        
        if len(recent_history) > 4:
            recent_history = recent_history[-4:]
            
        final_history = [system_msg]
        
        for msg in recent_history:
            # Extract fields safely from dict or object
            content = None
            role = "user"
            tool_calls = None
            tool_call_id = None
            
            if isinstance(msg, dict):
                content = msg.get("content")
                role = msg.get("role")
                tool_calls = msg.get("tool_calls")
                tool_call_id = msg.get("tool_call_id")
            else:
                content = getattr(msg, "content", None)
                role = getattr(msg, "role", "user")
                tool_calls = getattr(msg, "tool_calls", None)
                tool_call_id = getattr(msg, "tool_call_id", None)
            
            # Truncate content if it's text (limit to 2000 chars)
            if content and isinstance(content, str) and len(content) > 2000:
                content = content[:2000] + "... [Truncated]"
            
            # Reconstruct as dict
            new_msg = {"role": role, "content": content}
            if tool_calls:
                new_msg["tool_calls"] = tool_calls
            if tool_call_id:
                new_msg["tool_call_id"] = tool_call_id
                
            final_history.append(new_msg)
            
        groq_history = final_history

        user_msg = {"role": "user", "content": user_input}
        if screenshot_path and os.path.exists(screenshot_path):
            user_msg["content"] += "\n[Note: User attached a screenshot, but Groq text models cannot see it. Ask for text description if needed.]"
        
        groq_history.append(user_msg)
        
        response = await _get_groq_response(groq_history, tools_list)

        if hasattr(response, 'choices'):
            msg = response.choices[0].message
            groq_history.append(_message_to_dict(msg))
            return response
        else:
            return str(response)

    # Prepare content for Gemini
    content = [user_input]
    if screenshot_path:
        try:
            if os.path.exists(screenshot_path):
                image = Image.open(screenshot_path)
                content.append(image)
                content.append("Analyze this screenshot and use it to help answer the command if relevant.")
            else:
                logging.warning(f"Screenshot path not found: {screenshot_path}")
        except Exception as e:
            logging.error(f"Error loading screenshot: {e}")

    try:
        # Simple Retry Logic with Backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Add a tiny delay to avoid hitting rate limits instantly
                if attempt > 0:
                    await asyncio.sleep(attempt * 2)
                    
                response = await asyncio.to_thread(chat_session.send_message, content)
                return response
            except exceptions.ResourceExhausted:
                wait_time = (2 ** attempt) + 1  # 2s, 3s, 5s
                logging.warning(f"Quota exceeded. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                
                # If we fail twice, try switching to a lighter model for this request
                if attempt == max_retries - 2:
                     logging.info("Attempting fallback to gemini-1.5-flash...")
                     try:
                        fallback_model = genai.GenerativeModel(
                            'gemini-1.5-flash', 
                            tools=tools_list,
                            system_instruction=SYSTEM_INSTRUCTION
                        )
                        # We create a temporary session just for this command to bypass the block
                        # Note: This loses conversation history for this turn, but better than crashing
                        temp_session = fallback_model.start_chat(enable_automatic_function_calling=False)
                        # We need to await the thread
                        response = await asyncio.to_thread(temp_session.send_message, content)
                        return response
                     except Exception as fallback_error:
                        logging.error(f"Fallback model failed: {fallback_error}")
                    
        return "Error: Resource exhausted. Please wait a moment before trying again."
            
    except Exception as e:
        return f"Error processing command: {e}"

async def send_tool_result(tool_name, tool_result):
    """Sends the result of a tool execution back to the chat session."""
    global chat_session, groq_history, current_tools_list
    
    if AI_PROVIDER == "groq":
        # We need to find the tool_call_id from the last message
        last_msg = groq_history[-1]
        tool_call_id = None
        
        # Check if last_msg has tool_calls attribute
        if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
            for tc in last_msg.tool_calls:
                if tc.function.name == tool_name:
                    tool_call_id = tc.id
                    break
        
        if tool_call_id:
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": str(tool_result)
            }
            groq_history.append(tool_msg)
            
            # Get next response
            response = await _get_groq_response(groq_history, current_tools_list)
            
            if hasattr(response, 'choices'):
                groq_history.append(response.choices[0].message)
                return response
            else:
                return str(response)
        else:
            return f"Error: Could not find matching tool call ID for {tool_name} in Groq history."

    try:
        # Construct the function response part
        # Note: In the official SDK, we send the function response.
        # Since we disabled automatic_function_calling, we need to send the response manually.
        
        # For simplicity with the high-level API, we can just send the text result as a user message 
        # acting as the "System" or "Tool", or use the proper part structure.
        # Sending as text is often enough for the model to understand "The tool returned this".
        
        response = await asyncio.to_thread(
            chat_session.send_message, 
            f"Tool '{tool_name}' output:\n{tool_result}\n\nBased on this, what is the next step or final answer?"
        )
        return response
    except Exception as e:
        return f"Error sending tool result: {e}"

async def send_groq_tool_results(tool_outputs):
    """Sends a batch of tool results to Groq."""
    global groq_history, current_tools_list
    
    if AI_PROVIDER != "groq":
        return "Error: Wrong provider."
        
    for output in tool_outputs:
        groq_history.append({
            "role": "tool",
            "tool_call_id": output["tool_call_id"],
            "content": output["content"]
        })
        
    # Get next response
    response = await _get_groq_response(groq_history, current_tools_list)
    
    if hasattr(response, 'choices'):
        groq_history.append(response.choices[0].message)
        return response
    else:
        return str(response)


async def generate_workflow_script(events):
    """Generates a Python script to replicate a recorded workflow."""
    global model
    try:
        if not events:
            return "# No events recorded."

        # Process events into a readable log + collect images
        log = "User Action Log:\n"
        images = []
        last_time = 0
        
        # Simple clustering of typing events
        typing_buffer = ""
        
        for i, event in enumerate(events):
            t = event.get('timestamp', 0)
            delay = t - last_time
            last_time = t
            
            if event['type'] == 'click':
                # Flush typing
                if typing_buffer:
                    log += f"- Type: '{typing_buffer}'\n"
                    typing_buffer = ""
                
                log += f"- Wait {delay:.2f}s, then Click at ({event['x']}, {event['y']}) with {event['button']}\n"
                
                # Include Element Info for robustness
                if 'element_info' in event and isinstance(event['element_info'], dict):
                    ei = event['element_info']
                    log += f"  (Target: '{ei.get('title', 'Unknown')}', Type: {ei.get('control_type', 'Unknown')}, ID: {ei.get('auto_id', '')})\n"
                
                # Add image if available (limit to first 10 to save bandwidth/tokens for now)
                if 'screenshot' in event and os.path.exists(event['screenshot']) and len(images) < 10:
                    img = Image.open(event['screenshot'])
                    images.append(img)
                    log += "  (Screenshot attached showing state before click)\n"
                    
            elif event['type'] == 'type':
                k = event['key']
                if len(k) == 1:
                    typing_buffer += k
                else:
                    if typing_buffer:
                        log += f"- Type: '{typing_buffer}'\n"
                        typing_buffer = ""
                    log += f"- Press Key: {k}\n"

        if typing_buffer:
            log += f"- Type: '{typing_buffer}'\n"

        prompt = f"""
        You are an expert automation engineer.
        The user has performed a sequence of actions on their computer.
        
        {log}
        
        Attached are screenshots corresponding to some of the click events.
        
        Task:
        1. Analyze the log and images to understand the workflow.
        2. Generate a ROBUST Python script.
           - DO NOT just use 'pyautogui.click(x, y)'. That is fragile.
           - Use 'pywinauto' or 'uiautomation' where possible to find elements by Name/ID.
           - Use the 'element_info' from the log to find windows/buttons robustly.
           - ONLY fall back to coordinates if element info is missing or generic.
        3. Add 'time.sleep()' to respect the delays (but make it 1.5x faster).
        4. Add comments explaining each step.
        5. Return ONLY the Python code in a code block.
        """
        
        # Use a fresh model instance for this one-shot task
        gen_model = genai.GenerativeModel('gemini-2.0-flash')
        
        content = [prompt] + images
        
        response = await asyncio.to_thread(gen_model.generate_content, content)
        text = response.text
        
        if "```python" in text:
            code = text.split("```python")[1].split("```")[0].strip()
        elif "```" in text:
            code = text.split("```")[1].strip()
        else:
            code = text.strip()
            
        return code

    except Exception as e:
        logging.error(f"Workflow generation failed: {e}")
        return f"# Error generating workflow: {e}"

# Deprecated but kept for compatibility if imported elsewhere temporarily
async def generate_content_for_task(prompt):
    model = genai.GenerativeModel('gemini-2.5-flash-lite')
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"Error: {e}")
        return None
