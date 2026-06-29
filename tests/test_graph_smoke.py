"""Graph smoke tests.

These tests verify end-to-end graph execution. They will fail with NotImplementedError
until you implement nodes, routing, and graph wiring.

Note: These tests require a configured LLM (OPENAI_API_KEY or ANTHROPIC_API_KEY)
because classify_node and answer_node use real LLM calls.
"""

import importlib.util
import os

import pytest
from dotenv import load_dotenv

load_dotenv()
os.environ["LANGGRAPH_INTERRUPT"] = "false"

from langgraph_agent_lab.graph import build_graph
from langgraph_agent_lab.persistence import build_checkpointer
from langgraph_agent_lab.state import Route, Scenario, initial_state

pytestmark = [
    pytest.mark.skipif(
        importlib.util.find_spec("langgraph") is None,
        reason="langgraph not installed",
    ),
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY")
        and not os.getenv("OPENAI_API_KEY")
        and not os.getenv("ANTHROPIC_API_KEY"),
        reason="No LLM API key configured (set GEMINI_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY)",
    ),
]


@pytest.mark.parametrize(
    ("query", "expected_route"),
    [
        ("How do I reset my password?", Route.SIMPLE.value),
        ("Please lookup order status for order 123", Route.TOOL.value),
        ("Refund this customer", Route.RISKY.value),
        ("Can you fix it?", Route.MISSING_INFO.value),
        ("Timeout failure while processing", Route.ERROR.value),
    ],
)
def test_graph_runs_and_routes_correctly(query: str, expected_route: str) -> None:
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    scenario = Scenario(id="smoke", query=query, expected_route=Route(expected_route))
    state = initial_state(scenario)
    result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
    assert result["route"] == expected_route
    assert result.get("final_answer") or result.get("pending_question")


def test_graph_terminates_all_routes() -> None:
    """Verify every route reaches finalize node."""
    graph = build_graph(checkpointer=build_checkpointer("memory"))
    queries = [
        ("simple query about help", Route.SIMPLE),
        ("lookup order status 999", Route.TOOL),
        ("fix it", Route.MISSING_INFO),
        ("delete user account now", Route.RISKY),
        ("timeout error in system", Route.ERROR),
    ]
    for query, route in queries:
        scenario = Scenario(id=f"term-{route.value}", query=query, expected_route=route)
        state = initial_state(scenario)
        result = graph.invoke(state, config={"configurable": {"thread_id": state["thread_id"]}})
        events = result.get("events", [])
        finalize_events = [e for e in events if e.get("node") == "finalize"]
        assert finalize_events, f"Route {route.value} did not reach finalize node"


def test_sqlite_persistence_and_resume_approval(tmp_path) -> None:
    """Verify that SQLite checkpointer correctly persists state across interrupts and can be resumed."""
    db_file = tmp_path / "test_checkpoints.db"
    checkpointer = build_checkpointer("sqlite", database_url=str(db_file))
    graph = build_graph(checkpointer=checkpointer)

    # Configure graph interrupt to true
    os.environ["LANGGRAPH_INTERRUPT"] = "true"

    try:
        scenario = Scenario(
            id="test-risky-persistence",
            query="Refund this customer $100",
            expected_route=Route.RISKY
        )
        state = initial_state(scenario)
        thread_id = state["thread_id"]
        config = {"configurable": {"thread_id": thread_id}}

        # Invoke the graph. It should run up to the approval node and pause.
        final_state = graph.invoke(state, config=config)

        # Inspect state. Since we hit the interrupt, final_state should have the state at the interrupt point.
        assert final_state["proposed_action"] == "Execute action: Refund this customer $100"
        assert final_state["approval"] is None

        # Verify the next node to execute is "approval"
        state_history = graph.get_state(config)
        assert state_history.next == ("approval",)

        # Now, resume the graph execution by sending Command(resume=...)
        from langgraph.types import Command
        resume_command = Command(resume={"approved": True, "reviewer": "test-admin", "comment": "approved by unit test"})

        resumed_state = graph.invoke(resume_command, config=config)

        # Verify the graph completed successfully after resuming
        assert resumed_state["route"] == "risky"
        assert resumed_state["approval"]["approved"] is True
        assert resumed_state["approval"]["reviewer"] == "test-admin"
        assert resumed_state["approval"]["comment"] == "approved by unit test"

        # Verify it went to finalize and END
        events = resumed_state.get("events", [])
        finalize_events = [e for e in events if e.get("node") == "finalize"]
        assert finalize_events

    finally:
        os.environ["LANGGRAPH_INTERRUPT"] = "false"

