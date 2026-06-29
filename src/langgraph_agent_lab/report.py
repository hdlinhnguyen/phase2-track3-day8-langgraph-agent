"""Report generation helper.

TODO(student): implement report rendering using MetricsReport data
and the template in reports/lab_report_template.md.
"""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport


def render_report(metrics: MetricsReport) -> str:
    """Render a complete lab report from metrics data.

    Returns a formatted markdown report string.
    """
    scenarios_table = "| Scenario | Expected route | Actual route | Success | Retries | Interrupts |\n|---|---|---|---:|---:|---:|\n"
    for item in metrics.scenario_metrics:
        scenarios_table += f"| {item.scenario_id} | {item.expected_route} | {item.actual_route} | {item.success} | {item.retry_count} | {item.interrupt_count} |\n"

    # Dynamic Mermaid graph generation
    try:
        from .graph import build_graph

        graph = build_graph()
        mermaid_code = graph.get_graph().draw_mermaid()
        graph_section = f"```mermaid\n{mermaid_code}\n```"
    except Exception as e:
        graph_section = f"Error generating graph diagram: {e}"

    report = f"""# Day 08 Lab Report

## 1. Team / student

- Name: Nguyen Ho Dieu Linh
- MSV: 2A202600567
- Repo/commit: phase2-track3-day8-langgraph-agent
- Date: 2026-06-29

## 2. Architecture

We constructed a StateGraph with 11 nodes that handles support ticket classification, tool invocation, result evaluation, human-in-the-loop approval, error retries, and dead letter routing.

- **intake**: Normalizes user input.
- **classify**: Leverages LLM Structured Output (`with_structured_output`) to determine intent.
- **tool**: Executes read/write operations and simulates transient errors.
- **evaluate**: LLM-as-judge assesses quality of tool output.
- **answer**: LLM constructs a helpful response grounded in the context.
- **clarify**: Requests missing details.
- **risky_action**: Prepares action details for approval.
- **approval**: Uses `interrupt()` to request human confirmation if `LANGGRAPH_INTERRUPT=true`.
- **retry**: Increments retry attempt counters.
- **dead_letter**: Logs final failure when max attempts are exceeded.
- **finalize**: Emits the final audit event.

### Graph Visualization

{graph_section}

## 3. State schema

| Field | Reducer | Why |
|---|---|---|
| messages | append | audit conversation/events |
| route | overwrite | current route only |
| evaluation_result | overwrite | retry loop decision |
| pending_question | overwrite | clarification flow query |
| proposed_action | overwrite | risky action description |
| approval | overwrite | HITL approval decision |
| tool_results | append | tool logs |
| errors | append | error messages |
| events | append | audit timeline |

## 4. Scenario results

**Summary Metrics:**
- Total Scenarios: {metrics.total_scenarios}
- Success Rate: {metrics.success_rate:.2%}
- Avg Nodes Visited: {metrics.avg_nodes_visited:.2f}
- Total Retries: {metrics.total_retries}
- Total Interrupts: {metrics.total_interrupts}

{scenarios_table}

## 5. Failure analysis

1. **Retry or tool failure**: Transient errors are caught by `tool_node` simulating network issues on initial attempts. `evaluate_node` marks these as `"needs_retry"`, sending the flow to `retry` node where the attempt count increments, looping back to the tool. Max retry checks ensure the loop terminates gracefully, escalating to `dead_letter` after limit is exceeded.
2. **Risky action without approval**: All risky routes must route to `risky_action` then to `approval` node. If `LANGGRAPH_INTERRUPT=true`, an interrupt is raised requiring explicit user action, preventing any side effects from executing without verification.

## 6. Persistence / recovery evidence

We implemented `SqliteSaver` in SQLite WAL mode. Every state update and transition is transactionally committed to the sqlite file database using unique `thread_id` keys, enabling crash recovery and history replay.

## 7. Extension work

- **SQLite Persistence**: Implemented `SqliteSaver` checkpointer in WAL mode to persist graph execution state across restarts.
- **Mermaid Graph Visualization**: Exported Mermaid diagram of graph transitions.
- **Real Human-In-The-Loop (HITL) Interruption & Resume**: Implemented real `interrupt()` mechanism in `approval_node` when `LANGGRAPH_INTERRUPT=true`, fully validated end-to-end via programmatic resume using `Command(resume=...)`.

## 8. Improvement plan

In a production system, we would:
1. Replace mock tool execution with actual REST API calls.
2. Implement semantic routing fallback if the classifier experiences rate limits.
3. Build a React/Streamlit interface to intercept and resume interrupted states for HITL approval.
"""
    return report


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    """Write the rendered report to a file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
