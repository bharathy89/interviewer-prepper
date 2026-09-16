import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 5
CPU_TIME_LIMIT = 5
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
MAX_OUTPUT_CHARS = 4000

DOCKER_IMAGE = "python:3.12-slim"
DOCKER_AVAILABLE = shutil.which("docker") is not None


def _limit_resources():
    # Each limit is applied independently: some platforms (notably macOS) refuse
    # RLIMIT_AS regardless of the value requested, but CPU limiting still works
    # and the subprocess timeout below is the real backstop either way.
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_TIME_LIMIT, CPU_TIME_LIMIT))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except (ValueError, OSError):
        pass


def _truncate(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
    return text


def _run_in_docker(script_path: Path) -> subprocess.CompletedProcess:
    # --network=none is the critical protection here: it blocks all outbound
    # access, including the cloud metadata server (169.254.169.254) that a
    # malicious script could otherwise use to steal the host's credentials.
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "--network=none",
            "--memory=256m", "--memory-swap=256m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--read-only", "--tmpfs", "/tmp",
            "--user", "65534:65534",
            "--security-opt", "no-new-privileges",
            "-v", f"{script_path}:/code/candidate.py:ro",
            DOCKER_IMAGE, "python", "/code/candidate.py",
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS + 5,  # container start/teardown overhead on top of the script's own limit
    )


def _run_bare(script_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        preexec_fn=_limit_resources if sys.platform != "win32" else None,
    )


def run_code(code: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = Path(tmp_dir) / "candidate.py"
        script_path.write_text(code)

        try:
            proc = _run_in_docker(script_path) if DOCKER_AVAILABLE else _run_bare(script_path)
        except subprocess.TimeoutExpired:
            return {
                "timed_out": True,
                "exit_code": None,
                "stdout": "",
                "stderr": f"Execution timed out after {TIMEOUT_SECONDS}s.",
            }

        return {
            "timed_out": False,
            "exit_code": proc.returncode,
            "stdout": _truncate(proc.stdout or ""),
            "stderr": _truncate(proc.stderr or ""),
        }
