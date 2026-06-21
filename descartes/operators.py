"""Doubt operators — shipped as data, one tag per doubt.

Each operator is a distinct lens for interrogating a load-bearing decision.
They are used two ways:
  - as a prompt scaffold so an LLM raises sharp, plan-specific doubts, and
  - as deterministic templates in the no-key fallback path.
"""

OPERATORS = [
    {"id": "assumption",   "prompt": "What is being assumed here that has not been verified?"},
    {"id": "falsify",      "prompt": "What evidence would prove this wrong — and does that evidence exist?"},
    {"id": "inversion",    "prompt": "If the opposite were true, would the plan still survive?"},
    {"id": "named_source", "prompt": "Which authoritative source establishes this as fact, by name?"},
    {"id": "edge_case",    "prompt": "What input, scale, or condition breaks this?"},
    {"id": "quantify",     "prompt": "What is the actual number or threshold — is it known or guessed?"},
    {"id": "root_cause",   "prompt": "Is this addressing the real cause, or only a symptom?"},
    {"id": "reversibility","prompt": "If this is wrong, how costly is it to undo — is it a one-way door?"},
    {"id": "second_order", "prompt": "What does this cause downstream that the plan does not account for?"},
    {"id": "define",       "prompt": "Is every key term defined unambiguously?"},
    {"id": "deletion",     "prompt": "Is this step necessary at all, or can it be removed?"},
]

OPERATOR_IDS = [o["id"] for o in OPERATORS]
OPERATOR_TEXT = "\n".join(f"  - {o['id']}: {o['prompt']}" for o in OPERATORS)
