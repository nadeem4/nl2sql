"""What ``engine.check()`` reports, and the error it raises when the engine cannot start.

See docs/architecture/configuration_v2.md, "Lifecycle". Plain dataclasses, so
``from nl2sql import CheckReport`` costs nothing at import.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple


@dataclass(frozen=True)
class DatasourceStatus:
    """One datasource after ``check()``: ``ready``, or ``failed`` at a ``stage`` with a reason and a fix.

    ``stage`` is the adapter's: ``config``, ``driver``, ``network``, ``auth``,
    ``database`` or ``permission``, or None when the adapter cannot tell.
    ``warnings`` are about a ready datasource, such as a session the adapter
    cannot make read-only.
    """

    id: str
    status: Literal["ready", "failed"]
    required: bool = False
    stage: Optional[str] = None
    reason: Optional[str] = None
    fix: Optional[str] = None
    warnings: Tuple[str, ...] = ()

    def __str__(self) -> str:
        required = " (required)" if self.required else ""
        if self.status == "ready":
            lines = [f"{self.id}: ready"]
        else:
            stage = f" at {self.stage}" if self.stage else ""
            lines = [f"{self.id}{required}: failed{stage}: {self.reason}"]
        if self.fix:
            lines.append(f"  Fix: {self.fix}")
        lines += [f"  Warning: {warning}" for warning in self.warnings]
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

    def ready(self, *, keys_optional: bool) -> bool:
        """Whether the engine can answer now: what ``GET /ready`` reports (D208).

        Every required datasource is ready and at least one datasource is, every agent's key resolves unless
        ``keys_optional`` (a caller brings its own, or every agent is on ``replay``), and the index is ``ok``
        or ``stale`` (a glossary changed since the build; it still answers). An optional datasource that failed
        does not make the engine not ready; ``problems()`` still lists it.
        """
        up = [d for d in self.datasources if d.status == "ready"]
        required_down = any(d.required and d.status == "failed" for d in self.datasources)
        return bool(up) and not required_down and (keys_optional or not self.llm) and self.index in ("ok", "stale")

    def problems(self) -> List[str]:
        """What is wrong, one line each, safe to show an administrator: each failed datasource and its stage, each
        agent whose key does not resolve, and the index status unless it is ``ok`` or ``stale``. Never a reason's
        text, which can echo a connection string or a key; ``str(report)`` has it for the operator."""
        lines = [f"Datasource '{d.id}'{' (required)' if d.required else ''} failed"
                 f"{f' at {d.stage}' if d.stage else ''}." for d in self.datasources if d.status == "failed"]
        lines += [f"Agent '{agent}' has no API key that resolves." for agent in self.llm]
        if self.index not in ("ok", "stale"):
            lines.append(f"The index is {self.index}.")
        return lines

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
