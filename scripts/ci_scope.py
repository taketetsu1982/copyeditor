"""Select a narrow PR check only for explicitly supported documentation changes."""
import os
from pathlib import Path
import re
import subprocess


def select_scope(event, base, head):
    if event != "pull_request" or not re.fullmatch(r"[0-9a-f]{40}", base):
        return "full"
    try:
        # No rename detection: both old and new paths must be safe to omit runtime tests.
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, head],
            capture_output=True, check=True, timeout=30)
        paths = result.stdout.decode("utf-8").split("\0")[:-1]
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return "full"
    return "docs" if paths and set(paths) <= {"README.md"} else "full"


if __name__ == "__main__":
    scope = select_scope(os.environ.get("GITHUB_EVENT_NAME", ""),
                         os.environ.get("PR_BASE_SHA", ""), "HEAD")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"scope={scope}\n")
    print(f"CI scope: {scope}")
