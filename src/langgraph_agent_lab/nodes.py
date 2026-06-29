"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

import os

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from .llm import get_llm
from .state import AgentState, Route, make_event


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── TODO(student): implement ALL nodes below ────────────────────────


class Classification(BaseModel):
    route: Route = Field(
        description="The classified intent/route for the user query. Must be one of: simple, tool, missing_info, risky, error."
    )
    risk_level: str = Field(
        description="Risk level of the query. Must be 'high' for risky routes, and 'low' for all other routes."
    )


CLASSIFY_PROMPT = """You are an expert customer support ticket router.
Analyze the user's support ticket query and classify it into one of the following routes:
1. 'risky': Destructive operations, updates, transactions, or refunds (e.g., refund customer, delete customer account, send confirmation email, cancel subscription).
2. 'tool': Read-only lookup of order status, shipment status, tracking info, or database inquiries.
3. 'missing_info': Very vague, incomplete, or ambiguous requests that lack detail to take any action (e.g., "Can you fix it?", "Fix it", "Do it").
4. 'error': Explicit reports or statements indicating system failure, timeouts, server issues, or processing faults (e.g., "Timeout failure while processing request", "System failure").
5. 'simple': General questions, reset passwords, general FAQs that do not require tool usage or side effects (e.g., "How do I reset my password?").

Priorities: risky > tool > missing_info > error > simple. If a query could belong to multiple categories, choose the highest priority one."""


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM.

    *** MUST use a real LLM call — keyword-only heuristics will lose points. ***

    Use .with_structured_output() or equivalent to get reliable enum classification.
    The LLM should classify into one of: simple, tool, missing_info, risky, error.
    """
    query = state.get("query", "").strip()
    llm = get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(Classification).with_retry(stop_after_attempt=6)

    res = structured_llm.invoke(
        [{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": query}]
    )

    route = res.route.value if hasattr(res.route, "value") else str(res.route)
    risk_level = "high" if route == "risky" else "low"

    return {
        "route": route,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"Route: {route}, Risk: {risk_level}")],
    }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call.

    Simulate transient failures for error-route scenarios to test retry loops.
    """
    route = state.get("route", "")
    attempt = state.get("attempt", 0)
    query = state.get("query", "")

    if route == "error" and attempt < 2:
        result = "ERROR: Timeout failure while processing request"
    else:
        result = f"Successfully completed action for: {query}"

    return {
        "tool_results": [result],
        "events": [make_event("tool", "completed", f"Result: {result}")],
    }


class EvaluationOutput(BaseModel):
    satisfactory: bool = Field(
        description="True if the tool results resolved the query successfully without system errors, False if there was a system error or timeout requiring a retry."
    )
    reason: str = Field(description="Explanation of the evaluation decision.")


EVALUATE_PROMPT = """You are a quality assurance evaluator.
Check if the tool results resolved the query successfully or if there was a system failure, timeout, or error that requires a retry.
Respond with satisfactory=False if there are errors or failures, otherwise satisfactory=True."""


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the retry-loop gate.

    Check whether the latest tool result is satisfactory or needs retry.

    SHOULD use LLM-as-judge for bonus points. Heuristic (e.g., check for "ERROR" substring)
    is acceptable for base score.
    """
    tool_results = state.get("tool_results", [])
    latest_result = tool_results[-1] if tool_results else ""
    query = state.get("query", "")

    evaluation_result = "success"
    try:
        llm = get_llm(temperature=0.0)
        structured_llm = llm.with_structured_output(EvaluationOutput).with_retry(
            stop_after_attempt=6
        )
        res = structured_llm.invoke(
            [
                {"role": "system", "content": EVALUATE_PROMPT},
                {"role": "user", "content": f"Query: {query}\nLatest Tool Result: {latest_result}"},
            ]
        )
        if not res.satisfactory:
            evaluation_result = "needs_retry"
    except Exception:
        # Heuristic fallback if LLM call fails
        if "ERROR" in latest_result:
            evaluation_result = "needs_retry"

    return {
        "evaluation_result": evaluation_result,
        "events": [make_event("evaluate", "completed", f"Result: {evaluation_result}")],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM.

    *** MUST use a real LLM call — hardcoded strings will lose points. ***

    The LLM should generate a helpful response grounded in available context:
    - tool_results (if any)
    - approval decision (if risky route)
    - original query
    """
    query = state.get("query", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval")

    context = f"User Query: {query}\n"
    if tool_results:
        context += f"Tool Results: {tool_results}\n"
    if approval:
        context += f"Human Approval Decision: {approval}\n"

    prompt = f"""You are a helpful customer support agent. Generate a final response to the user's query.
You must ground your response in the provided context. If a tool was run, explain the result clearly.
If approval was involved, you may reference it.

Context:
{context}"""

    llm = get_llm(temperature=0.7).with_retry(stop_after_attempt=6)
    response = llm.invoke(prompt)
    final_answer = response.content

    return {
        "final_answer": final_answer,
        "events": [make_event("answer", "completed", "Answer generated")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Generate a specific clarification question based on the vague/incomplete query.
    """
    query = state.get("query", "")
    prompt = f"""The user query is vague or incomplete. Generate a polite clarification question asking for details.
Query: {query}"""

    llm = get_llm(temperature=0.7).with_retry(stop_after_attempt=6)
    response = llm.invoke(prompt)
    question = response.content

    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "Asked for clarification")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval.

    Describe the proposed action and why it requires approval.
    """
    query = state.get("query", "")
    proposed_action = f"Execute action: {query}"

    return {
        "proposed_action": proposed_action,
        "events": [
            make_event("risky_action", "completed", f"Prepared description: {proposed_action}")
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step.

    Default behavior: mock approval (approved=True) so tests and CI run offline.
    Extension: if env LANGGRAPH_INTERRUPT=true, use langgraph.types.interrupt() for real HITL.
    """
    if os.getenv("LANGGRAPH_INTERRUPT") == "true":
        decision = interrupt(
            {
                "message": f"Approval required for action: {state.get('proposed_action')}",
                "proposed_action": state.get("proposed_action"),
                "query": state.get("query"),
            }
        )
        if isinstance(decision, dict):
            approved = decision.get("approved", False)
            reviewer = decision.get("reviewer", "human-reviewer")
            comment = decision.get("comment", "")
        else:
            approved = getattr(decision, "approved", False)
            reviewer = getattr(decision, "reviewer", "human-reviewer")
            comment = getattr(decision, "comment", "")
    else:
        # Default behavior: automatically approve
        approved = True
        reviewer = "mock-reviewer"
        comment = "Automatically approved in non-interactive mode"

    approval_decision = {"approved": approved, "reviewer": reviewer, "comment": comment}

    return {
        "approval": approval_decision,
        "events": [make_event("approval", "completed", f"Approved: {approved}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt.

    Increment the attempt counter and log the transient failure.
    """
    attempt = state.get("attempt", 0)
    new_attempt = attempt + 1
    error_msg = f"Attempt {new_attempt} failed."

    return {
        "attempt": new_attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "completed", f"Attempt incremented to {new_attempt}")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries exceeded.

    This is the third layer: retry → fallback → dead letter.
    Log the failure and set a final_answer explaining that the request could not be completed.
    """
    query = state.get("query", "")
    errors = state.get("errors", [])
    error_log = f"Failed to execute request '{query}' after maximum retries. Errors: {errors}"
    final_answer = (
        "We encountered system errors while processing your request. Please try again later."
    )

    return {
        "final_answer": final_answer,
        "events": [make_event("dead_letter", "completed", error_log)],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END."""
    return {
        "events": [make_event("finalize", "completed", "workflow finished")],
    }
