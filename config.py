import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# AI Provider Configuration
# Options: "gemini", "groq"
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")

# Google Gemini API Key
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# Groq API Key
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Groq Models (Updated per user request)
GROQ_MODELS = [ 
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b"
]

# Whisper Model for speech-to-text (Groq API)
WHISPER_MODEL = "whisper-large-v3"

# Search API Keys (Optional)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# Email Configuration
EMAIL_USER = os.getenv("EMAIL_USER", "YOUR_EMAIL@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "YOUR_EMAIL_PASSWORD")

# Logging Configuration
LOG_FILE = "jarvis.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Safety Configuration
SAFE_MODE = True  # Default to Safe Mode (Human-in-the-Loop) for demo safety

