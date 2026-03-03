"""
Execution context — correlation IDs for distributed tracing.

Every scheduled job and every API-triggered run gets a unique
ExecutionContext. The context is passed explicitly through the
call chain (notion → reminder → telegram) so every log line
for a given run shares the same execution_id.

This makes it trivial to filter logs for a single run:
    grep '"execution_id":"abc-123"' logs/ops_agent.log

Usage
─────
    ctx = ExecutionContext.new(job="morning_brief")
    log = ctx.logger("app.services.reminder_service")
    log.info("Starting")          # → {"execution_id": "abc-123", "msg": "Starting", ...}
"""

import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ExecutionContext:
    execution_id: str
    job: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def new(cls, job: str) -> "ExecutionContext":
        return cls(execution_id=str(uuid.uuid4()), job=job)

    def logger(self, name: str) -> logging.Logger:
        """Return a LoggerAdapter that stamps execution_id onto every record."""
        base = logging.getLogger(name)
        return logging.LoggerAdapter(base, extra={"execution_id": self.execution_id})

    def elapsed_ms(self) -> int:
        delta = datetime.now(timezone.utc) - self.started_at
        return int(delta.total_seconds() * 1000)
