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
from types import SimpleNamespace

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

genai.configure(api_key=GOOGLE_API_KEY)

# Global model and chat session
model = None
chat_session = None

# Groq Globals
groq_client = None
groq_history = []
current_groq_model_index = 0
current_tools_list = []

async def _get_groq_response(messages, tools=None):
    """Internal helper to get response from Groq with model cycling."""
    global current_groq_model_index, groq_client
    
    # Convert tools to Groq schema
    groq_tools = None
    if tools:
        groq_tools = [get_tool_schema(t) for t in tools]

    max_retries = len(GROQ_MODELS)
    
    for attempt in range(max_retries):
        model_name = GROQ_MODELS[current_groq_model_index]
        try:
            logging.info(f"Using Groq Model: {model_name}")
            
            # Create completion
            completion = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=model_name,
                messages=messages,
                tools=groq_tools,
                tool_choice="auto" if groq_tools else None,
                max_tokens=4096
            )
            
            return completion
            
        except groq.RateLimitError:
            logging.warning(f"Rate limit hit for {model_name}. Cycling model...")
            current_groq_model_index = (current_groq_model_index + 1) % len(GROQ_MODELS)
            continue
            
        except Exception as e:
            logging.error(f"Groq Error ({model_name}): {e}")
            # Cycle on other errors too if it looks like a model availability issue or tool use failure (400)
            # The specific error for tool failure is 400 with 'tool_use_failed'
            error_str = str(e).lower()
            if "rate limit" in error_str or "429" in error_str or "400" in error_str or "tool_use_failed" in error_str:
                 current_groq_model_index = (current_groq_model_index + 1) % len(GROQ_MODELS)
                 continue
            return f"Error with Groq: {e}"
            
    return "Error: All Groq models exhausted or failed."

SYSTEM_INSTRUCTION = """
You are Jarvis, an Autonomous Agent specialized in Developer Workflows.
You are an expert pair programmer, debugger, and system architect.
Your goal is to accelerate development tasks: coding, debugging, and file management.

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

2. **Smart Context**:
   - You have access to the active window title. Use it to infer context.
   - If the user says "Fix this", and the active window is "main.py - VS Code", analyze that file.

3. **Research Agent**:
   - For technical questions ("latest React features", "Python 3.12 changes"), use 'deep_search'.
   - Synthesize answers from official documentation and credible tech sources.

4. **Web Navigation**:
   - Construct URLs for direct navigation.
   - "Open GitHub issues for React" -> open_website("https://github.com/facebook/react/issues")

5. **UI Interaction**:
   - Use 'click_at_coordinates' or 'type_text' only when API methods are unavailable.
   - Request screenshots if coordinates are needed and not provided.

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
    Uses the AI model to fix the broken code based on the error message.
    """
    global model
    if model is None:
        # We need to initialize it if it's not already. 
        # But usually it is initialized by process_command. 
        # If not, we can't do much without tools list, but for generation we don't need tools.
        model = genai.GenerativeModel('gemini-2.0-flash', system_instruction=SYSTEM_INSTRUCTION)

    prompt = f"""
    The following Python script failed to execute:
    ```python
    {original_code}
    ```
    
    Error Message:
    {error_message}
    
    Task: Rewrite the script to fix the error. 
    Return ONLY the corrected Python code in a code block. 
    Do not explain. Just provide the code.
    """
    
    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        
        # Extract code from response
        text = response.text
        if "```python" in text:
            code = text.split("```python")[1].split("```")[0].strip()
        elif "```" in text:
            code = text.split("```")[1].strip()
        else:
            code = text.strip()
            
        return code
    except Exception as e:
        logging.error(f"Failed to fix code with AI: {e}")
        return None

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
        user_msg = {"role": "user", "content": user_input}
        if screenshot_path and os.path.exists(screenshot_path):
            user_msg["content"] += "\n[Note: User attached a screenshot, but Groq text models cannot see it. Ask for text description if needed.]"
        
        groq_history.append(user_msg)
        
        response = await _get_groq_response(groq_history, tools_list)
        
        if hasattr(response, 'choices'):
            msg = response.choices[0].message
            # Convert to dict for history if needed, but object is fine usually if handled correctly.
            # However, for subsequent calls, we need the message in the history.
            # Groq Python SDK 'ChatCompletionMessage' is an object.
            # We can append the object directly as the SDK handles it.
            groq_history.append(msg)
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
        2. Generate a robust Python script using 'pyautogui' and 'pywinauto' to replicate this workflow.
        3. Use 'time.sleep()' to respect the delays (or slightly faster).
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
