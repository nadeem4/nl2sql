"""What ``engine.check()`` reports, and the error it raises when the engine cannot start.

See docs/architecture/configuration_v2.md, "Lifecycle". Plain dataclasses, so
``from nl2sql import CheckReport`` costs nothing at import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


@dataclass(frozen=True)
class DatasourceStatus:
    """One datasource after ``check()``: ``ready``, or ``failed`` at a ``stage`` with a reason and a fix."""

    id: str
    status: Literal["ready", "failed"]
    required: bool = False
    stage: Optional[str] = None
    reason: Optional[str] = None
    fix: Optional[str] = None

    def __str__(self) -> str:
        if self.status == "ready":
            return f"{self.id}: ready"
        required = " (required)" if self.required else ""
        lines = [f"{self.id}{required}: failed at {self.stage}: {self.reason}"]
        if self.fix:
            lines.append(f"  Fix: {self.fix}")
        return "\n".join(lines)


@dataclass(frozen=True)
class CheckReport:
    """What ``engine.check()`` found.

    ``datasources`` has one status per configured datasource. ``llm`` maps
    each agent whose API key does not resolve to the reason; it is empty when
    every key resolves. ``index`` is the index status from ``index_health()``
    (``ok``, ``empty``, ``stale`` or ``missing``).
    """

    datasources: List[DatasourceStatus] = field(default_factory=list)
    llm: Dict[str, str] = field(default_factory=dict)
    index: str = "missing"

    @property
    def ok(self) -> bool:
        """Every datasource is ready, every key resolves and the index is built."""
        return all(d.status == "ready" for d in self.datasources) and not self.llm and self.index == "ok"

    def __str__(self) -> str:
        lines = ["Datasources:"]
        lines += [f"  {line}" for d in self.datasources for line in str(d).splitlines()] or ["  none"]
        lines.append("LLM keys:")
        lines += [f"  {agent}: {reason}" for agent, reason in self.llm.items()] or ["  every key resolves"]
        lines.append(f"Index: {self.index}")
        return "\n".join(lines)


class EngineNotReady(RuntimeError):
    """``check()`` found a ``required`` datasource that failed, or no datasource ready at all."""

    def __init__(self, message: str, report: CheckReport):
        self.report = report
        super().__init__(f"{message}\n{report}")
