"""Shared test helpers: free ports, background uvicorn servers, and minimal
ADK echo agents served over A2A (no LLM needed)."""

import socket
import threading
import time
from typing import AsyncGenerator

import uvicorn
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types as genai_types


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(150):
        if server.started:
            return server
        time.sleep(0.1)
    raise RuntimeError(f"Server on port {port} failed to start")


def text_event(agent: BaseAgent, ctx: InvocationContext, text: str) -> Event:
    return Event(
        invocation_id=ctx.invocation_id,
        author=agent.name,
        content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]),
    )


def incoming_text(ctx: InvocationContext) -> str:
    if ctx.user_content and ctx.user_content.parts:
        return ctx.user_content.parts[0].text or ""
    return ""


class SimpleEchoAgent(BaseAgent):
    """Echoes the incoming text and its ADK session id."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield text_event(self, ctx, f"echo sid={ctx.session.id}: {incoming_text(ctx)}")
