# SmudgeAI - Autonomous Desktop AI Agent

> **SmudgeAI** is a Windows-native autonomous desktop AI agent capable of understanding screen context, navigating UI elements, executing multi-step workflows, and performing actions with minimal human intervention.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Core Components](#core-components)
4. [AI Engine & Multimodal Processing](#ai-engine--multimodal-processing)
5. [Desktop Automation Stack](#desktop-automation-stack)
6. [UI Detection & Element Matching](#ui-detection--element-matching)
7. [Security & Safety Systems](#security--safety-systems)
8. [Error Handling & Recovery](#error-handling--recovery)
9. [Multi-Monitor & DPI Scaling Support](#multi-monitor--dpi-scaling-support)
10. [Internationalization & Localization](#internationalization--localization)
11. [Logging & Observability](#logging--observability)
12. [Capabilities Summary](#capabilities-summary)
13. [Comparison with OpenClaw](#comparison-with-openclaw)
14. [Bug Fixes & Security Hardening](#bug-fixes--security-hardening)
15. [Getting Started](#getting-started)
16. [Configuration](#configuration)
17. [Roadmap](#roadmap)

---

## Overview

SmudgeAI is designed to be a **fully autonomous desktop control agent** that can:

- **Understand screen context** through vision models and UI element trees
- **Navigate any Windows application** using UIA-based element discovery
- **Execute multi-step workflows** with verification and rollback
- **Adapt to unknown interfaces** through LLM-based reasoning
- **Operate safely** with permission systems and safeguards

### Key Design Goals

1. **Reliability First** - Every action is verified before proceeding
2. **Security by Default** - Permission systems and input sanitization on all dangerous operations
3. **Cross-UI Adaptability** - Works with any Windows application through UIA and CV fallback
4. **Production Ready** - Structured logging, error recovery, and observability

---

## Architecture

### High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              SmudgeAI Architecture                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐     ┌──────────────────────────────────────────────┐     │
│  │    GUI       │────▶│              AI Engine                        │     │
│  │  (PyQt5)     │     │  ┌─────────────┐  ┌─────────────────────┐   │     │
│  │              │     │  │ Groq Client │  │ Gemini Client        │   │     │
│  │  - Input     │     │  │ (Primary)   │  │ (Fallback)          │   │     │
│  │  - Display   │     │  └─────────────┘  └─────────────────────┘   │     │
│  │  - Status    │     │  ┌─────────────────────────────────────┐   │     │
│  └──────────────┘     │  │ Rate Limiter & Model Cycling          │   │     │
│         │              │  └─────────────────────────────────────┘   │     │
│         │              │  ┌─────────────────────────────────────┐   │     │
│         ▼              │  │ Conversation History Manager        │   │     │
│  ┌──────────────────────────────────────────────────────────────┐ │     │
│  │                    Task Manager                               │ │     │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐    │ │     │
│  │  │ Permission  │  │ Tool       │  │ Error              │    │ │     │
│  │  │ System      │  │ Registry   │  │ Classifier         │    │ │     │
│  │  └────────────┘  └────────────┘  └────────────────────┘    │ │     │
│  └──────────────────────────────────────────────────────────────┘ │     │
│                              │                                        │
│         ┌────────────────────┼────────────────────┐                 │
│         ▼                    ▼                    ▼                    │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐        │
│  │ Desktop     │    │ CV/UI       │    │ Local VLM       │        │
│  │ State       │    │ Integration │    │ (Optional)      │        │
│  │ (UIA)       │    │             │    │                 │        │
│  └─────────────┘    └─────────────┘    └─────────────────┘        │
│                              │                                        │
│                              ▼                                        │
│                     ┌─────────────────┐                              │
│                     │  Action         │                              │
│                     │  Execution      │                              │
│                     │  (pyautogui)    │                              │
│                     └─────────────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Language/Framework |
|-----------|----------------|-------------------|
| **GUI** | User input, status display, permission dialogs | PyQt5 |
| **AI Engine** | LLM orchestration, vision analysis, tool calling | Python (async) |
| **Task Manager** | Tool execution, permission checks, caching | Python |
| **Desktop State** | UIA element tree, window management | pywinauto + pygetwindow |
| **CV/UI Integration** | Template matching, LLM coordinate verification | OpenCV + PIL |
| **Error Handler** | Error classification, retry strategies | Python |
| **Structured Logging** | Correlation IDs, action tracking | Python (custom) |

---

## Core Components

### 1. AI Engine (`ai_engine.py`)

The AI Engine is the brain of SmudgeAI, orchestrating all LLM interactions.

#### Features

- **Multi-Provider Support**: Groq (primary), Google Gemini (fallback)
- **Vision Analysis**: Llama 3.2 Vision (Groq) → Gemini 1.5 Flash fallback
- **Model Cycling**: Automatic failover when rate limits hit
- **Rate Limiting**: Built-in rate limiter with exponential backoff
- **Conversation History**: Properly serialized message history (dict-based)
- **Tool Schema Generation**: Dynamic tool schema from Python functions

#### Rate Limiter Implementation

```python
class RateLimiter:
    requests_in_window: int      # Requests in current window
    window_size: float = 60.0    # 60-second window
    max_requests: int = 30       # Max 30 requests per window
    blocked_until: float         # Timestamp when block expires
    consecutive_errors: int       # Track consecutive failures
```

**Backoff Strategy**:
- Base delay: 30 seconds
- Exponential: 2^consecutive_errors
- Jitter: Random 0-10 seconds
- Max block: 300 seconds (5 minutes)

#### Vision Pipeline

```
Screenshot → Groq Llama 3.2 Vision → JSON Elements → Coordinate Verification → Click
                ↓ (fallback)
         Gemini 1.5 Flash Vision
```

### 2. Desktop State (`desktop_state.py`)

Captures and maintains the UI element hierarchy of all windows.

#### COM Threading Fix

Previously, pywinauto initialization caused race conditions. Now:

```python
def _ensure_pywinauto_com_init():
    import pythoncom
    pythoncom.CoInitializeEx(None, pythoncom.COINIT_MULTITHREADED)
```

#### Element Types Supported

- WINDOW, BUTTON, EDIT, MENU, MENU_ITEM
- TAB, CHECKBOX, RADIO_BUTTON, COMBOBOX
- LIST, LIST_ITEM, TEXT, UNKNOWN

#### Data Structures

```python
@dataclass
class UIElement:
    title: str
    element_type: ElementType
    rect: tuple          # (x, y, width, height)
    automation_id: str    # UIA AutomationId
    class_name: str       # Window class name
    is_visible: bool
    is_enabled: bool
    children: List[UIElement]

@dataclass
class WindowInfo:
    title: str
    process_name: str
    rect: tuple
    is_active: bool
    elements: List[UIElement]
```

### 3. CV/UI Integration (`cv_ui_integration.py`)

Hybrid UI detection combining UIA accessibility with computer vision.

#### Detection Methods (Priority Order)

1. **UIA Detection**: Uses Windows Accessibility API for precise element location
2. **Template Matching**: OpenCV-based image matching for visual elements
3. **LLM Vision**: Groq/Gemini vision for complex UI interpretation
4. **Coordinate Verification**: All LLM coordinates verified against UIA elements

#### ScreenHelper Class

Handles multi-monitor and DPI scaling:

```python
class ScreenHelper:
    _dpi_scale: float              # System DPI (1.0 = 100%)
    _monitor_info: Dict            # Per-monitor bounds

    def adjust_coords_for_monitor(x, y, window_rect):
        # Offset coordinates for secondary monitors

    def scale_screenshot_for_dpi(screenshot_path):
        # Scale screenshot to match actual pixel coordinates
```

#### RobustClicker with Circuit Breaker

```python
class RobustClicker:
    _max_retries: int = 3
    _circuit_breaker_max_iterations: int = 10
    _circuit_breaker_time_budget: float = 30.0  # seconds

    # Exponential backoff: 0.5s → 1.0s → 2.0s (capped at 5s)
    def _get_retry_delay(attempt) -> float
```

### 4. Task Manager (`task_manager.py`)

Executes tools/actions requested by the AI engine.

#### Permission System

DANGEROUS_ACTIONS_REGEX patterns detect:
- File deletion: `delete`, `remove`
- System commands: `shutdown`, `restart`, `kill`
- Registry modifications: `reg delete`, `reg add`
- Shell execution: `exec`, `cmd`, `powershell`

```python
class PermissionSystem:
    DANGEROUS_PATTERNS = [
        (r"delete", r"(file|folder|directory)", "delete_file", ...),
        (r"shutdown", r".*", "shutdown_pc", ...),
        # ... 17 total patterns
    ]

    SYSTEM_DIRS = [
        r"C:\Windows", r"C:\Program Files",
        r"C:\System32", r"C:\Boot", r"C:\Recovery"
    ]
```

#### Command Cache

- **Expiry**: 24 hours
- **Max Size**: 50 entries
- **Validation**: Schema validation, sensitive data redaction

### 5. Error Handler (`error_handler.py`)

Structured error classification and recovery.

#### Error Categories

| Category | Description | Action |
|----------|-------------|--------|
| RETRYABLE | Transient failures (timeout, network) | Retry with backoff |
| FATAL | Unrecoverable errors | Log and escalate |
| ESCALATION | Requires human intervention | Block and alert |

#### Retry Strategies

```python
class RetryStrategy:
    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 10.0
    exponential_base: float = 2.0
    jitter: bool = True
```

---

## AI Engine & Multimodal Processing

### Vision Pipeline

```
User Request: "Click the Save button"

┌─────────────────────────────────────────────────────────────┐
│ 1. Screenshot Capture                                        │
│    - pyautogui.screenshot()                                 │
│    - Scale for DPI if needed                                │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Element Detection                                         │
│    a) UIA Lookup (if window context available)              │
│    b) Template Matching (OpenCV)                            │
│    c) LLM Vision Analysis                                   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Coordinate Verification                                  │
│    - Check if LLM coords fall within any UIA element        │
│    - Penalize confidence if unverified                     │
│    - Filter elements below 0.3 confidence threshold         │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Action Execution                                         │
│    - pyautogui.click(x, y)                                 │
│    - Adjust for monitor offset/DPI                           │
│    - Verify state change post-click                          │
└─────────────────────────────────────────────────────────────┘
```

### Model Selection Strategy

1. **Text + Tools**: Groq `llama-3.3-70b-versatile` (primary)
2. **Vision**: Groq `llama-3.2-11b-vision-preview` (primary)
3. **Fallback**: Gemini 1.5 Flash for both

### Conversation History Management

All messages stored as plain dictionaries to prevent type confusion:

```python
def _message_to_dict(msg) -> dict:
    return {
        "role": getattr(msg, "role", "assistant"),
        "content": getattr(msg, "content", None),
        "tool_calls": getattr(msg, "tool_calls", None),
        "tool_call_id": getattr(msg, "tool_call_id", None)
    }
```

---

## Desktop Automation Stack

### Action Execution Flow

```
AI Decision → Tool Call → Permission Check → Action → Verification → State Update

Permission Check:
├─ Dangerous pattern detected?
├─ System directory access?
├─ Path traversal attempt?
└─ Callback to user if needed
```

### Supported Actions

| Category | Actions |
|----------|---------|
| **Application** | open_application, close_application, switch_to_window |
| **Navigation** | click_element, double_click, right_click, hover |
| **Text Input** | type_text, press_key, hotkey, select_all |
| **File System** | create_file, read_file, delete_file, list_directory |
| **Clipboard** | read_clipboard, write_clipboard |
| **System** | shutdown_pc, restart_pc, get_system_info |
| **Web** | search_google, open_url, get_page_text |
| **Office** | send_email, create_document |

### State Verification

Every click action is verified by comparing before/after state:

```python
async def _execute_click_with_verification(element):
    pre_state = desktop_state.get_state_summary()
    pyautogui.click(x, y)
    await asyncio.sleep(0.2)
    post_state = desktop_state.get_state_summary()
    return pre_state != post_state or _verify_element_exists(element)
```

---

## UI Detection & Element Matching

### Detection Method Priority

1. **UIA (Windows Accessibility)**: Fastest, most reliable for standard Windows apps
2. **Template Matching (OpenCV)**: Good for custom/non-standard UI
3. **LLM Vision**: Last resort for complex or dynamic UIs

### Localization Support

UI element matching supports localized button text:

```python
_LOCALIZED_UI_TERMS = {
    "de_DE": {"save": ["Speichern"], "open": ["Öffnen"], ...},
    "fr_FR": {"save": ["Enregistrer"], "open": ["Ouvrir"], ...},
    "ja_JP": {"save": ["保存"], "open": ["開く"], ...},
    # ... 6 languages supported
}
```

### Hallucination Prevention

LLM-provided coordinates are verified against UIA elements:

```python
def _verify_llm_coordinates(elements):
    for elem in elements:
        if _is_coord_in_any_element_bounds(elem):
            elem.confidence += 0.3   # Boost verified coords
        else:
            elem.confidence -= 0.4   # Penalize hallucinated
    return [e for e in elements if e.confidence > 0.3]
```

---

## Security & Safety Systems

### 1. Permission System

All dangerous operations require explicit permission:

```python
DANGEROUS_PATTERNS = [
    (r"delete", r"(file|folder)", "delete_file"),
    (r"shutdown", r".*", "shutdown_pc"),
    (r"exec", r"(shell|cmd|powershell)", "execute_shell"),
    # ... 17 patterns total
]
```

**Built-in Safety**:
- SAFE_MODE flag for human-in-the-loop
- System directory protection
- Path traversal detection
- Confirmation dialogs for destructive actions

### 2. Input Sanitization

**Clipboard Injection Prevention**:
```python
PROMPT_INJECTION_PATTERNS = [
    r"(?i)ignore\s+(previous|all|your)\s+instructions",
    r"(?i)jailbreak",
    r"(?i) DAN\s+mode",
    # ... 15+ patterns
]
```

**SKILL.md Template Injection Prevention**:
```python
def _sanitize_skill_content(content):
    # Remove {{...}}, {%...%}, <script>, javascript:
    sanitized = re.sub(r'\{\{[^}]+\}\}', '[TEMPLATE_REMOVED]')
    sanitized = re.sub(r'(?i)<script[^>]*>.*?</script>', '[SCRIPT_REMOVED]')
    return sanitized
```

### 3. Path Traversal Protection

```python
def create_file(path):
    real_path = os.path.realpath(path)
    if any('..' in part for part in path.split(os.sep)):
        return "Error: Path traversal detected"
    if is_system_directory(real_path):
        return "Error: Access denied to system directory"
```

### 4. Cache Validation

```python
def _validate_cache_entry(entry):
    if not isinstance(entry, dict):
        return False
    for k in ["tool", "args", "result", "timestamp"]:
        if k not in entry:
            return False
    # Redact sensitive keys
    for k in SENSITIVE_CACHE_KEYS:
        if k in entry:
            entry[k] = "***REDACTED***"
    return True
```

### 5. API Key Security

```python
ALLOWED_API_KEY_PREFIXES = {
    "GROQ_API_KEY": ["gsk_"],
    "GOOGLE_API_KEY": ["AIza"],
}

def _validate_api_key(key_name, value):
    if not any(value.startswith(p) for p in ALLOWED_PREFIXES[key_name]):
        return False  # Invalid prefix = potential fake key
```

---

## Error Handling & Recovery

### Error Classification

```python
class ErrorClassifier:
    RETRY_PATTERNS = {
        "timeout": {
            "patterns": ["timeout", "timed out"],
            "severity": ErrorSeverity.MEDIUM,
            "suggested_fix": "Retry with exponential backoff"
        },
        "rate_limit": {
            "patterns": ["rate limit", "429"],
            "severity": ErrorSeverity.HIGH,
            "suggested_fix": "Wait and retry with longer backoff"
        },
        "connection": {
            "patterns": ["connection", "network"],
            "severity": ErrorSeverity.MEDIUM,
            "suggested_fix": "Check network, retry after delay"
        }
    }
```

### Circuit Breaker Pattern

```python
class RobustClicker:
    _circuit_breaker_max_iterations = 10
    _circuit_breaker_time_budget = 30.0  # seconds

    def _check_circuit_breaker(self):
        if self._circuit_breaker_iterations >= MAX:
            return False  # Block further attempts
        if time.time() - self._start_time > BUDGET:
            return False  # Time exceeded
        return True
```

---

## Multi-Monitor & DPI Scaling Support

### Multi-Monitor Handling

```python
class ScreenHelper:
    def get_primary_monitor_offset(self) -> Tuple[int, int]:
        # Returns (x, y) offset of primary monitor

    def adjust_coords_for_monitor(self, x, y, window_rect):
        # Adjust for windows on secondary monitors
        # pyautogui uses primary monitor coords
        # Secondary monitors may have negative coords
```

### DPI Scaling Detection

```python
def _init_dpi(self):
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    self._dpi_scale = user32.GetDpiForSystem() / 96.0

def scale_screenshot_for_dpi(self, screenshot_path):
    if self._dpi_scale != 1.0:
        img = Image.open(screenshot_path)
        new_size = (int(img.width * self._dpi_scale), ...)
        img_scaled = img.resize(new_size, Image.LANCZOS)
        img_scaled.save(screenshot_path)
```

---

## Internationalization & Localization

### Supported Locales

| Locale | Language | Coverage |
|--------|----------|----------|
| en_US | English | Default (no mapping needed) |
| de_DE | German | 24 common UI terms |
| fr_FR | French | 24 common UI terms |
| es_ES | Spanish | 24 common UI terms |
| ja_JP | Japanese | 24 common UI terms |
| zh_CN | Chinese (Simplified) | 24 common UI terms |
| ko_KR | Korean | 24 common UI terms |

### Element Matching with Localization

```python
def _match_element(elements, description):
    search_terms = set(description.lower().split())

    # Expand with localized terms
    for word in search_terms:
        localized = get_localized_terms(word)  # ["Speichern", "Save"]
        search_terms.update(localized)

    # Match against element labels
    for elem in elements:
        if _fuzzy_match(search_terms, elem.label):
            return elem
```

---

## Logging & Observability

### Structured Logging with Correlation IDs

```python
class CorrelationLogger:
    session_id: str        # Unique per application run
    correlation_id: str    # Unique per action/request

    def log_action(action, target, result, duration_ms):
        # Format: [SESSION_ID][CORR_ID][INFO] ACTION: action | target=x result=y duration_ms=z

    def log_api_call(provider, model, success, duration_ms, error):
        # Track API performance and failures

    def log_tool_execution(tool_name, args, success, duration_ms):
        # Monitor tool performance
```

### Log Context Manager

```python
with LogContext(user_id="123", action="click"):
    # All logs within this block include user_id=123 and action=click
    click_element("Save")
```

---

## Capabilities Summary

### What SmudgeAI Can Do

| Capability | Implementation | Status |
|------------|---------------|--------|
| Open/close applications | `subprocess` + pyautogui | ✅ |
| Click UI elements | UIA + pyautogui | ✅ |
| Type text | pyautogui | ✅ |
| Read clipboard | pyperclip (sanitized) | ✅ |
| Navigate file system | os/shutil modules | ✅ |
| Web search | Google search API | ✅ |
| Send emails | SMTP | ✅ |
| Handle multi-monitor | ScreenHelper class | ✅ |
| Handle DPI scaling | ctypes DPI detection | ✅ |
| Localized UI matching | 7 languages | ✅ |
| Permission system | 17 dangerous patterns | ✅ |
| Error recovery | Circuit breaker + retry | ✅ |
| Vision analysis | Groq Vision + Gemini | ✅ |
| Coordinate verification | UIA validation | ✅ |
| Prompt injection detection | Regex patterns | ✅ |
| Path traversal prevention | realpath + validation | ✅ |
| Command cache | Validated + redacted | ✅ |

### What SmudgeAI Cannot Do (Yet)

| Capability | Limitation | Priority |
|------------|------------|----------|
| Browser automation | No Playwright/CDP | High |
| Cross-platform | Windows only | Medium |
| Drag-and-drop verification | Basic implementation | Medium |
| Voice input | Basic STT support | Low |
| Memory persistence | Session only | Medium |

---

## Comparison with OpenClaw

| Aspect | SmudgeAI | OpenClaw |
|--------|----------|----------|
| **UI Understanding** | UIA + CV + Vision (hybrid) | CDP + ARIA snapshots |
| **Action Execution** | pyautogui (coordinate-based) | Playwright (ref-based) |
| **Verification** | Before/after state diff | Explicit state contracts |
| **Cross-platform** | Windows only | macOS/Linux/Windows |
| **Planning** | LLM-based | Attempt-based with compaction |
| **Rate Limiting** | Built-in circuit breaker | API-level |
| **Security** | Permission system + injection prevention | Not evaluated |
| **Localization** | 7 languages | Not evaluated |

### Where SmudgeAI Excels

1. **Hybrid Detection**: Combines UIA, CV, and Vision for robustness
2. **Security**: Comprehensive permission and injection prevention
3. **Localization**: Native multi-language UI matching
4. **Error Recovery**: Circuit breaker and retry strategies built-in

### Where SmudgeAI Can Improve

1. **Browser Automation**: OpenClaw's CDP-based approach is more reliable
2. **Action Verification**: OpenClaw's state contracts are more explicit
3. **Cross-platform**: SmudgeAI is Windows-only

---

## Bug Fixes & Security Hardening

### Critical Bugs Fixed

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| COM Threading Conflict | pywinauto initialized before COM | Explicit `CoInitializeEx` at usage time |
| Hallucinated Coordinates | LLM provided unverified coords | UIA verification + confidence adjustment |
| History Type Mixing | Object/dict type inconsistency | Always serialize to dict |
| Infinite Loops | No circuit breaker | Max iterations + time budget |
| Silent Exception Crashes | No exception handler in Worker | Signal-based error propagation |

### Security Vulnerabilities Fixed

| Vulnerability | Attack Vector | Mitigation |
|---------------|---------------|------------|
| Prompt Injection | Malicious clipboard content | Regex pattern detection + redaction |
| Template Injection | Malicious SKILL.md | Template tag sanitization |
| API Key Exposure | .env commit | Key prefix validation + masked logging |
| Path Traversal | `../` in file paths | realpath validation + forbidden dirs |
| Unsafe Deserialization | Malicious cache JSON | Schema validation + redaction |

---

## Getting Started

### Prerequisites

- Windows 10/11
- Python 3.9+
- API Keys (Groq and/or Google Gemini)

### Installation

```bash
# Clone the repository
git clone https://github.com/your-repo/SmudgeAI.git
cd SmudgeAI

# Install dependencies
pip install -r requirements.txt

# Create .env file
echo "GROQ_API_KEY=gsk_xxxxx" > .env
echo "GOOGLE_API_KEY=xxxxx" >> .env

# Run the application
python main.py
```

### Requirements

```
pyqt5>=5.15.0
pywinauto>=0.6.8
pyautogui>=0.9.54
opencv-python>=4.8.0
Pillow>=10.0.0
groq>=0.4.0
google-generative-ai>=0.3.0
python-dotenv>=1.0.0
pyperclip>=1.8.2
pygetwindow>=0.0.9
```

---

## Configuration

### Environment Variables

```bash
# AI Provider
AI_PROVIDER=groq  # or "gemini"

# API Keys
GROQ_API_KEY=gsk_xxxxx
GOOGLE_API_KEY=AIzaxxxxx

# Search APIs (Optional)
TAVILY_API_KEY=tvly-xxxxx
SERPER_API_KEY=serper_xxxxx

# Email (Optional)
EMAIL_USER=your_email@gmail.com
EMAIL_PASSWORD=app_specific_password

# Logging
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR
LOG_FILE=smudgeai.log

# Safety
SAFE_MODE=True  # Require confirmation for dangerous actions
```

---

## Roadmap

### Phase 1: Foundation (Completed ✅)
- [x] UIA-based element discovery
- [x] pyautogui action execution
- [x] Vision-based UI analysis
- [x] Multi-monitor support
- [x] DPI scaling handling

### Phase 2: Reliability (Completed ✅)
- [x] Coordinate verification
- [x] Circuit breaker pattern
- [x] Error classification
- [x] Permission system
- [x] Command caching

### Phase 3: Security (Completed ✅)
- [x] Prompt injection prevention
- [x] Path traversal prevention
- [x] Safe deserialization
- [x] API key validation
- [x] SKILL.md sanitization

### Phase 4: Autonomy (In Progress)
- [ ] Multi-step workflow execution
- [ ] Context compaction for long tasks
- [ ] Learning from user corrections
- [ ] Cross-application workflows

### Phase 5: Production
- [ ] Browser automation (Playwright/CDP)
- [ ] Cross-platform support (macOS, Linux)
- [ ] Voice input/output
- [ ] Persistent memory
- [ ] Web dashboard for monitoring

---

## License

MIT License - See LICENSE file for details.

---

## Contributing

Contributions welcome! Please read CONTRIBUTING.md for guidelines.

---

**Built with ❤️ for Windows desktop automation**
