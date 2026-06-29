"""Checkpointer adapter."""

from __future__ import annotations

from typing import Any


def build_checkpointer(kind: str = "memory", database_url: str | None = None) -> Any | None:  # noqa: ANN401
    """Return a LangGraph checkpointer.

    For SQLite:
    - Use SqliteSaver with sqlite3.connect() and WAL mode
    """
    if kind == "none":
        return None
    if kind == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    if kind == "sqlite":
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        db_path = database_url or "outputs/checkpoints.db"
        # Ensure outputs directory exists
        import os

        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        # SqliteSaver expects an open connection
        return SqliteSaver(conn)
    if kind == "postgres":
        raise NotImplementedError("Postgres checkpointer is not configured for this environment.")
    raise ValueError(f"Unknown checkpointer kind: {kind}")
