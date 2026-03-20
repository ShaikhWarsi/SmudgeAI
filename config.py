import os
import logging
from dotenv import load_dotenv

load_dotenv()

ALLOWED_API_KEY_PREFIXES = {
    "GOOGLE_API_KEY": ["AIza"],
    "GROQ_API_KEY": ["gsk_"],
    "TAVILY_API_KEY": ["tvly-"],
    "SERPER_API_KEY": ["serper_"],
    "EMAIL_PASSWORD": [],
}

SENSITIVE_KEYS = {"GROQ_API_KEY", "GOOGLE_API_KEY", "EMAIL_PASSWORD", "TAVILY_API_KEY", "SERPER_API_KEY"}


def _validate_api_key(key_name: str, value: str) -> bool:
    if not value:
        return True
    if key_name in ALLOWED_API_KEY_PREFIXES:
        prefixes = ALLOWED_API_KEY_PREFIXES[key_name]
        if prefixes and not any(value.startswith(p) for p in prefixes):
            logging.warning(f"{key_name} does not match expected prefix patterns - please verify it is correct")
            return False
    if len(value) < 10:
        logging.warning(f"{key_name} appears to be too short to be a valid key")
        return False
    return True


def _mask_sensitive_value(value: str) -> str:
    if not value or len(value) < 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def get_config_with_logging():
    config_info = {}
    for key in ["AI_PROVIDER", "GROQ_MODELS", "WHISPER_MODEL", "LOG_FILE", "LOG_LEVEL", "SAFE_MODE"]:
        val = os.getenv(key, "")
        config_info[key] = val

    sensitive_display = {}
    for key in SENSITIVE_KEYS:
        val = os.getenv(key, "")
        sensitive_display[key] = _mask_sensitive_value(val) if val else "(not set)"

    return config_info, sensitive_display


AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
if GOOGLE_API_KEY and not _validate_api_key("GOOGLE_API_KEY", GOOGLE_API_KEY):
    logging.warning("GOOGLE_API_KEY validation failed - functionality may be impaired")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
if GROQ_API_KEY and not _validate_api_key("GROQ_API_KEY", GROQ_API_KEY):
    logging.warning("GROQ_API_KEY validation failed - functionality may be impaired")

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b"
]

WHISPER_MODEL = "whisper-large-v3"

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

EMAIL_USER = os.getenv("EMAIL_USER", "YOUR_EMAIL@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
if not EMAIL_PASSWORD or EMAIL_PASSWORD == "YOUR_EMAIL_PASSWORD":
    logging.info("EMAIL_PASSWORD not configured - email features disabled")

LOG_FILE = os.getenv("LOG_FILE", "jarvis.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

SAFE_MODE = True

import sys as _sys
if not hasattr(_sys, '_in_security_check'):
    _sys._in_security_check = True
    _config, _sensitive = get_config_with_logging()
    logging.info(f"Config loaded - AI Provider: {AI_PROVIDER}, Safe Mode: {SAFE_MODE}")
    for k, v in _sensitive.items():
        logging.debug(f"  {k}: {v}")

