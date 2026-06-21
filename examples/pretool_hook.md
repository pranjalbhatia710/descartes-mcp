# Optional: a PreToolUse hook that nudges you to doubt before you build

Claude Code hooks run *your* command, not an MCP tool, so they can't call
`doubt()` directly. But a tiny `PreToolUse` hook is a useful trip-wire: when
Claude is about to write to a plan file, remind it (and you) to run Descartes
first.

Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (global):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 .claude/hooks/descartes_nudge.py"
          }
        ]
      }
    ]
  }
}
```

`.claude/hooks/descartes_nudge.py`:

```python
#!/usr/bin/env python3
"""PreToolUse nudge: if a *plan/spec* file is about to be written, remind the
agent to run the descartes `doubt` tool on it first. Non-blocking."""
import json
import sys

PLAN_HINTS = ("plan", "spec", "design", "rfc", "proposal")

def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return  # never block the tool on a hook error
    path = (event.get("tool_input", {}) or {}).get("file_path", "").lower()
    if any(h in path for h in PLAN_HINTS):
        # exit code 2 + stderr surfaces the message to the model without blocking
        print("Descartes: consider running the `doubt` tool on this plan before "
              "writing it — pass the relevant files as `context`.", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
```

This is illustrative — tune `PLAN_HINTS`, the matcher, or make it advisory-only
(exit 0) to taste. Descartes works fine with no hook at all.
