import asyncio
import logging
import time
from typing import List, Optional, Dict, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from abc import ABC, abstractmethod
import threading


class AgentState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    ERROR = "error"
    STOPPED = "stopped"


class AgentMessage:
    def __init__(self, sender: str, content: Any, msg_type: str = "info"):
        self.sender = sender
        self.content = content
        self.type = msg_type
        self.timestamp = time.time()


@dataclass
class AgentTask:
    task_id: str
    description: str
    priority: int = 0
    state: AgentState = AgentState.IDLE
    result: Optional[Any] = None
    error: Optional[str] = None


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.state = AgentState.IDLE
        self.inbox: List[AgentMessage] = []
        self.outbox: List[AgentMessage] = []
        self._lock = threading.RLock()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @abstractmethod
    async def think(self) -> Any:
        pass

    async def run(self):
        self._running = True
        self.state = AgentState.RUNNING
        while self._running:
            try:
                result = await self.think()
                if result is not None:
                    msg = AgentMessage(self.name, result, "result")
                    self._add_outbox(msg)
            except Exception as e:
                logging.error(f"{self.name} error: {e}")
                self.state = AgentState.ERROR
                await asyncio.sleep(1)

    def stop(self):
        self._running = False
        self.state = AgentState.STOPPED

    def send_message(self, recipient: str, content: Any, msg_type: str = "info") -> AgentMessage:
        msg = AgentMessage(self.name, content, msg_type)
        return msg

    def _add_outbox(self, msg: AgentMessage):
        with self._lock:
            self.outbox.append(msg)

    def get_messages(self) -> List[AgentMessage]:
        with self._lock:
            messages = self.outbox.copy()
            self.outbox.clear()
            return messages


class MonitorAgent(BaseAgent):
    def __init__(self, desktop_state=None):
        super().__init__("MonitorAgent")
        self._desktop_state = desktop_state
        self._last_screen_hash = None
        self._last_state = None
        self._change_callbacks: List[Callable] = []
        self._monitoring = False
        self._last_check = 0

    def add_change_callback(self, callback: Callable):
        self._change_callbacks.append(callback)

    async def think(self) -> Optional[Dict[str, Any]]:
        if not self._monitoring:
            await asyncio.sleep(0.5)
            return None

        if not self._desktop_state:
            await asyncio.sleep(1)
            return None

        try:
            self._desktop_state.update(force=True)
            current_state = self._desktop_state.get_state_summary()

            state_hash = hash(current_state)

            if state_hash != self._last_screen_hash and self._last_screen_hash is not None:
                change = {
                    "type": "state_changed",
                    "previous_hash": self._last_screen_hash,
                    "current_hash": state_hash,
                    "state": current_state,
                    "timestamp": time.time()
                }

                for callback in self._change_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(callback):
                            await callback(change)
                        else:
                            callback(change)
                    except Exception as e:
                        logging.error(f"Change callback error: {e}")

                self._last_screen_hash = state_hash
                return change

            self._last_screen_hash = state_hash

        except Exception as e:
            logging.debug(f"Monitor agent error: {e}")

        await asyncio.sleep(0.3)
        return None


class ExecutorAgent(BaseAgent):
    def __init__(self, task_planner=None):
        super().__init__("ExecutorAgent")
        self._task_planner = task_planner
        self._current_workflow = []
        self._paused = False

    async def think(self) -> Optional[Dict[str, Any]]:
        if self._paused:
            await asyncio.sleep(0.5)
            return None

        if not self._current_workflow:
            await asyncio.sleep(0.2)
            return None

        if self._task_planner:
            try:
                next_step = self._current_workflow[0]
                result = await self._execute_step(next_step)
                self._current_workflow.pop(0)
                return {
                    "type": "step_completed",
                    "step": next_step,
                    "result": result
                }
            except Exception as e:
                logging.error(f"Executor error: {e}")
                return {
                    "type": "step_failed",
                    "step": self._current_workflow[0] if self._current_workflow else "unknown",
                    "error": str(e)
                }

        return None

    async def _execute_step(self, step: Dict[str, Any]) -> Any:
        action = step.get("action")
        target = step.get("target")
        params = step.get("parameters", {})

        if not self._task_planner:
            return "No task planner"

        tool = self._task_planner._tools.get(action)
        if not tool:
            return f"Unknown action: {action}"

        try:
            if asyncio.iscoroutinefunction(tool):
                result = await tool(target=target, **params)
            else:
                result = await asyncio.to_thread(tool, target=target, **params)
            return result
        except Exception as e:
            return f"Execution error: {e}"

    def set_workflow(self, workflow: List[Dict[str, Any]]):
        self._current_workflow = workflow.copy()

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False


class CoordinatorAgent:
    def __init__(self):
        self.agents: Dict[str, BaseAgent] = {}
        self.tasks: Dict[str, AgentTask] = {}
        self._message_queues: Dict[str, List[AgentMessage]] = {}
        self._running = False
        self._coordinator_task: Optional[asyncio.Task] = None
        self._monitor: Optional[MonitorAgent] = None
        self._executor: Optional[ExecutorAgent] = None

    def register_agent(self, agent: BaseAgent):
        self.agents[agent.name] = agent
        self._message_queues[agent.name] = []

    def set_monitor(self, monitor: MonitorAgent):
        self._monitor = monitor
        monitor.add_change_callback(self._on_monitor_change)

    def set_executor(self, executor: ExecutorAgent):
        self._executor = executor

    async def _on_monitor_change(self, change: Dict[str, Any]):
        if self._executor and change.get("type") == "state_changed":
            await self._deliver_message("ExecutorAgent", change, "monitor_event")

    async def start(self):
        self._running = True

        for agent in self.agents.values():
            asyncio.create_task(agent.run())

        self._coordinator_task = asyncio.create_task(self._coordinate())

    async def stop(self):
        self._running = False
        for agent in self.agents.values():
            agent.stop()
        if self._coordinator_task:
            self._coordinator_task.cancel()

    async def _coordinate(self):
        while self._running:
            try:
                for agent_name, agent in self.agents.items():
                    messages = agent.get_messages()
                    for msg in messages:
                        await self._route_message(msg)
            except Exception as e:
                logging.error(f"Coordinator error: {e}")
            await asyncio.sleep(0.1)

    async def _route_message(self, message: AgentMessage):
        pass

    async def _deliver_message(self, recipient: str, content: Any, msg_type: str = "info"):
        if recipient in self._message_queues:
            msg = AgentMessage("Coordinator", content, msg_type)
            self._message_queues[recipient].append(msg)

    def submit_task(self, description: str, priority: int = 0) -> str:
        import uuid
        task_id = str(uuid.uuid4())[:8]
        task = AgentTask(
            task_id=task_id,
            description=description,
            priority=priority
        )
        self.tasks[task_id] = task
        return task_id

    def get_task_result(self, task_id: str) -> Optional[Any]:
        task = self.tasks.get(task_id)
        if task:
            return task.result
        return None

    async def execute_workflow(self, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self._executor:
            return [{"error": "No executor agent"}]

        self._executor.set_workflow(steps)
        results = []

        while self._executor._current_workflow or self._executor.state == AgentState.RUNNING:
            await asyncio.sleep(0.2)

            messages = self._executor.get_messages()
            for msg in messages:
                if msg.type == "result":
                    results.append(msg.content)

            if self._executor.state == AgentState.ERROR:
                break

        return results


class MultiAgentOrchestrator:
    def __init__(self, desktop_state=None, task_planner=None):
        self.coordinator = CoordinatorAgent()

        self._monitor = MonitorAgent(desktop_state)
        self._executor = ExecutorAgent(task_planner)

        self.coordinator.register_agent(self._monitor)
        self.coordinator.register_agent(self._executor)
        self.coordinator.set_monitor(self._monitor)
        self.coordinator.set_executor(self._executor)

        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        await self.coordinator.start()

    async def stop(self):
        self._running = False
        await self.coordinator.stop()

    def start_monitoring(self):
        self._monitor._monitoring = True

    def stop_monitoring(self):
        self._monitor._monitoring = False

    async def execute_parallel(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        sequential_results = []

        for step in steps:
            if self._executor:
                self._executor.set_workflow([step])
                while self._executor._current_workflow:
                    await asyncio.sleep(0.1)
                    messages = self._executor.get_messages()
                    if messages:
                        sequential_results.append(messages[0].content)

        return {
            "completed": len(sequential_results),
            "results": sequential_results
        }

    async def execute_parallel_batched(self, steps: List[Dict[str, Any]], max_concurrent: int = 3) -> Dict[str, Any]:
        if not self._executor or not self._executor._task_planner:
            return {"error": "No executor or task planner"}

        results = []
        step_dependencies = {}

        for i, step in enumerate(steps):
            deps = step.get("depends_on", [])
            step_dependencies[i] = deps

        completed = set()
        running_tasks = []

        async def execute_step_with_deps(step_idx: int, step: Dict[str, Any]) -> Dict[str, Any]:
            deps = step_dependencies.get(step_idx, [])
            for dep_idx in deps:
                while dep_idx not in completed:
                    await asyncio.sleep(0.1)

            result = await self._execute_single_step(step)
            completed.add(step_idx)
            return {"index": step_idx, "result": result}

        try:
            batch_idx = 0
            while batch_idx < len(steps) or running_tasks:
                while len(running_tasks) < max_concurrent and batch_idx < len(steps):
                    task = asyncio.create_task(execute_step_with_deps(batch_idx, steps[batch_idx]))
                    running_tasks.append(task)
                    batch_idx += 1

                if running_tasks:
                    done, running_tasks = await asyncio.wait(running_tasks, timeout=0.2)
                    for completed_task in done:
                        results.append(completed_task.result())

            results.sort(key=lambda x: x["index"])

            return {
                "completed": len(results),
                "results": [r["result"] for r in results]
            }

        except Exception as e:
            logging.error(f"Parallel execution error: {e}")
            return {"error": str(e), "completed": len(results)}

    async def _execute_single_step(self, step: Dict[str, Any]) -> Any:
        if not self._executor or not self._executor._task_planner:
            return "No executor"

        tool = self._executor._task_planner._tools.get(step.get("action"))
        if not tool:
            return f"Unknown action: {step.get('action')}"

        try:
            if asyncio.iscoroutinefunction(tool):
                result = await tool(target=step.get("target"), **step.get("parameters", {}))
            else:
                result = await asyncio.to_thread(tool, target=step.get("target"), **step.get("parameters", {}))
            return result
        except Exception as e:
            return f"Error: {e}"

    def get_monitor_state(self) -> Dict[str, Any]:
        if self._monitor._desktop_state:
            return {
                "monitoring": self._monitor._monitoring,
                "active_window": self._monitor._desktop_state.active_window.title if self._monitor._desktop_state.active_window else None,
                "windows": len(self._monitor._desktop_state.windows)
            }
        return {"monitoring": False}


_orchestrator_instance: Optional[MultiAgentOrchestrator] = None


def get_orchestrator(desktop_state=None, task_planner=None) -> MultiAgentOrchestrator:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = MultiAgentOrchestrator(desktop_state, task_planner)
    return _orchestrator_instance