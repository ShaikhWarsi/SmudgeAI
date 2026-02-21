# Jarvis: Autonomous Agent for Developer Workflows

![Status](https://img.shields.io/badge/Status-Beta-orange) ![Python](https://img.shields.io/badge/Python-3.11%2B-blue) ![AI](https://img.shields.io/badge/AI-Gemini%202.0%20Flash-purple) ![License](https://img.shields.io/badge/License-MIT-green)

**Jarvis** is an autonomous agent designed specifically for **Developer Workflows**. Unlike generic chatbots, Jarvis is an active pair programmer that lives in your environment, debugging code, managing files, and automating repetitive engineering tasks with human-in-the-loop safety.

---

## 🚀 Key Capabilities

### 1. Self-Healing Code Execution 🩹
- **Problem**: Most agents crash when their generated code fails.
- **Solution**: Jarvis captures `stderr`, feeds it back to the LLM, analyzes the root cause, and **rewrites the script automatically**. It iterates until the code works or a maximum retry limit is reached.
- **Use Case**: "Fix this script" -> Jarvis runs it, sees the error, patches it, and verifies the fix.

### 2. Intelligent Context Awareness 🧠
- **Feature**: Smart Window Analysis.
- **How it works**: Jarvis detects your active window (e.g., "main.py - VS Code") to infer context. If you say "Debug this", it knows exactly what "this" refers to without needing a file upload.

### 3. Safe Mode (Human-in-the-Loop) 🛡️
- **Philosophy**: AI should be powerful but controllable.
- **Implementation**: 
  - **Read-Only Default**: Jarvis can read screens and files freely.
  - **Active Confirmation**: Any destructive action (File Deletion, Shell Execution, System Shutdown) requires explicit user approval via a native OS dialog.
  - **Visual Indicator**: The UI clearly signals when Safe Mode is active (Green Shield).

### 4. Deep Technical Research 🔍
- **Feature**: Real-time access to documentation and tech stacks.
- **Tech**: Integrates **Tavily/Serper APIs** to fetch the latest API references, library updates, or GitHub issues, ensuring code suggestions aren't outdated.

---

## 🛠️ Installation & Setup

### Prerequisites
- Python 3.9+
- Google Cloud API Key (Gemini) OR Groq API Key

### Quick Start
1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-repo/jarvis-ai.git
   cd jarvis-ai
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   - Rename `.env.example` to `.env` and fill in your keys:
     ```ini
     # AI Provider (gemini or groq)
     AI_PROVIDER=gemini

     # Keys
     GOOGLE_API_KEY=your_gemini_key
     GROQ_API_KEY=your_groq_key
     
     TAVILY_API_KEY=your_tavily_key_here  # Optional for Deep Search
     ```

4. **Run**:
   ```bash
   python main.py
   ```

---

## 🏗️ Architecture & Security

### The "Sandbox" Reality
- **Current State (MVP)**: Jarvis operates within a defined `workspace/` directory for file creation to prevent accidental system clutter. 
- **Production Roadmap**: The production version will utilize ephemeral **Docker Containers** for each task execution to ensure complete isolation and security.

### Core Modules
- **`gui.py`**: Modern PyQt5 interface with non-intrusive notifications and status cycling.
- **`ai_engine.py`**: Manages Gemini 2.0 Flash sessions with a specialized "Developer Persona" system prompt.
- **`task_manager.py`**: Handles tool execution with robust error handling and Safe Mode enforcement.
- **`workflow_learner.py`**: Experimental module for learning repetitive GUI tasks via computer vision.

---

## 🧪 Testing

Run the unit test suite to verify core functionality:
```bash
python -m unittest discover tests
```

---
*Built for Hackathon 2026. Focused on practical developer utility, not hype.*
