import logging
import time
import uuid
import functools
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime


class CorrelationLogger:
    _instance = None

    def __init__(self):
        self.logger = logging.getLogger("smudgeai")
        self.correlation_id = None
        self.session_id = str(uuid.uuid4())[:8]
        self._context: Dict[str, Any] = {}

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def new_correlation_id(self) -> str:
        self.correlation_id = str(uuid.uuid4())[:12]
        return self.correlation_id

    def get_correlation_id(self) -> Optional[str]:
        return self.correlation_id

    def set_context(self, key: str, value: Any):
        self._context[key] = value

    def clear_context(self):
        self._context = {}

    def _format_message(self, level: str, message: str, extra: Dict = None) -> str:
        parts = [
            f"[{self.session_id}]",
        ]
        if self.correlation_id:
            parts.append(f"[{self.correlation_id}]")
        parts.append(f"[{level}]")
        if self._context:
            ctx_str = " ".join(f"{k}={v}" for k, v in self._context.items())
            parts.append(f"[{ctx_str}]")
        parts.append(message)
        if extra:
            extra_str = " ".join(f"{k}={v}" for k, v in extra.items())
            parts.append(f"| {extra_str}")
        return " ".join(parts)

    def debug(self, message: str, extra: Dict = None):
        self.logger.debug(self._format_message("DEBUG", message, extra))

    def info(self, message: str, extra: Dict = None):
        self.logger.info(self._format_message("INFO", message, extra))

    def warning(self, message: str, extra: Dict = None):
        self.logger.warning(self._format_message("WARN", message, extra))

    def error(self, message: str, extra: Dict = None):
        self.logger.error(self._format_message("ERROR", message, extra))

    def critical(self, message: str, extra: Dict = None):
        self.logger.critical(self._format_message("CRITICAL", message, extra))

    def log_action(self, action: str, target: str = "", result: str = "", duration_ms: float = 0):
        self.info(f"ACTION: {action}", {
            "target": target,
            "result": result,
            "duration_ms": round(duration_ms, 2)
        })

    def log_api_call(self, provider: str, model: str, success: bool, duration_ms: float, error: str = ""):
        level = "INFO" if success else "ERROR"
        self.logger.log(
            logging.INFO if success else logging.ERROR,
            self._format_message(level, f"API_CALL: {provider}/{model}", {
                "success": success,
                "duration_ms": round(duration_ms, 2),
                "error": error
            })
        )

    def log_tool_execution(self, tool_name: str, args: Dict, success: bool, duration_ms: float = 0, error: str = ""):
        self.info(f"TOOL: {tool_name}", {
            "args": str(args)[:200],
            "success": success,
            "duration_ms": round(duration_ms, 2),
            "error": error[:100] if error else ""
        })

    def log_state_change(self, before: str, after: str, verified: bool):
        self.info("STATE_CHANGE", {
            "before": before[:50],
            "after": after[:50],
            "verified": verified
        })

    def log_user_input(self, input_text: str, masked: bool = True):
        if masked and len(input_text) > 100:
            display = input_text[:100] + "..."
        else:
            display = input_text
        self.debug(f"USER_INPUT: {display}")

    def log_retry(self, attempt: int, max_attempts: int, reason: str):
        self.warning(f"RETRY: attempt {attempt}/{max_attempts}", {"reason": reason})

    def log_circuit_breaker(self, triggered: bool, reason: str, state: str = ""):
        level = "WARNING" if triggered else "INFO"
        self.logger.log(
            logging.WARNING if triggered else logging.INFO,
            self._format_message(level, "CIRCUIT_BREAKER", {
                "triggered": triggered,
                "reason": reason,
                "state": state
            })
        )


_correlation_logger = CorrelationLogger.get_instance()


def get_correlation_logger():
    return _correlation_logger


def with_correlation_id(func):
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        logger = get_correlation_logger()
        cid = logger.new_correlation_id()
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Exception in {func.__name__}", {"error": str(e)})
            raise
        finally:
            logger.correlation_id = None

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        logger = get_correlation_logger()
        cid = logger.new_correlation_id()
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            logger.error(f"Exception in {func.__name__}", {"error": str(e)})
            raise
        finally:
            logger.correlation_id = None

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper


class LogContext:
    def __init__(self, **context):
        self.context = context
        self.logger = get_correlation_logger()
        self.old_context = {}

    def __enter__(self):
        for key, value in self.context.items():
            self.old_context[key] = self.logger._context.get(key)
            self.logger.set_context(key, value)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for key in self.context.keys():
            if self.old_context.get(key) is not None:
                self.logger.set_context(key, self.old_context[key])
            elif key in self.logger._context:
                del self.logger._context[key]
        return False