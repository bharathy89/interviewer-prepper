from . import ollama_client
from .companies import get_profile

NO_COMMENT = "NO_COMMENT"


def _numbered(code: str) -> str:
    lines = (code or "").splitlines() or [""]
    width = len(str(len(lines)))
    return "\n".join(f"{str(i).rjust(width)}| {line}" for i, line in enumerate(lines, start=1))


def _system_prompt(problem: dict, company: str | None) -> str:
    profile = get_profile(company)
    company_label = company if company and company != "General" else "a generalist tech company"

    constraints = "\n".join(f"- {c}" for c in problem["constraints"])
    examples = "\n".join(
        f"- Input: {e['input']} -> Output: {e['output']}" for e in problem["examples"]
    )
    hints = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(problem["hints"]))

    return f"""You are conducting a live technical coding interview, in the style of {company_label}.
Interview style: {profile['style']}

Problem: {problem['title']} ({problem['difficulty']})
{problem['prompt']}

Constraints:
{constraints}

Examples:
{examples}

Hints available to you, in escalating order (only share the next one when the candidate is
genuinely stuck — never share all at once, never give the full solution or write code for them):
{hints}

Rules:
- Ask the candidate to explain their approach before they start coding, if they haven't already.
- Answer clarifying questions using only the constraints/examples above. Never invent new constraints.
- Give hints progressively and only when asked or clearly stuck. Never write code for the candidate.
- Keep every response short: 1-2 sentences is the target, 3 is the ceiling. No preamble, no
  restating the problem or the candidate's own words back to them, no filler like "great
  question" or "let's dive in" — get straight to the point, like a real interviewer typing in a
  chat window, not writing an essay.
- You will be shown the candidate's current editor contents with each message, prefixed with line
  numbers; react to it naturally (e.g. ask about complexity, point out a bug's symptom without
  naming the fix) but don't grade it here. Whenever you point at a specific part of the code,
  reference the exact line number shown (e.g. "take a look at line 6").
"""


def _history_to_messages(problem: dict, company: str | None, history: list[dict]) -> list[dict]:
    messages = [{"role": "system", "content": _system_prompt(problem, company)}]
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    return messages


def opening_message(problem: dict, company: str | None) -> str:
    profile = get_profile(company)
    company_note = f" We'll run this in the style of {company}: {profile['style']}" if (
        company and company != "General"
    ) else ""
    return (
        f"Let's get started. Here's your problem: **{problem['title']}** ({problem['difficulty']})."
        f"{company_note} Take a moment to read it over, and walk me through your approach before you "
        f"start coding."
    )


def respond(problem: dict, company: str | None, history: list[dict], message: str, code: str) -> str:
    history = history + [
        {
            "role": "user",
            "content": f"{message}\n\n--- Candidate's current code (line-numbered) ---\n{_numbered(code)}",
        }
    ]
    messages = _history_to_messages(problem, company, history)
    return ollama_client.chat(messages)


def maybe_intervene(
    problem: dict,
    company: str | None,
    history: list[dict],
    code: str,
    transcript: str | None = None,
) -> str | None:
    """Decide whether the interviewer should proactively say something right now.

    Returns None if nothing is warranted, otherwise a short spoken-style message.
    Called both when a voice utterance just completed (transcript set) and from the
    stagnation poll (transcript None, triggered by no code progress / repeated failures).
    """
    if transcript is not None:
        trigger_note = f'The candidate just said: "{transcript}"'
    else:
        trigger_note = (
            "The candidate hasn't changed their code in a while. Decide if a nudge is warranted."
        )

    prompt = f"""{trigger_note}

Candidate's current code (line-numbered):
{_numbered(code)}

If the candidate asked a direct question, answer it in 1-2 sentences, no more. If they seem stuck
or are making a mistake, give the next appropriate hint from your escalating hint list in 1-2
sentences, naming the specific line number(s) involved. If neither applies — they're just
thinking out loud, or making fine progress — reply with exactly the single token {NO_COMMENT}
and nothing else.
"""
    history = history + [{"role": "user", "content": prompt}]
    messages = _history_to_messages(problem, company, history)
    reply = ollama_client.chat(messages).strip()
    if reply == NO_COMMENT or not reply:
        return None
    return reply
