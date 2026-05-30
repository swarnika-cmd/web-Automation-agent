"""
Event Log — Centralized logging for agent thinking, tool executions, errors, and status updates.

Provides AgentEvent and EventLog classes to capture and serialize agent activity.
This is the single source of truth for the HTML report generator and the live dashboard.
"""

import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


@dataclass
class AgentEvent:
    timestamp: float          # Epoch timestamp (time.time())
    step: int                 # Loop step number
    event_type: str           # "thinking", "tool_call", "observation", "error", "retry"
    tool_name: Optional[str]  # Name of the tool invoked (if any)
    tool_args: Optional[Dict[str, Any]] # Arguments passed to the tool (if any)
    message: str              # Description / logs
    screenshot_path: Optional[str] = None # Path to screenshots taken (relative)
    duration_ms: Optional[int] = None     # Execution duration in milliseconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventLog:
    """
    Centralized event logger for AutoCli.
    """
    def __init__(self, task_description: str = ""):
        self.events: List[AgentEvent] = []
        self.start_time: float = time.time()
        self.task_description: str = task_description
        self.status: str = "running"  # "running", "success", "failed", "stopped"
        self.end_time: Optional[float] = None

    def add(
        self,
        step: int,
        event_type: str,
        message: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        screenshot_path: Optional[str] = None,
        duration_ms: Optional[int] = None
    ) -> AgentEvent:
        """
        Record a new event in the log.
        """
        event = AgentEvent(
            timestamp=time.time(),
            step=step,
            event_type=event_type,
            tool_name=tool_name,
            tool_args=tool_args,
            message=message,
            screenshot_path=screenshot_path,
            duration_ms=duration_ms
        )
        self.events.append(event)
        return event

    def finish(self, status: str = "success") -> None:
        """Mark the task as completed."""
        self.end_time = time.time()
        self.status = status

    def get_duration(self) -> float:
        """Get elapsed time in seconds."""
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the entire event log for transmission or saving."""
        return {
            "task_description": self.task_description,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.get_duration(),
            "events": [event.to_dict() for event in self.events]
        }
