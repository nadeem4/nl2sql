"""The chat-tool runner (docs/architecture/wave_c.md, section 4).

``build_chat_tools`` resolves the ``chat_tools:`` names through the registry when the engine is built. A
``ChatToolRunner`` calls one tool for a conversation and turns whatever happens into a ``ToolStep``: an output,
or an error with a code the model can read. A tool makes no model call and gets the public ``NL2SQL`` facade
only; the runner is the one place that knows how to call it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional

from nl2sql.chat_tools import ChatToolInfo, ToolContext, ToolNotApplicable
from nl2sql.common.errors import ErrorCode
from nl2sql.common.logger import get_logger
from nl2sql.conversations.models import Conversation, ToolCall, ToolOutput, ToolStep
from nl2sql.policies import UserContext
from nl2sql.registry import Registry, RegistryError

logger = get_logger(__name__)

# What `chat_tools:` means when the section is absent: these, of the ones the registry has. An empty list means
# none. `dashboard_edit` and `dashboard_pin` join when D4 makes them real.
DEFAULT_CHAT_TOOLS = ("chart", "diagram", "report")


def build_chat_tools(names: Optional[List[str]], registry: Registry,
                     problem: Callable[..., None]) -> Dict[str, Any]:
    """The enabled tools by name, in config order. A name that cannot be loaded is reported as a config problem.
    With no list, the default tools the registry has; a name listed explicitly must be registered."""
    if names is None:
        have = set(registry.names("chat_tool"))
        names = [n for n in DEFAULT_CHAT_TOOLS if n in have]
    tools: Dict[str, Any] = {}
    for i, name in enumerate(names):
        if name == "ask":
            problem(f"chat_tools[{i}]", "'ask' is the conversation's own tool and cannot be a registered chat tool.")
            continue
        try:
            tool = registry.load("chat_tool", name)()
            if tool.args_model.model_config.get("extra") != "forbid":
                problem(f"chat_tools[{i}]", f"The args_model of the chat tool '{name}' must forbid extra keys "
                                            "(model_config = ConfigDict(extra=\"forbid\")).")
                continue
            tools[name] = tool
        except RegistryError as error:
            problem(f"chat_tools[{i}]", str(error))
        except Exception as error:  # a plugin's constructor
            problem(f"chat_tools[{i}]", f"The chat tool '{name}' could not be built: {error or type(error).__name__}")
    return tools


def describe(tools: Mapping[str, Any]) -> List[ChatToolInfo]:
    """The tools as the model and ``engine.chat_tools.list()`` show them."""
    return [ChatToolInfo(name=name, description=tool.description, args_schema=tool.args_model.model_json_schema())
            for name, tool in tools.items()]


def _failed(call: ToolCall, code: ErrorCode, message: str) -> ToolStep:
    return ToolStep(call=call, status="error", error={"code": code.value, "message": message})


class ChatToolRunner:
    """Calls the enabled tools for a conversation. ``engine`` is the public facade a tool is handed."""

    def __init__(self, tools: Mapping[str, Any], engine: Any) -> None:
        self._tools = tools
        self._engine = engine

    @property
    def names(self) -> List[str]:
        return list(self._tools)

    def args_models(self) -> Dict[str, type]:
        """Each enabled tool's argument model, which a step is checked against before it runs."""
        return {name: tool.args_model for name, tool in self._tools.items()}

    def specs(self) -> List[Dict[str, Any]]:
        """The tools as the model's ``[CONVERSATION]`` block lists them."""
        return [info.model_dump() for info in describe(self._tools)]

    def run(self, conversation: Conversation, user: UserContext, call: ToolCall) -> ToolStep:
        """Call the tool ``call`` names. Never raises: an unknown tool, a tool that cannot apply, and a tool that
        raises or returns something else are each an ``error`` step with their code."""
        tool = self._tools.get(call.name)
        if tool is None:
            enabled = ", ".join(self._tools) or "none"
            return _failed(call, ErrorCode.UNKNOWN_CHAT_TOOL,
                           f"No chat tool '{call.name}' is enabled. The enabled tools are: {enabled}.")
        try:
            output = tool(ToolContext(conversation=conversation.model_copy(deep=True), user=user,
                                      engine=self._engine), tool.args_model.model_validate(call.args))
        except ToolNotApplicable as error:
            return _failed(call, ErrorCode.TOOL_NOT_APPLICABLE, str(error))
        except Exception as error:
            logger.warning("The chat tool '%s' failed.", call.name, exc_info=True)
            return _failed(call, ErrorCode.TOOL_FAILED,
                           f"The chat tool '{call.name}' failed ({type(error).__name__}).")
        if not isinstance(output, ToolOutput):
            return _failed(call, ErrorCode.TOOL_FAILED,
                           f"The chat tool '{call.name}' returned something that is not a ToolOutput.")
        return ToolStep(call=call, status="ok", output=output)
