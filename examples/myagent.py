"""Toy agent for the agenteval smoke test. No LLM, fully deterministic."""


def agent(user_input: str) -> dict:
    text = user_input.lower()
    if "refund" in text:
        return {
            "output": "Sure — I've started your refund.",
            "tool_calls": ["lookup_order"],
            "steps": 2,
        }
    return {
        "output": "How can I help?",
        "tool_calls": [],
        "steps": 1,
    }
