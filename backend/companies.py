COMPANY_PROFILES = {
    "Google": {
        "style": (
            "Rigorous and academic. Pushes hard on optimal time/space complexity "
            "and edge cases before accepting a solution."
        ),
    },
    "Amazon": {
        "style": (
            "Ties technical evaluation to Leadership Principles — probes ownership "
            "of the solution and how the candidate would handle ambiguity or scale."
        ),
    },
    "Meta": {
        "style": (
            "Fast-paced, execution-focused. Values getting to a working solution "
            "quickly and iterating, with lighter emphasis on upfront theory."
        ),
    },
    "Microsoft": {
        "style": (
            "Collaborative and conversational. Treats the interview like pairing "
            "with a teammate, values clear explanation as much as the answer."
        ),
    },
    "Apple": {
        "style": (
            "Detail-oriented and exacting. Cares about correctness on subtle edge "
            "cases and precision in the final implementation."
        ),
    },
    "Netflix": {
        "style": (
            "High autonomy, senior-level bar. Expects the candidate to drive the "
            "session with minimal hand-holding and justify decisions independently."
        ),
    },
    "OpenAI": {
        "style": (
            "Research-driven and fast-moving. Expects strong fundamentals plus "
            "comfort with ambiguity — probes how you'd approach a problem with an "
            "incomplete spec and iterate quickly toward something that works."
        ),
    },
    "Anthropic": {
        "style": (
            "Values careful, structured reasoning and clear communication about "
            "tradeoffs and failure modes — expects you to reason explicitly about "
            "edge cases and what could go wrong, not just the happy path."
        ),
    },
    "Google DeepMind": {
        "style": (
            "Research-lab rigor. Expects a strong grasp of algorithmic fundamentals "
            "and pushes on the theoretical underpinnings and edge cases of a solution."
        ),
    },
    "xAI": {
        "style": (
            "Fast-paced and pragmatic, closer to a high-velocity startup — values a "
            "working solution quickly and iterating, with less emphasis on process."
        ),
    },
    "Mistral AI": {
        "style": (
            "Lean, engineering-focused team. Values efficient, clean solutions and "
            "a clear rationale for design choices, often with a systems/performance angle."
        ),
    },
    "NVIDIA": {
        "style": (
            "Deep systems and performance focus. Expects you to reason about "
            "hardware constraints, parallelism, and efficiency, not just correctness."
        ),
    },
    "Databricks": {
        "style": (
            "Distributed-systems minded. Pushes on how a solution behaves at scale "
            "across a cluster, fault tolerance, and data consistency."
        ),
    },
    "Scale AI": {
        "style": (
            "Fast-moving, product-and-infra focused. Values pragmatic solutions "
            "that ship, with attention to data quality and pipeline reliability."
        ),
    },
    "General": {
        "style": (
            "Neutral, standard technical interview. Balanced focus on correctness, "
            "complexity, and communication."
        ),
    },
}


def get_profile(company: str | None) -> dict:
    if not company or company not in COMPANY_PROFILES:
        return COMPANY_PROFILES["General"]
    return COMPANY_PROFILES[company]


def list_companies() -> list[dict]:
    return [
        {"name": name, "style": profile["style"]}
        for name, profile in COMPANY_PROFILES.items()
        if name != "General"
    ]
