# Day 08 Lab Report

## 1. Team / student

- Name: Linh Nguyen
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

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	intake(intake)
	classify(classify)
	tool(tool)
	evaluate(evaluate)
	answer(answer)
	clarify(clarify)
	risky_action(risky_action)
	approval(approval)
	retry(retry)
	dead_letter(dead_letter)
	finalize(finalize)
	__end__([<p>__end__</p>]):::last
	__start__ --> intake;
	answer --> finalize;
	approval -.-> clarify;
	approval -.-> tool;
	clarify --> finalize;
	classify -.-> answer;
	classify -.-> clarify;
	classify -.-> retry;
	classify -.-> risky_action;
	classify -.-> tool;
	dead_letter --> finalize;
	evaluate -.-> answer;
	evaluate -.-> retry;
	intake --> classify;
	retry -.-> dead_letter;
	retry -.-> tool;
	risky_action --> approval;
	tool --> evaluate;
	finalize --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc

```

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
- Total Scenarios: 7
- Success Rate: 100.00%
- Avg Nodes Visited: 6.57
- Total Retries: 4
- Total Interrupts: 2

| Scenario | Expected route | Actual route | Success | Retries | Interrupts |
|---|---|---|---:|---:|---:|
| S01_simple | simple | simple | True | 0 | 0 |
| S02_tool | tool | tool | True | 0 | 0 |
| S03_missing | missing_info | missing_info | True | 0 | 0 |
| S04_risky | risky | risky | True | 0 | 1 |
| S05_error | error | error | True | 3 | 0 |
| S06_delete | risky | risky | True | 0 | 1 |
| S07_dead_letter | error | error | True | 1 | 0 |


## 5. Failure analysis

1. **Retry or tool failure**: Transient errors are caught by `tool_node` simulating network issues on initial attempts. `evaluate_node` marks these as `"needs_retry"`, sending the flow to `retry` node where the attempt count increments, looping back to the tool. Max retry checks ensure the loop terminates gracefully, escalating to `dead_letter` after limit is exceeded.
2. **Risky action without approval**: All risky routes must route to `risky_action` then to `approval` node. If `LANGGRAPH_INTERRUPT=true`, an interrupt is raised requiring explicit user action, preventing any side effects from executing without verification.

## 6. Persistence / recovery evidence

We implemented `SqliteSaver` in SQLite WAL mode. Every state update and transition is transactionally committed to the sqlite file database using unique `thread_id` keys, enabling crash recovery and history replay.

## 7. Extension work

- **SQLite Persistence**: Implemented `SqliteSaver` checkpointer in WAL mode to persist graph execution state across restarts.
- **Mermaid Graph Visualization**: Exported Mermaid diagram of graph transitions.

## 8. Improvement plan

In a production system, we would:
1. Replace mock tool execution with actual REST API calls.
2. Implement semantic routing fallback if the classifier experiences rate limits.
3. Build a React/Streamlit interface to intercept and resume interrupted states for HITL approval.
