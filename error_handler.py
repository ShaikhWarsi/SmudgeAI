import time
import logging
from typing import Dict, Optional, Any, List, Callable
from dataclasses import dataclass
from enum import Enum
from abc import ABC, abstractmethod


class ErrorSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(Enum):
    RETRYABLE = "retryable"
    FATAL = "fatal"
    ESCALATION = "escalation"
    UNKNOWN = "unknown"


@dataclass
class ErrorClass:
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    cause: str
    suggested_fix: str
    retry_strategy: Optional[Dict[str, Any]] = None


class RetryStrategy:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 0.5,
        max_delay: float = 10.0,
        exponential_base: float = 2.0,
        jitter: bool = True
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        import random
        delay = min(self.base_delay * (self.exponential_base ** attempt), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay


class ErrorClassifier:
    RETRY_PATTERNS = {
        "timeout": {
            "patterns": ["timeout", "timed out", "took too long", "request timeout"],
            "category": ErrorCategory.RETRYABLE,
            "severity": ErrorSeverity.MEDIUM,
            "cause": "Operation exceeded time limit",
            "suggested_fix": "Retry with exponential backoff"
        },
        "rate_limit": {
            "patterns": ["rate limit", "429", "too many requests", "throttl"],
            "category": ErrorCategory.RETRYABLE,
            "severity": ErrorSeverity.HIGH,
            "cause": "API rate limit exceeded",
            "suggested_fix": "Wait and retry with longer backoff"
        },
        "connection": {
            "patterns": ["connection", "network", "unreachable", "refused", "reset"],
            "category": ErrorCategory.RETRYABLE,
            "severity": ErrorSeverity.MEDIUM,
            "cause": "Network connectivity issue",
            "suggested_fix": "Check network, retry after delay"
        },
        "resource_busy": {
            "patterns": ["busy", "in use", "locked", "occupied"],
            "category": ErrorCategory.RETRYABLE,
            "severity": ErrorSeverity.LOW,
            "cause": "Resource temporarily unavailable",
            "suggested_fix": "Retry after brief delay"
        }
    }

    FATAL_PATTERNS = {
        "not_found": {
            "patterns": ["not found", "does not exist", "file not found", "no such file"],
            "category": ErrorCategory.FATAL,
            "severity": ErrorSeverity.HIGH,
            "cause": "Target resource does not exist",
            "suggested_fix": "Verify target exists before retrying"
        },
        "permission": {
            "patterns": ["permission denied", "access denied", "unauthorized", "forbidden"],
            "category": ErrorCategory.FATAL,
            "severity": ErrorSeverity.HIGH,
            "cause": "Insufficient permissions",
            "suggested_fix": "Check user permissions"
        },
        "invalid_input": {
            "patterns": ["invalid", "malformed", "bad request", "400", "type error"],
            "category": ErrorCategory.FATAL,
            "severity": ErrorSeverity.MEDIUM,
            "cause": "Invalid input parameters",
            "suggested_fix": "Fix input parameters"
        },
        "syntax_error": {
            "patterns": ["syntax error", "parse error", "unexpected token", "indent"],
            "category": ErrorCategory.FATAL,
            "severity": ErrorSeverity.MEDIUM,
            "cause": "Code or input has syntax error",
            "suggested_fix": "Fix syntax before retry"
        }
    }

    ESCALATION_PATTERNS = {
        "ui_blocked": {
            "patterns": ["blocked", "modal", "dialog", "popup", "alert"],
            "category": ErrorCategory.ESCALATION,
            "severity": ErrorSeverity.HIGH,
            "cause": "UI element is blocked by another dialog",
            "suggested_fix": "Close dialog or request human intervention"
        },
        "unrecoverable": {
            "patterns": ["unrecoverable", "fatal", "crash", "segmentation fault"],
            "category": ErrorCategory.ESCALATION,
            "severity": ErrorSeverity.CRITICAL,
            "cause": "System in unrecoverable state",
            "suggested_fix": "Restart application or escalate to human"
        }
    }

    def __init__(self):
        self.error_history: List[Dict] = []
        self.classification_cache: Dict[str, ErrorClass] = {}

    def classify(self, error: str) -> ErrorClass:
        error_lower = error.lower()

        if error in self.classification_cache:
            return self.classification_cache[error]

        for category_name, category_data in {**self.RETRY_PATTERNS, **self.FATAL_PATTERNS, **self.ESCALATION_PATTERNS}.items():
            for pattern in category_data["patterns"]:
                if pattern.lower() in error_lower:
                    error_class = ErrorClass(
                        category=category_data["category"],
                        severity=category_data["severity"],
                        message=error,
                        cause=category_data["cause"],
                        suggested_fix=category_data["suggested_fix"],
                        retry_strategy=self._get_retry_strategy(category_data["category"])
                    )
                    self.classification_cache[error] = error_class
                    self._record_error(error_class)
                    return error_class

        unknown_class = ErrorClass(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            message=error,
            cause="Unknown error type",
            suggested_fix="Investigate error manually"
        )
        self.classification_cache[error] = unknown_class
        self._record_error(unknown_class)
        return unknown_class

    def _get_retry_strategy(self, category: ErrorCategory) -> Optional[Dict[str, Any]]:
        if category == ErrorCategory.RETRYABLE:
            return {
                "max_retries": 3,
                "base_delay": 1.0,
                "max_delay": 30.0,
                "exponential_base": 2.0
            }
        elif category == ErrorCategory.ESCALATION:
            return {
                "max_retries": 1,
                "base_delay": 2.0,
                "max_delay": 5.0,
                "exponential_base": 1.5
            }
        return None

    def _record_error(self, error_class: ErrorClass):
        self.error_history.append({
            "error": error_class.message,
            "category": error_class.category.value,
            "severity": error_class.severity.value,
            "timestamp": time.time()
        })

        if len(self.error_history) > 100:
            self.error_history = self.error_history[-50:]

    def get_error_stats(self) -> Dict[str, Any]:
        stats = {
            "total_errors": len(self.error_history),
            "by_category": {},
            "by_severity": {},
            "recent": self.error_history[-10:]
        }

        for entry in self.error_history:
            cat = entry["category"]
            sev = entry["severity"]
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            stats["by_severity"][sev] = stats["by_severity"].get(sev, 0) + 1

        return stats


class ErrorHandler:
    def __init__(self):
        self.classifier = ErrorClassifier()
        self.handlers: Dict[ErrorCategory, Callable] = {}
        self._register_default_handlers()

    def _register_default_handlers(self):
        self.handlers[ErrorCategory.RETRYABLE] = self._handle_retryable
        self.handlers[ErrorCategory.FATAL] = self._handle_fatal
        self.handlers[ErrorCategory.ESCALATION] = self._handle_escalation
        self.handlers[ErrorCategory.UNKNOWN] = self._handle_unknown

    def _handle_retryable(self, error: ErrorClass, context: Dict) -> Dict[str, Any]:
        return {
            "action": "retry",
            "message": error.suggested_fix,
            "delay": self._calculate_delay(error)
        }

    def _handle_fatal(self, error: ErrorClass, context: Dict) -> Dict[str, Any]:
        return {
            "action": "abort",
            "message": f"FATAL: {error.suggested_fix}",
            "error": error.message
        }

    def _handle_escalation(self, error: ErrorClass, context: Dict) -> Dict[str, Any]:
        return {
            "action": "escalate",
            "message": f"ESCALATION: {error.suggested_fix}",
            "requires_human": True
        }

    def _handle_unknown(self, error: ErrorClass, context: Dict) -> Dict[str, Any]:
        return {
            "action": "retry",
            "message": error.suggested_fix,
            "delay": 2.0,
            "max_retries": 1
        }

    def _calculate_delay(self, error: ErrorClass) -> float:
        if error.retry_strategy:
            base = error.retry_strategy.get("base_delay", 1.0)
            return base
        return 1.0

    def handle(self, error: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        error_class = self.classifier.classify(error)
        handler = self.handlers.get(error_class.category, self._handle_unknown)
        return handler(error_class, context or {})

    def should_retry(self, error: str) -> bool:
        error_class = self.classifier.classify(error)
        return error_class.category == ErrorCategory.RETRYABLE

    def should_escalate(self, error: str) -> bool:
        error_class = self.classifier.classify(error)
        return error_class.category in [ErrorCategory.ESCALATION, ErrorCategory.FATAL]

    def get_stats(self) -> Dict[str, Any]:
        return self.classifier.get_error_stats()


class ResilientOperation:
    def __init__(self, error_handler: ErrorHandler = None, max_total_retries: int = 5):
        self.error_handler = error_handler or ErrorHandler()
        self.max_total_retries = max_total_retries

    async def execute(
        self,
        operation: Callable,
        *args,
        **kwargs
    ) -> Dict[str, Any]:
        attempt = 0
        last_error = None

        while attempt < self.max_total_retries:
            try:
                if asyncio.iscoroutinefunction(operation):
                    result = await operation(*args, **kwargs)
                else:
                    result = operation(*args, **kwargs)

                return {
                    "success": True,
                    "result": result,
                    "attempts": attempt + 1
                }

            except Exception as e:
                last_error = str(e)
                error_info = self.error_handler.handle(last_error)

                if error_info.get("action") == "abort":
                    return {
                        "success": False,
                        "error": last_error,
                        "action": "abort",
                        "attempts": attempt + 1
                    }

                if error_info.get("action") == "escalate":
                    return {
                        "success": False,
                        "error": last_error,
                        "action": "escalate",
                        "requires_human": True,
                        "attempts": attempt + 1
                    }

                if error_info.get("action") == "retry":
                    delay = error_info.get("delay", 1.0)
                    if "max_retries" in error_info:
                        if attempt >= error_info["max_retries"]:
                            break

                    await asyncio.sleep(delay)
                    attempt += 1

        return {
            "success": False,
            "error": last_error,
            "action": "max_retries_exceeded",
            "attempts": attempt
        }


import asyncio


_error_handler_instance: Optional[ErrorHandler] = None


def get_error_handler() -> ErrorHandler:
    global _error_handler_instance
    if _error_handler_instance is None:
        _error_handler_instance = ErrorHandler()
    return _error_handler_instance