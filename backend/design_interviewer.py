import json
import re

from . import ollama_client
from .companies import get_profile
from .interviewer import NO_COMMENT


def _system_prompt(problem: dict, company: str | None) -> str:
    profile = get_profile(company)
    company_label = company if company and company != "General" else "a generalist tech company"

    requirements = "\n".join(f"- {r}" for r in problem["requirements"])
    discussion_points = "\n".join(f"- {d}" for d in problem["discussion_points"])

    return f"""You are conducting a live system design interview, in the style of {company_label}.
Interview style: {profile['style']}

Problem: {problem['title']} ({problem['difficulty']})
{problem['prompt']}

Requirements:
{requirements}

Discussion points you should steer the conversation toward, but only raise one at a time and
only once the candidate has had a chance to develop their current thread of thought:
{discussion_points}

Rules:
- Ask the candidate to state their assumptions and high-level approach before diving into detail.
- Ask probing follow-up questions about scalability, data modeling, and tradeoffs rather than
  handing over an "ideal" architecture. This is a discussion, not a lecture.
- Never just list all the discussion points at once — surface them naturally as the conversation
  progresses, the way a real interviewer would.
- Keep every response short: 1-2 sentences is the target, 3 is the ceiling. No preamble, no
  restating the problem or the candidate's own words back to them, no filler like "great
  question" or "let's dive in" — get straight to the point, like a real interviewer typing in a
  chat window, not writing an essay.
- The candidate is sketching their design on a drawing canvas (boxes, arrows, labels) as you talk.
  You won't see it on every turn, but sometimes a description of it, or an image of it, will be
  included below — react to what's actually there when it is, rather than assuming.
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
        f"{company_note} Take a moment to read over the requirements, and walk me through your "
        f"high-level approach before you start sketching out the design."
    )


def respond(
    problem: dict, company: str | None, history: list[dict], message: str, image_b64: str | None = None
) -> str:
    history = history + [{"role": "user", "content": message}]
    messages = _history_to_messages(problem, company, history)
    if image_b64:
        return ollama_client.chat_with_image(messages, image_b64)
    return ollama_client.chat(messages)


def maybe_intervene(
    problem: dict,
    company: str | None,
    history: list[dict],
    transcript: str | None = None,
    image_b64: str | None = None,
) -> str | None:
    """Decide whether the interviewer should proactively say something right now.

    Returns None if nothing is warranted, otherwise a short spoken-style message.
    Called both when a voice utterance just completed (transcript set) and from the
    canvas-stagnation poll (transcript None, triggered by no diagram changes).
    """
    if transcript is not None:
        trigger_note = f'The candidate just said: "{transcript}"'
    else:
        trigger_note = (
            "The candidate hasn't changed their diagram in a while. Decide if a nudge is warranted."
        )

    diagram_note = (
        "The image below is the most recent snapshot of their diagram (it may be a few seconds "
        "stale). Refer to what's actually in it when relevant."
        if image_b64
        else "You don't have a current view of their diagram for this turn — don't claim to see "
        "specifics of it; ask them to describe or share it if that's genuinely needed."
    )

    prompt = f"""{trigger_note}

{diagram_note}

If the candidate asked a direct question, answer it in 1-2 sentences, no more. If they seem stuck
or have gone quiet, ask ONE short question (1 sentence) to nudge the discussion forward (e.g.
about a requirement they haven't addressed yet, or a component they mentioned but haven't
detailed). If neither applies — they're just thinking, or making fine progress — reply with
exactly the single token {NO_COMMENT} and nothing else.
"""
    history = history + [{"role": "user", "content": prompt}]
    messages = _history_to_messages(problem, company, history)
    reply = (
        ollama_client.chat_with_image(messages, image_b64) if image_b64 else ollama_client.chat(messages)
    ).strip()
    if reply == NO_COMMENT or not reply:
        return None
    return reply


def review_diagram(problem: dict, company: str | None, history: list[dict], image_b64: str) -> str:
    prompt = (
        "Here is the candidate's current diagram. Look at what's actually drawn — the boxes, "
        "labels, and connections — and give feedback in 1-2 sentences, no more: point out one "
        "specific thing you see (e.g. a missing piece, an unclear connection, or a good choice), "
        "or ask a clarifying question about a component in it. No preamble."
    )
    history = history + [{"role": "user", "content": prompt}]
    messages = _history_to_messages(problem, company, history)
    return ollama_client.chat_with_image(messages, image_b64)


def _extract_json_array(text: str) -> list:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


MAX_SUGGESTED_ELEMENTS = 4


def _sanitize_skeleton(elements: list, current_elements: list[dict]) -> list[dict]:
    """Defends against the model not perfectly following the prompt: wraps a plain-string
    label into the {"text": ...} shape Excalidraw's skeleton format actually requires (a
    bare string silently renders no label at all), and drops any arrow whose start/end
    doesn't resolve to a real id rather than letting a dangling arrow render disconnected.
    """
    known_ids = {str(e["id"]) for e in current_elements if e.get("id")}
    cleaned = []
    for el in elements[: MAX_SUGGESTED_ELEMENTS * 2]:
        if not isinstance(el, dict) or "type" not in el:
            continue
        el = dict(el)
        if isinstance(el.get("label"), str):
            el["label"] = {"text": el["label"]}
        if el.get("id"):
            known_ids.add(str(el["id"]))
        cleaned.append(el)

    result = []
    for el in cleaned:
        if el.get("type") == "arrow":
            start_id = (el.get("start") or {}).get("id")
            end_id = (el.get("end") or {}).get("id")
            if not start_id or not end_id or start_id not in known_ids or end_id not in known_ids:
                continue
        result.append(el)
    return result[:MAX_SUGGESTED_ELEMENTS]


def suggest_diagram_update(
    problem: dict,
    company: str | None,
    history: list[dict],
    image_b64: str,
    current_elements: list[dict],
) -> list[dict]:
    """Ask the model for NEW elements to add to the diagram (never a full replacement,
    so a bad suggestion can never delete the candidate's own work) in a simplified
    "skeleton" schema. The caller runs this through Excalidraw's own
    convertToExcalidrawElements() to get real elements — this function only has to
    produce the simplified shape, not Excalidraw's full internal element format.
    """
    prompt = f"""The candidate wants concrete suggestions for their diagram based on the discussion
so far. Here is an image of the current diagram, plus the exact existing elements as JSON so you
can reference their ids precisely (reuse an existing "id" for start/end when you want a new arrow
to attach to something already there — never invent an id that isn't listed):

{json.dumps(current_elements)}

Propose AT MOST 4 new elements total, focused on the single most useful thing to add right now —
not a full redesign in one shot. Never repeat or remove anything that already exists. Reply with
ONLY a JSON array (no prose, no markdown fences) using this exact schema:
[
  {{"type": "rectangle", "id": "<your own short id, e.g. cache1>", "x": <number>, "y": <number>, "width": <number>, "height": <number>, "label": {{"text": "<text>"}}}},
  {{"type": "arrow", "x": <number>, "y": <number>, "start": {{"id": "<id>"}}, "end": {{"id": "<id>"}}}},
  {{"type": "text", "x": <number>, "y": <number>, "text": "<text>"}}
]
Note "label" is an OBJECT with a "text" field, not a plain string — {{"text": "Cache"}}, never
just "Cache".
Give every new rectangle/text you add its own short "id" string, and use those same ids (or an
existing id from the list above) as the start/end of any arrow — including arrows between two
elements you're both adding in this same response. Every arrow MUST have BOTH a start and an end
id referencing a real element; never emit an arrow with only one end, and never emit an arrow
whose start/end id isn't in the existing list or among the ids you just assigned.
Place new elements in empty space that doesn't overlap the existing element bounds given above.
If you have nothing concrete to add right now, reply with exactly: []
"""
    history = history + [{"role": "user", "content": prompt}]
    messages = _history_to_messages(problem, company, history)
    reply = ollama_client.chat_with_image(messages, image_b64)
    return _sanitize_skeleton(_extract_json_array(reply), current_elements)
