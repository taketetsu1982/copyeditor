"""Check owner evidence using read-only GitHub CLI requests."""
import argparse
from datetime import datetime
import json
import re
import subprocess

SECTIONS = ("Vocabulary", "Syntax", "Structure", "Translation artifacts", "Context weights")
SHA = r"[0-9a-f]{40}"


def gh_json(endpoint, paginate=False):
    command = ["gh", "api", endpoint, "--method", "GET"]
    if paginate:
        command += ["--paginate", "--slurp"]
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    return json.loads(result.stdout)


def records(endpoint):
    pages = gh_json(endpoint + "?per_page=100", paginate=True)
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError("Invalid pagination")
    return [record for page in pages for record in page]


def approved(record, head, review):
    body = record["body"].replace("\r\n", "\n")
    heads = re.findall(r"^Head: (.*)$", body, re.M)
    if heads != [head] or (review and record.get("commit_id") not in (None, head)):
        return False
    if review and record["state"] != "COMMENTED":
        return False
    return all(re.findall(r"^" + re.escape(section) + r": (.*)$", body, re.M) == ["approved"]
               for section in SECTIONS)


def check_native(pr, owner):
    root = "repos/{owner}/{repo}"
    endpoint = f"{root}/pulls/{pr}"
    pull = gh_json(endpoint)
    head = pull["head"]["sha"]
    if not re.fullmatch(SHA, head) or not pull["user"]["login"]:
        raise ValueError("Invalid pull request")
    comments = records(f"{root}/issues/{pr}/comments")
    reviews = records(endpoint + "/reviews")
    evidence = []
    for review, entries in ((False, comments), (True, reviews)):
        for record in entries:
            if record["user"]["login"].casefold() != owner.casefold():
                continue
            if review and record["state"] == "PENDING":
                continue
            if not re.search(r"^Head: " + head + r"\r?$", record["body"], re.M):
                continue
            timestamp = record["submitted_at" if review else "updated_at"]
            when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if when.utcoffset() is None:
                raise ValueError("Missing timezone")
            evidence.append((when, approved(record, head, review)))
    if gh_json(endpoint)["head"]["sha"] != head:
        return "Pull request head changed; retry."
    if not evidence:
        return "No owner record for the current head."
    latest = max(when for when, _ in evidence)
    # Equal timestamps cannot establish that approval superseded a refusal.
    if not all(ok for when, ok in evidence if when == latest):
        return "Latest owner evidence is incomplete or not approved."
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["native"])
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    if args.pr < 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner):
        parser.error("Expected a positive PR number and a GitHub login.")
    try:
        reason = check_native(args.pr, args.owner)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        # gh stderr and malformed record contents may contain personal data.
        reason = "GitHub evidence could not be read or validated."
    print("NATIVE FAIL: " + reason if reason else "NATIVE PASS")
    return 1 if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
