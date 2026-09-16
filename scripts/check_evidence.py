"""Check owner evidence using read-only GitHub CLI requests."""
import argparse
import base64
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


ACK_EN = "Acknowledgements: natural-japanese (coji/natural-japanese) informed this project's approach."
ACK_JA = "謝辞: natural-japanese（coji/natural-japanese）を本プロジェクトの方針の参考にしました。"


def unique_fields(pairs):
    if len(dict(pairs)) != len(pairs):
        raise ValueError("Duplicate evidence field")
    return dict(pairs)


def check_provenance(pr, owner):
    root = "repos/{owner}/{repo}"
    endpoint = f"{root}/pulls/{pr}"
    pull = gh_json(endpoint)
    head = pull["head"]["sha"]
    if not re.fullmatch(SHA, head) or not pull["user"]["login"]:
        raise ValueError("Invalid pull request")
    commit = gh_json(f"{root}/git/commits/{head}")
    tree = gh_json(f"{root}/git/trees/{commit['tree']['sha']}?recursive=1")
    if tree.get("truncated") is not False:
        raise ValueError("Incomplete tree")
    blobs, readme = {}, None
    for entry in tree["tree"]:
        path = entry["path"]
        if path == "README.md" or path.startswith(("rules/", "examples/")):
            if entry["type"] == "tree":
                continue
            if entry["type"] != "blob" or entry["mode"] not in ("100644", "100755") or not re.fullmatch(SHA, entry["sha"]):
                raise ValueError("Invalid public asset")
            if path == "README.md":
                readme = entry["sha"]
            else:
                blobs[path] = entry["sha"]
    if not readme or not all(any(p.startswith(prefix) for p in blobs) for prefix in ("rules/", "examples/")):
        return "Public assets or README are missing."
    encoded = gh_json(f"{root}/git/blobs/{readme}")
    if encoded["encoding"] != "base64":
        raise ValueError("Invalid README encoding")
    text = base64.b64decode(encoded["content"]).decode("utf-8")
    if ACK_EN not in text or ACK_JA not in text:
        return "English and Japanese acknowledgements are required."
    evidence = []
    for record in records(f"{root}/issues/{pr}/comments"):
        if record["user"]["login"].casefold() != owner.casefold():
            continue
        blocks = re.findall(r"```copyeditor-provenance-v1\n(.*?)\n```", record["body"].replace("\r\n", "\n"), re.S)
        for block in blocks:
            data = json.loads(block, object_pairs_hook=unique_fields)
            if data.get("head") != head:
                continue
            when = datetime.fromisoformat(record["updated_at"].replace("Z", "+00:00"))
            if when.utcoffset() is None:
                raise ValueError("Missing timezone")
            valid = (len(blocks) == 1 and set(data) == {"head", "comparison_revision", "non_reuse", "blobs"}
                     and data["non_reuse"] == "confirmed" and data["blobs"] == blobs
                     and isinstance(data["comparison_revision"], str) and re.fullmatch(SHA, data["comparison_revision"]))
            evidence.append((when, bool(valid), data.get("comparison_revision")))
    if not evidence:
        return "No owner provenance record for the current head."
    latest = max(item[0] for item in evidence)
    selected = [item for item in evidence if item[0] == latest]
    if not all(item[1] for item in selected) or len({item[2] for item in selected}) != 1:
        return "Latest owner provenance is incomplete or not confirmed."
    revision = selected[0][2]
    if gh_json(f"repos/coji/natural-japanese/git/commits/{revision}")["sha"] != revision:
        return "Comparison revision is unavailable."
    if gh_json(endpoint)["head"]["sha"] != head:
        return "Pull request head changed; retry."
    return None



def publication_assets(commit):
    root = "repos/{owner}/{repo}"
    value = gh_json(f"{root}/git/commits/{commit}")
    if value["sha"] != commit:
        raise ValueError("Unexpected commit")
    tree_sha = value["tree"]["sha"]
    if not re.fullmatch(SHA, tree_sha):
        raise ValueError("Invalid tree")
    tree = gh_json(f"{root}/git/trees/{tree_sha}?recursive=1")
    if tree.get("truncated") is not False:
        raise ValueError("Incomplete tree")
    assets = {}
    for entry in tree["tree"]:
        path = entry["path"]
        selected = path.startswith(("rules/", "examples/"))
        if not selected or entry["type"] == "tree":
            continue
        if (entry["type"] != "blob" or entry["mode"] not in ("100644", "100755")
                or not re.fullmatch(SHA, entry["sha"]) or path in assets):
            raise ValueError("Invalid public asset")
        assets[path] = entry["sha"]
    if "rules/ja.md" not in assets or not any(p.startswith("examples/ja/") for p in assets):
        raise ValueError("Missing Japanese assets")
    return assets


def check_publication(native_pr, provenance_pr, owner, commit):
    endpoints = [f"repos/{{owner}}/{{repo}}/pulls/{pr}" for pr in (native_pr, provenance_pr)]
    heads = [gh_json(endpoint)["head"]["sha"] for endpoint in endpoints]
    if not all(re.fullmatch(SHA, head) for head in heads):
        raise ValueError("Invalid head")
    if check_native(native_pr, owner) or check_provenance(provenance_pr, owner):
        return "Owner approval is missing or invalid."
    target = publication_assets(commit)
    for native, head in ((True, heads[0]), (False, heads[1])):
        approved_assets = publication_assets(head)
        def scope(assets):
            return {p: h for p, h in assets.items()
                    if not native or p == "rules/ja.md" or p.startswith("examples/ja/")}
        if scope(approved_assets) != scope(target):
            return "Publication assets differ from approved assets."
    if [gh_json(endpoint)["head"]["sha"] for endpoint in endpoints] != heads:
        return "Pull request head changed; retry."
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["native", "provenance", "publish-gate"])
    parser.add_argument("--pr", type=int)
    parser.add_argument("--native-pr", type=int)
    parser.add_argument("--provenance-pr", type=int)
    parser.add_argument("--commit")
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    publication = args.command == "publish-gate"
    prs = [args.native_pr, args.provenance_pr] if publication else [args.pr]
    if publication and (args.pr is not None or not re.fullmatch(SHA, args.commit or "")):
        parser.error("Expected publication PRs and a full commit SHA.")
    if not publication and any(v is not None for v in (args.native_pr, args.provenance_pr, args.commit)):
        parser.error("Publication arguments require publish-gate.")
    if any(pr is None or pr < 1 for pr in prs) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner):
        parser.error("Expected a positive PR number and a GitHub login.")
    try:
        reason = (check_publication(args.native_pr, args.provenance_pr, args.owner, args.commit) if publication
                  else (check_native if args.command == "native" else check_provenance)(args.pr, args.owner))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        # gh stderr and malformed record contents may contain personal data.
        reason = "GitHub evidence could not be read or validated."
    label = args.command.upper()
    print(label + " FAIL: " + reason if reason else label + " PASS")
    return 1 if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
