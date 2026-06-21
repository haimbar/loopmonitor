#!/usr/bin/env python3
"""
MCP server for loopmonitor — query and control long-running Python/R loops.

Reads loop state from ~/.ipc/ (written by ipc_range / ipc_for / ipc_while /
ipc_repeat) and sends commands via FIFO (Python) or command file (R).

Tools exposed:
  list_loops    — show all registered loopmonitor processes
  peek_loop     — current iteration, ETA, and tracked values
  control_loop  — send continue / break / checkpoint / pause / resume / set
"""

import os
import signal

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from loopmonitor._registry import all_entries, is_alive, clean_stale
from loopmonitor._state import read_state
from loopmonitor._report import format_peek
from loopmonitor.cli import _send, _resolve_targets

app = Server("loopmonitor")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="list_loops",
            description=(
                "List all processes currently registered with loopmonitor. "
                "Shows PID, language (Python/R), label, alive status, and start time. "
                "Stale entries (dead processes) are included so you can see recently "
                "finished loops."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="peek_loop",
            description=(
                "Return the current status of one or more monitored loops: "
                "iteration count, progress percentage, elapsed time, ETA, "
                "and any tracked values (e.g. loss, accuracy). "
                "target accepts a PID, 'all', or a label glob (e.g. 'train-*')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "PID, 'all', or a label glob.",
                    }
                },
                "required": ["target"],
            },
        ),
        types.Tool(
            name="control_loop",
            description=(
                "Send a control command to one or more monitored loops. "
                "target accepts a PID, 'all', or a label glob. "
                "Commands: "
                "'continue' — exit the loop, resume the rest of the script; "
                "'break' — stop the script and save a JSON snapshot; "
                "'checkpoint' — save a snapshot without stopping; "
                "'pause' — suspend the process (SIGSTOP); "
                "'resume' — resume a paused process (SIGCONT); "
                "'set key=value' — inject a value readable by ipc_get() / step.get()."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "PID, 'all', or a label glob.",
                    },
                    "command": {
                        "type": "string",
                        "description": (
                            "One of: continue, break, checkpoint, pause, resume, "
                            "set key=value"
                        ),
                    },
                },
                "required": ["target", "command"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "list_loops":
        result = _list_loops()
    elif name == "peek_loop":
        result = _peek_loop(arguments["target"])
    elif name == "control_loop":
        result = _control_loop(arguments["target"], arguments["command"])
    else:
        result = f"[ERROR] Unknown tool: {name}"
    return [types.TextContent(type="text", text=result)]


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------

def _list_loops() -> str:
    entries = all_entries()
    if not entries:
        return "No processes registered with loopmonitor."
    lines = [f"{'PID':>8}  {'ALIVE':>5}  {'LANG':<6}  {'LABEL':<30}  STARTED"]
    lines.append("─" * 78)
    for e in entries:
        pid   = e["pid"]
        alive = "yes" if is_alive(pid) else "no"
        lang  = e.get("language", "python")
        label = e.get("label", "")[:30]
        start = e.get("start_time", "")[:19].replace("T", " ")
        lines.append(f"{pid:>8}  {alive:>5}  {lang:<6}  {label:<30}  {start}")
    return "\n".join(lines)


def _peek_loop(target: str) -> str:
    targets = _resolve_targets(target)
    if not targets:
        return f"No alive loopmonitor processes matching {target!r}."
    parts = []
    for entry in targets:
        pid = entry["pid"]
        state = read_state(pid)
        if state:
            parts.append(format_peek(state))
        else:
            parts.append(f"[loopmonitor] PID {pid}: no state file yet.")
    return "\n\n".join(parts)


_VALID_COMMANDS = {"continue", "break", "checkpoint", "pause", "resume", "set"}


def _control_loop(target: str, command: str) -> str:
    base_cmd = command.split()[0]
    if base_cmd not in _VALID_COMMANDS:
        return (
            f"[ERROR] Unknown command {command!r}. "
            f"Valid: {', '.join(sorted(_VALID_COMMANDS))}"
        )
    targets = _resolve_targets(target)
    if not targets:
        return f"No alive loopmonitor processes matching {target!r}."

    results = []
    for entry in targets:
        pid   = entry["pid"]
        label = entry.get("label", str(pid))
        name  = f"PID {pid} ({label})"
        if base_cmd == "pause":
            try:
                os.kill(pid, signal.SIGSTOP)
                results.append(f"{name}: paused.")
            except OSError as exc:
                results.append(f"{name}: ERROR — {exc}")
        elif base_cmd == "resume":
            try:
                os.kill(pid, signal.SIGCONT)
                results.append(f"{name}: resumed.")
            except OSError as exc:
                results.append(f"{name}: ERROR — {exc}")
        else:
            ok = _send(pid, command, exit_on_error=False)
            results.append(
                f"{name}: '{command}' sent." if ok
                else f"{name}: ERROR — could not deliver command."
            )
    return "\n".join(results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
