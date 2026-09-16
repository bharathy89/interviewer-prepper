import ollama

MODEL = "glm-5.3-flash:cloud"


class InterviewerUnavailable(Exception):
    pass


def chat(messages: list[dict]) -> str:
    try:
        response = ollama.chat(model=MODEL, messages=messages)
    except Exception as exc:
        raise InterviewerUnavailable(
            f"Couldn't reach the interviewer model ({MODEL}) via Ollama: {exc}"
        ) from exc
    return response["message"]["content"]


def chat_with_image(messages: list[dict], image_b64: str) -> str:
    messages = messages[:-1] + [{**messages[-1], "images": [image_b64]}]
    try:
        response = ollama.chat(model=MODEL, messages=messages)
    except Exception as exc:
        raise InterviewerUnavailable(
            f"Couldn't reach the interviewer model ({MODEL}) via Ollama: {exc}"
        ) from exc
    return response["message"]["content"]
