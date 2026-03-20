import os
import json
import time
import hashlib
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
import logging


class MemoryType(Enum):
    WORKFLOW_STEP = "workflow_step"
    USER_PREFERENCE = "user_preference"
    TASK_RESULT = "task_result"
    ERROR_PATTERN = "error_pattern"
    SCREENSHOT_CONTEXT = "screenshot_context"


@dataclass
class MemoryEntry:
    content: str
    memory_type: MemoryType
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None


class SimpleVectorStore:
    def __init__(self, storage_path: str = "workspace/memory"):
        self.storage_path = storage_path
        self.memory_file = os.path.join(storage_path, "memory_store.json")
        self.index_file = os.path.join(storage_path, "memory_index.json")
        self.entries: List[MemoryEntry] = []
        self.type_index: Dict[MemoryType, List[int]] = {}
        os.makedirs(storage_path, exist_ok=True)
        self._load()

    def _load(self):
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.entries = [
                        MemoryEntry(
                            content=e["content"],
                            memory_type=MemoryType(e["memory_type"]),
                            timestamp=e["timestamp"],
                            metadata=e.get("metadata", {}),
                            embedding=e.get("embedding")
                        )
                        for e in data.get("entries", [])
                    ]
                    self._rebuild_index()
                    logging.info(f"Loaded {len(self.entries)} memory entries")
            except Exception as e:
                logging.error(f"Failed to load memory store: {e}")
                self.entries = []

    def _save(self):
        try:
            data = {
                "entries": [
                    {
                        "content": e.content,
                        "memory_type": e.memory_type.value,
                        "timestamp": e.timestamp,
                        "metadata": e.metadata,
                        "embedding": e.embedding
                    }
                    for e in self.entries
                ]
            }
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save memory store: {e}")

    def _rebuild_index(self):
        self.type_index = {mem_type: [] for mem_type in MemoryType}
        for i, entry in enumerate(self.entries):
            self.type_index[entry.memory_type].append(i)

    def _simple_embed(self, text: str) -> List[float]:
        words = text.lower().split()
        embedding = [0.0] * 128
        for i, word in enumerate(words[:128]):
            hash_val = int(hashlib.md5(word.encode()).hexdigest(), 16)
            embedding[i % 128] += (hash_val % 1000) / 1000.0

        norm = sum(e * e for e in embedding) ** 0.5
        if norm > 0:
            embedding = [e / norm for e in embedding]
        return embedding

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        return sum(x * y for x, y in zip(a, b))

    def add(
        self,
        content: str,
        memory_type: MemoryType,
        metadata: Optional[Dict[str, Any]] = None
    ) -> int:
        entry = MemoryEntry(
            content=content,
            memory_type=memory_type,
            timestamp=time.time(),
            metadata=metadata or {},
            embedding=self._simple_embed(content)
        )
        self.entries.append(entry)
        self.type_index[memory_type].append(len(self.entries) - 1)

        if len(self.entries) > 1000:
            self.entries = self.entries[-500:]
            self._rebuild_index()

        self._save()
        return len(self.entries) - 1

    def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5,
        min_score: float = 0.1
    ) -> List[Dict[str, Any]]:
        query_embedding = self._simple_embed(query)

        candidate_indices = list(range(len(self.entries)))
        if memory_type:
            candidate_indices = self.type_index.get(memory_type, [])

        scores = []
        for idx in candidate_indices:
            entry = self.entries[idx]
            if entry.embedding:
                score = self._cosine_similarity(query_embedding, entry.embedding)
                if score >= min_score:
                    scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:limit]:
            entry = self.entries[idx]
            results.append({
                "content": entry.content,
                "type": entry.memory_type.value,
                "timestamp": entry.timestamp,
                "score": score,
                "metadata": entry.metadata
            })

        return results

    def get_recent(self, memory_type: Optional[MemoryType] = None, limit: int = 10) -> List[Dict[str, Any]]:
        indices = list(range(len(self.entries)))
        if memory_type:
            indices = self.type_index.get(memory_type, [])

        recent_indices = sorted(indices, key=lambda i: self.entries[i].timestamp, reverse=True)

        results = []
        for idx in recent_indices[:limit]:
            entry = self.entries[idx]
            results.append({
                "content": entry.content,
                "type": entry.memory_type.value,
                "timestamp": entry.timestamp,
                "metadata": entry.metadata
            })

        return results

    def get_workflow_history(self, max_steps: int = 50) -> List[str]:
        recent = self.get_recent(MemoryType.WORKFLOW_STEP, limit=max_steps)
        return [r["content"] for r in recent]

    def add_workflow_step(self, step: str, task_id: str, step_number: int):
        return self.add(
            content=f"[Task: {task_id}] Step {step_number}: {step}",
            memory_type=MemoryType.WORKFLOW_STEP,
            metadata={"task_id": task_id, "step": step_number}
        )

    def add_error_pattern(self, error: str, context: str, suggested_fix: str = ""):
        return self.add(
            content=f"Error: {error} | Context: {context} | Fix: {suggested_fix}",
            memory_type=MemoryType.ERROR_PATTERN,
            metadata={"error": error, "context": context}
        )

    def find_similar_error(self, error: str) -> Optional[Dict[str, Any]]:
        results = self.search(error, memory_type=MemoryType.ERROR_PATTERN, limit=1)
        if results and results[0]["score"] > 0.7:
            return results[0]
        return None

    def clear_old_entries(self, max_age_days: int = 30):
        cutoff = time.time() - (max_age_days * 86400)
        old_count = len(self.entries)
        self.entries = [e for e in self.entries if e.timestamp > cutoff or e.memory_type == MemoryType.USER_PREFERENCE]
        self._rebuild_index()
        removed = old_count - len(self.entries)
        if removed > 0:
            self._save()
            logging.info(f"Cleared {removed} old memory entries")
        return removed


class WorkflowMemory:
    def __init__(self):
        self.store = SimpleVectorStore()
        self.current_task_id: Optional[str] = None
        self.current_task_steps: List[str] = []

    def start_task(self, task_description: str) -> str:
        import uuid
        self.current_task_id = str(uuid.uuid4())[:8]
        self.store.add_workflow_step(
            f"Task started: {task_description}",
            self.current_task_id,
            0
        )
        return self.current_task_id

    def add_step(self, step_description: str):
        if not self.current_task_id:
            self.start_task("Unknown")

        step_num = len(self.current_task_steps) + 1
        self.current_task_steps.append(step_description)

        self.store.add_workflow_step(
            step_description,
            self.current_task_id,
            step_num
        )

    def end_task(self, result: str = "completed"):
        if self.current_task_id:
            self.store.add_workflow_step(
                f"Task ended: {result}",
                self.current_task_id,
                len(self.current_task_steps) + 1
            )
        self.current_task_id = None
        self.current_task_steps = []

    def get_context(self, query: str, limit: int = 5) -> str:
        results = self.store.search(query, limit=limit)

        if not results:
            return ""

        context = "Relevant past context:\n"
        for r in results:
            age = (time.time() - r["timestamp"]) / 3600
            context += f"- [{age:.1f}h ago] {r['content']}\n"

        return context

    def get_full_workflow(self) -> List[str]:
        return self.store.get_workflow_history()


_memory_instance: Optional[WorkflowMemory] = None


def get_workflow_memory() -> WorkflowMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = WorkflowMemory()
    return _memory_instance