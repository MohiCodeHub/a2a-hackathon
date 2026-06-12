"""Merge the orchestrator conversation with out-of-band recorded tool calls
into a single tau2 trajectory suitable for Environment.set_state replay.

Each recorded call becomes an atomic [carrier message with the tool_call,
ToolMessage] pair; pairs are interleaved with conversation messages by
timestamp (same host clock) and never split. Timestamps are then rewritten
strictly monotonic and turn_idx renumbered, which is what the evaluator and
results viewers expect."""

from datetime import datetime, timedelta

from tau2.data_model.message import AssistantMessage, Message, UserMessage

from a2a_hack.env_api.sessions import RecordedCall


def _carrier_message(record: RecordedCall) -> Message:
    """Build the message that carries a recorded tool call (requestor by scope)."""
    cls = UserMessage if record.scope == "user" else AssistantMessage
    return cls(
        role=record.tool_call.requestor,
        content=None,
        tool_calls=[record.tool_call],
        timestamp=record.timestamp,
    )


def merge_trajectory(
    conversation: list[Message], records: list[RecordedCall]
) -> list[Message]:
    """Merge conversation messages and recorded env tool calls into one trajectory.

    Args:
        conversation: The orchestrator trajectory (text-only messages between
            the user sim and the personal agent via the bridge).
        records: The session's recorded tool calls, in execution order.

    Returns:
        A chronologically ordered trajectory with strictly monotonic
        timestamps and renumbered turn_idx.
    """
    pairs = [
        [_carrier_message(record), record.tool_message] for record in records
    ]

    conv = sorted(conversation, key=lambda m: m.timestamp)
    merged: list[Message] = []
    ci, pi = 0, 0
    while ci < len(conv) and pi < len(pairs):
        if conv[ci].timestamp <= pairs[pi][0].timestamp:
            merged.append(conv[ci])
            ci += 1
        else:
            merged.extend(pairs[pi])
            pi += 1
    merged.extend(conv[ci:])
    for pair in pairs[pi:]:
        merged.extend(pair)

    # Rewrite timestamps strictly monotonic; keeps downstream sorts stable.
    base = datetime.now() - timedelta(seconds=len(merged))
    trajectory = []
    for i, msg in enumerate(merged):
        msg = msg.model_copy(deep=True)
        msg.timestamp = (base + timedelta(seconds=i)).isoformat()
        msg.turn_idx = i
        trajectory.append(msg)
    return trajectory
