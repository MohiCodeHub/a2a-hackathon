"""Session management and on-the-wire recording for the env tools API.

A session is keyed by the A2A contextId. Everything the harness needs for
evaluation and transcripts is recorded here: out-of-band env tool calls
(both scopes) and the personal->CS conversation leg captured by the gateway.
"""

import threading
from typing import Literal, Optional

from pydantic import BaseModel, Field
from tau2.data_model.message import ToolCall, ToolMessage
from tau2.data_model.tasks import Task
from tau2.environment.environment import Environment
from tau2.environment.tool import Tool
from tau2.utils.utils import get_now

from a2a_hack.domain import get_hack_environment

Scope = Literal["user", "agent"]

SCOPE_TO_REQUESTOR: dict[Scope, str] = {"user": "user", "agent": "assistant"}


class SessionError(Exception):
    """Base class for session errors, carrying an HTTP-friendly code."""

    status_code = 500


class UnknownSessionError(SessionError):
    status_code = 404


class UnknownToolError(SessionError):
    status_code = 404


class SessionClosedError(SessionError):
    status_code = 409


class RecordedCall(BaseModel):
    """One env tool call executed through the API, with its result."""

    seq: int
    timestamp: str
    scope: Scope
    tool_call: ToolCall
    tool_message: ToolMessage


class RecordedChat(BaseModel):
    """One message on the personal->CS leg, captured by the gateway."""

    seq: int
    timestamp: str
    role: Literal["personal", "cs"]
    content: str
    raw: Optional[dict] = None


class Session(BaseModel):
    """Live state for one simulation, keyed by contextId."""

    model_config = {"arbitrary_types_allowed": True}

    id: str
    task: Task
    env: Environment
    records: list[RecordedCall] = Field(default_factory=list)
    chat_records: list[RecordedChat] = Field(default_factory=list)
    closed: bool = False

    def model_post_init(self, __context) -> None:
        self._lock = threading.Lock()
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def tools(self, scope: Scope) -> list[Tool]:
        """Tools visible to a scope: user scope mirrors tau2's build_user
        (filtered to task.user_tools); agent scope gets all agent tools."""
        if scope == "user":
            return self.env.get_user_tools(include=self.task.user_tools)
        return self.env.get_tools()

    def execute_tool(self, scope: Scope, name: str, arguments: dict) -> ToolMessage:
        """Execute a tool under the session lock, record it, return the result.

        Unknown tools 404 without being recorded so replay only ever sees
        calls the live env actually executed.
        """
        with self._lock:
            if self.closed:
                raise SessionClosedError(f"Session {self.id} is closed")
            if name not in {t.name for t in self.tools(scope)}:
                raise UnknownToolError(f"Unknown tool for {scope} scope: {name}")
            seq = self._next_seq()
            tool_call = ToolCall(
                id=f"oob-{self.id}-{seq}",
                name=name,
                arguments=arguments,
                requestor=SCOPE_TO_REQUESTOR[scope],
            )
            tool_message = self.env.get_response(tool_call)
            self.records.append(
                RecordedCall(
                    seq=seq,
                    timestamp=tool_message.timestamp or get_now(),
                    scope=scope,
                    tool_call=tool_call,
                    tool_message=tool_message,
                )
            )
            return tool_message

    def record_chat(self, role: Literal["personal", "cs"], content: str, raw: Optional[dict] = None) -> None:
        """Record one personal<->CS message (gateway capture). Best-effort:
        recording never blocks tool execution semantics, but shares the lock
        so seq ordering is globally consistent within the session."""
        with self._lock:
            self.chat_records.append(
                RecordedChat(
                    seq=self._next_seq(),
                    timestamp=get_now(),
                    role=role,
                    content=content,
                    raw=raw,
                )
            )


class SessionManager:
    """Creates and owns sessions; holds the static per-job bearer tokens."""

    def __init__(self, user_token: str, agent_token: str, cs_url: Optional[str] = None):
        self.user_token = user_token
        self.agent_token = agent_token
        self.cs_url = cs_url
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def scope_for_token(self, token: str) -> Optional[Scope]:
        """Map a bearer token to its scope; None if invalid."""
        if token == self.user_token:
            return "user"
        if token == self.agent_token:
            return "agent"
        return None

    def create_session(self, session_id: str, task: Task) -> Session:
        """Create a session with a fresh environment for the given task."""
        with self._lock:
            if session_id in self._sessions:
                raise ValueError(f"Session {session_id} already exists")
            session = Session(id=session_id, task=task, env=get_hack_environment())
            self._sessions[session_id] = session
            return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise UnknownSessionError(f"Unknown session: {session_id}")
        return session

    def find(self, session_id: str) -> Optional[Session]:
        """Like get() but returns None for unknown sessions (gateway capture)."""
        with self._lock:
            return self._sessions.get(session_id)

    def close(self, session_id: str) -> Session:
        """Close a session: subsequent tool calls 409. Records stay readable."""
        session = self.get(session_id)
        with session._lock:
            session.closed = True
        return session
