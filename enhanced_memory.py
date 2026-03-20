import os
import json
import time
import hashlib
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
import threading
import asyncio


class MemoryType(Enum):
    WORKFLOW_STEP = "workflow_step"
    USER_PREFERENCE = "user_preference"
    TASK_RESULT = "task_result"
    ERROR_PATTERN = "error_pattern"
    SCREENSHOT_CONTEXT = "screenshot_context"
    CODE_SNIPPET = "code_snippet"


@dataclass
class MemoryEntry:
    content: str
    memory_type: MemoryType
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    step_number: Optional[int] = None


class ChromaVectorStore:
    def __init__(self, storage_path: str = "workspace/chroma_db"):
        self.storage_path = storage_path
        self._chroma_client = None
        self._collection = None
        self._chroma_available = None
        self._fallback_store: Dict[str, MemoryEntry] = {}
        self._lock = threading.RLock()
        self._init_chroma()

    def _init_chroma(self):
        if self._chroma_available is not None:
            return

        try:
            import chromadb
            from chromadb.config import Settings

            os.makedirs(self.storage_path, exist_ok=True)

            self._chroma_client = chromadb.PersistentClient(
                path=self.storage_path,
                settings=Settings(anonymized_telemetry=False)
            )

            self._collection = self._chroma_client.get_or_create_collection(
                name="memory",
                metadata={"hnsw:space": "cosine"}
            )

            self._chroma_available = True
            logging.info("ChromaDB initialized successfully")

        except ImportError:
            logging.warning("ChromaDB not installed, using fallback dict store")
            self._chroma_available = False
        except Exception as e:
            logging.error(f"Failed to initialize ChromaDB: {e}")
            self._chroma_available = False

    def _generate_id(self, content: str, memory_type: MemoryType) -> str:
        unique_str = f"{content}:{memory_type.value}:{time.time()}"
        return hashlib.sha256(unique_str.encode()).hexdigest()[:16]

    def add(self, entry: MemoryEntry) -> str:
        entry_id = self._generate_id(entry.content, entry.memory_type)

        metadata = {
            "type": entry.memory_type.value,
            "timestamp": entry.timestamp,
            "task_id": entry.task_id or "",
            "step_number": entry.step_number or 0,
        }
        metadata.update(entry.metadata)

        if self._chroma_available and self._collection is not None:
            try:
                self._collection.add(
                    documents=[entry.content],
                    metadatas=[metadata],
                    ids=[entry_id]
                )
                return entry_id
            except Exception as e:
                logging.error(f"ChromaDB add failed: {e}")
                self._chroma_available = False

        with self._lock:
            self._fallback_store[entry_id] = entry
        return entry_id

    def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5,
        min_score: float = 0.0
    ) -> List[Dict[str, Any]]:
        results = []

        if self._chroma_available and self._collection is not None:
            try:
                where_filter = {"type": memory_type.value} if memory_type else None

                query_results = self._collection.query(
                    query_texts=[query],
                    n_results=limit,
                    where=where_filter
                )

                if query_results and query_results.get("documents"):
                    for i, doc in enumerate(query_results["documents"][0]):
                        distance = query_results.get("distances", [[0]])[0][i]
                        score = 1.0 - distance

                        if score >= min_score:
                            metadata = query_results.get("metadatas", [[{}]])[0][i]
                            results.append({
                                "id": query_results["ids"][0][i],
                                "content": doc,
                                "score": score,
                                "type": metadata.get("type", "unknown"),
                                "timestamp": metadata.get("timestamp", 0),
                                "task_id": metadata.get("task_id", ""),
                                "metadata": {k: v for k, v in metadata.items()
                                           if k not in ["type", "timestamp", "task_id", "step_number"]}
                            })

                return results

            except Exception as e:
                logging.error(f"ChromaDB query failed: {e}")
                self._chroma_available = False

        return self._fallback_search(query, memory_type, limit)

    def _fallback_search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        query_words = set(query.lower().split())

        with self._lock:
            entries = list(self._fallback_store.values())

        if memory_type:
            entries = [e for e in entries if e.memory_type == memory_type]

        scored = []
        for entry in entries:
            entry_words = set(entry.content.lower().split())
            overlap = len(query_words & entry_words)
            if overlap > 0:
                score = overlap / max(len(query_words), len(entry_words))
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for entry, score in scored[:limit]:
            results.append({
                "id": self._generate_id(entry.content, entry.memory_type),
                "content": entry.content,
                "score": score,
                "type": entry.memory_type.value,
                "timestamp": entry.timestamp,
                "task_id": entry.task_id or "",
                "metadata": entry.metadata
            })

        return results

    def get_recent(self, memory_type: Optional[MemoryType] = None, limit: int = 10) -> List[Dict[str, Any]]:
        if self._chroma_available and self._collection is not None:
            try:
                where_filter = {"type": memory_type.value} if memory_type else None

                results = self._collection.get(
                    where=where_filter,
                    limit=limit
                )

                if results and results.get("documents"):
                    entries = []
                    for i, doc in enumerate(results["documents"]):
                        metadata = results.get("metadatas", [{}])[i]
                        entries.append({
                            "id": results["ids"][i],
                            "content": doc,
                            "type": metadata.get("type", "unknown"),
                            "timestamp": metadata.get("timestamp", 0),
                            "task_id": metadata.get("task_id", ""),
                            "metadata": {k: v for k, v in metadata.items()
                                       if k not in ["type", "timestamp", "task_id", "step_number"]}
                        })

                    entries.sort(key=lambda x: x["timestamp"], reverse=True)
                    return entries[:limit]

            except Exception as e:
                logging.error(f"ChromaDB get_recent failed: {e}")

        with self._lock:
            entries = list(self._fallback_store.values())

        if memory_type:
            entries = [e for e in entries if e.memory_type == memory_type]

        entries.sort(key=lambda x: x.timestamp, reverse=True)

        results = []
        for entry in entries[:limit]:
            results.append({
                "id": self._generate_id(entry.content, entry.memory_type),
                "content": entry.content,
                "type": entry.memory_type.value,
                "timestamp": entry.timestamp,
                "task_id": entry.task_id or "",
                "metadata": entry.metadata
            })

        return results

    def delete_old_entries(self, max_age_days: int = 30) -> int:
        cutoff = time.time() - (max_age_days * 86400)
        deleted = 0

        if self._chroma_available and self._collection is not None:
            try:
                all_entries = self._collection.get()
                if all_entries and all_entries.get("ids"):
                    ids_to_delete = []
                    for i, metadata in enumerate(all_entries.get("metadatas", [])):
                        if metadata.get("timestamp", 0) < cutoff:
                            if metadata.get("type") != MemoryType.USER_PREFERENCE.value:
                                ids_to_delete.append(all_entries["ids"][i])

                    if ids_to_delete:
                        self._collection.delete(ids=ids_to_delete)
                        deleted = len(ids_to_delete)

            except Exception as e:
                logging.error(f"ChromaDB cleanup failed: {e}")

        with self._lock:
            old_keys = [
                k for k, v in self._fallback_store.items()
                if v.timestamp < cutoff and v.memory_type != MemoryType.USER_PREFERENCE
            ]
            for k in old_keys:
                del self._fallback_store[k]
                deleted += 1

        if deleted > 0:
            logging.info(f"Deleted {deleted} old memory entries")

        return deleted


class WorkflowCheckpoint:
    def __init__(self, checkpoint_dir: str = "workspace/checkpoints"):
        self.checkpoint_dir = checkpoint_dir
        os.makedirs(checkpoint_dir, exist_ok=True)

    def save(
        self,
        task_id: str,
        step_number: int,
        state: Dict[str, Any],
        workflow_steps: List[Dict]
    ) -> str:
        checkpoint = {
            "task_id": task_id,
            "step_number": step_number,
            "timestamp": time.time(),
            "state": state,
            "workflow_steps": workflow_steps,
            "completed_steps": workflow_steps[:step_number] if step_number > 0 else [],
            "remaining_steps": workflow_steps[step_number:] if step_number < len(workflow_steps) else []
        }

        filename = f"{task_id}_step{step_number}.json"
        filepath = os.path.join(self.checkpoint_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, indent=2, default=str)

            latest_link = os.path.join(self.checkpoint_dir, f"{task_id}_latest.json")
            with open(latest_link, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, indent=2, default=str)

            logging.info(f"Checkpoint saved: {task_id} step {step_number}")
            return filepath

        except Exception as e:
            logging.error(f"Failed to save checkpoint: {e}")
            return ""

    def load(self, task_id: str) -> Optional[Dict]:
        latest_link = os.path.join(self.checkpoint_dir, f"{task_id}_latest.json")

        if not os.path.exists(latest_link):
            return None

        try:
            with open(latest_link, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load checkpoint: {e}")
            return None

    def list_checkpoints(self) -> List[Dict]:
        checkpoints = []

        if not os.path.exists(self.checkpoint_dir):
            return checkpoints

        for filename in os.listdir(self.checkpoint_dir):
            if filename.endswith("_latest.json"):
                filepath = os.path.join(self.checkpoint_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        checkpoints.append({
                            "task_id": data.get("task_id"),
                            "step_number": data.get("step_number"),
                            "timestamp": data.get("timestamp"),
                            "filepath": filepath
                        })
                except:
                    pass

        checkpoints.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return checkpoints

    def delete_checkpoint(self, task_id: str) -> bool:
        try:
            pattern = f"{task_id}_*.json"
            for filename in os.listdir(self.checkpoint_dir):
                if filename.startswith(task_id):
                    os.remove(os.path.join(self.checkpoint_dir, filename))
            return True
        except Exception as e:
            logging.error(f"Failed to delete checkpoint: {e}")
            return False


class EnhancedWorkflowMemory:
    def __init__(self):
        self.vector_store = ChromaVectorStore()
        self.checkpoint = WorkflowCheckpoint()
        self.current_task_id: Optional[str] = None
        self.current_step: int = 0
        self.workflow_steps: List[Dict] = []
        self._checkpoint_interval: int = 5

    def start_task(self, task_description: str, steps: List[Dict] = None) -> str:
        import uuid
        self.current_task_id = str(uuid.uuid4())[:8]
        self.current_step = 0
        self.workflow_steps = steps or []

        entry = MemoryEntry(
            content=f"Task started: {task_description}",
            memory_type=MemoryType.WORKFLOW_STEP,
            timestamp=time.time(),
            task_id=self.current_task_id,
            step_number=0
        )
        self.vector_store.add(entry)

        self._save_checkpoint()

        return self.current_task_id

    def add_step(self, step_description: str, result: str = "") -> int:
        if not self.current_task_id:
            self.start_task("Unknown")

        self.current_step += 1

        content = f"Step {self.current_step}: {step_description}"
        if result:
            content += f" | Result: {result}"

        entry = MemoryEntry(
            content=content,
            memory_type=MemoryType.WORKFLOW_STEP,
            timestamp=time.time(),
            task_id=self.current_task_id,
            step_number=self.current_step,
            metadata={"result": result} if result else {}
        )
        self.vector_store.add(entry)

        if self.current_step % self._checkpoint_interval == 0:
            self._save_checkpoint()

        return self.current_step

    def end_task(self, result: str = "completed"):
        if not self.current_task_id:
            return

        entry = MemoryEntry(
            content=f"Task ended: {result}",
            memory_type=MemoryType.WORKFLOW_STEP,
            timestamp=time.time(),
            task_id=self.current_task_id,
            step_number=self.current_step + 1
        )
        self.vector_store.add(entry)

        self._save_checkpoint(final=True)
        self.current_task_id = None
        self.current_step = 0
        self.workflow_steps = []

    def _save_checkpoint(self, final: bool = False):
        if not self.current_task_id:
            return

        if not final and self.current_step % self._checkpoint_interval != 0:
            return

        state = {
            "current_step": self.current_step,
            "completed": final
        }

        self.checkpoint.save(
            task_id=self.current_task_id,
            step_number=self.current_step,
            state=state,
            workflow_steps=self.workflow_steps
        )

    def resume_task(self, task_id: str) -> Optional[Dict]:
        checkpoint = self.checkpoint.load(task_id)
        if not checkpoint:
            return None

        self.current_task_id = checkpoint["task_id"]
        self.current_step = checkpoint["step_number"]
        self.workflow_steps = checkpoint.get("workflow_steps", [])

        return {
            "task_id": self.current_task_id,
            "step_number": self.current_step,
            "workflow_steps": self.workflow_steps,
            "remaining_steps": checkpoint.get("remaining_steps", [])
        }

    def get_context(self, query: str, limit: int = 5) -> str:
        results = self.vector_store.search(query, limit=limit)

        if not results:
            return ""

        context = "Relevant past context:\n"
        for r in results:
            age = (time.time() - r["timestamp"]) / 3600
            context += f"- [{age:.1f}h ago, score={r['score']:.2f}] {r['content']}\n"

        return context

    def get_workflow_history(self, limit: int = 50) -> List[str]:
        entries = self.vector_store.get_recent(MemoryType.WORKFLOW_STEP, limit=limit)
        return [e["content"] for e in entries]

    def add_error_pattern(self, error: str, context: str, suggested_fix: str = ""):
        entry = MemoryEntry(
            content=f"Error: {error} | Context: {context} | Fix: {suggested_fix}",
            memory_type=MemoryType.ERROR_PATTERN,
            timestamp=time.time(),
            metadata={"error": error, "context": context}
        )
        self.vector_store.add(entry)

    def find_similar_error(self, error: str) -> Optional[Dict]:
        results = self.vector_store.search(error, MemoryType.ERROR_PATTERN, limit=1)
        if results and results[0]["score"] > 0.7:
            return results[0]
        return None

    def add_code_snippet(self, code: str, description: str, language: str = ""):
        entry = MemoryEntry(
            content=f"{description}\n```{language}\n{code}\n```",
            memory_type=MemoryType.CODE_SNIPPET,
            timestamp=time.time(),
            metadata={"language": language}
        )
        self.vector_store.add(entry)


_memory_instance: Optional[EnhancedWorkflowMemory] = None


def get_enhanced_memory() -> EnhancedWorkflowMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = EnhancedWorkflowMemory()
    return _memory_instance