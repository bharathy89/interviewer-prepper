SENIORITY_LEVELS = ["Fresh Grad", "Junior", "Senior", "Staff", "Principal"]

DEFAULT_SENIORITY = "Senior"

# What "a good answer" looks like at each level, and how much the interviewer
# should lead vs. wait — threaded into the system prompt so hints, pacing,
# and what counts as "stuck" all calibrate to who's actually interviewing.
SENIORITY_BARS = {
    "Fresh Grad": (
        "No professional experience expected. A good answer shows a basic grasp of the "
        "client-server model and a sane data model — don't expect them to raise scaling or "
        "failure-mode topics unprompted. Give hints early and generously; walk them toward a "
        "working single-machine design rather than waiting them out."
    ),
    "Junior": (
        "0-2 years of experience. Should independently propose a basic working architecture "
        "(API + database) and recognize an obvious bottleneck once you point at it. Nudge them "
        "toward scaling/caching topics if they haven't raised one within a few exchanges, rather "
        "than waiting the whole session."
    ),
    "Senior": (
        "3-7 years of experience. Should independently drive the design: propose an "
        "architecture, spot bottlenecks unprompted, and discuss at least one scaling tradeoff "
        "and one reliability tradeoff without being led there. Keep hints minimal, and reserve "
        "them for genuine stuck points."
    ),
    "Staff": (
        "Expected to reason about system evolution, org/team boundaries, and second-order "
        "consequences (cost, operability, migration path) without being prompted. Push back on "
        "hand-wavy tradeoffs — expect rough quantification (e.g. back-of-envelope numbers) "
        "offered unprompted, not just when asked."
    ),
    "Principal": (
        "Expected to set technical direction: weigh build-vs-buy and long-term technical debt "
        "alongside pure technical tradeoffs, and proactively surface risks you didn't ask about. "
        "Hints should be very sparing — silence on an important topic is a real gap worth "
        "probing, not something to walk them through."
    ),
}


def get_bar(level: str | None) -> str:
    return SENIORITY_BARS.get(level, SENIORITY_BARS[DEFAULT_SENIORITY])
