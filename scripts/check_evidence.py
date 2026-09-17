"""Check owner evidence using read-only GitHub CLI requests."""
import argparse
import base64
from datetime import datetime
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import zipfile

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


def check_provenance(pr, owner, published_at=None):
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
    if published_at is not None and latest > published_at:
        return "Provenance approval must precede publication."
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


def check_publication(native_pr, owner, commit):
    endpoint = f"repos/{{owner}}/{{repo}}/pulls/{native_pr}"
    head = gh_json(endpoint)["head"]["sha"]
    if not re.fullmatch(SHA, head):
        raise ValueError("Invalid head")
    if check_native(native_pr, owner):
        return "Owner approval is missing or invalid."
    def scope(assets):
        return {p: h for p, h in assets.items() if p == "rules/ja.md" or p.startswith("examples/ja/")}
    if scope(publication_assets(head)) != scope(publication_assets(commit)):
        return "Publication assets differ from approved assets."
    if gh_json(endpoint)["head"]["sha"] != head:
        return "Pull request head changed; retry."
    return None


def release_repository(repository):
    if (not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository)
            or gh_json("repos/{owner}/{repo}")["full_name"] != repository):
        raise ValueError("Invalid release repository")


def parse_release_run_url(repository, run_url):
    try:
        release_repository(repository)
        match = re.fullmatch(r"https://github\.com/" + re.escape(repository)
                             + r"/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)", run_url)
        if not match:
            raise ValueError("Invalid run URL")
        return tuple(map(int, match.groups()))
    except Exception:
        raise ValueError("GitHub evidence could not be read or validated.") from None


def load_release_evidence(repository, run_id, run_attempt, tag):
    def require(condition):
        if not condition:
            raise ValueError("Invalid release evidence")
    try:
        release_repository(repository)
        require(all(type(v) is int and v > 0 for v in (run_id, run_attempt)))
        require(isinstance(tag, str) and re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag))
        require(tuple(map(int, tag[1:].split('.'))) >= (0, 1, 0))
        root = f"repos/{repository}"
        run_root = f"{root}/actions/runs/{run_id}"
        attempt_root = f"{run_root}/attempts/{run_attempt}"
        run = gh_json(attempt_root)
        expected = dict(id=run_id, run_attempt=run_attempt, status="completed", conclusion="success",
                        event="push", name="Publish image", head_branch=tag)
        require(all(type(run[k]) is type(v) and run[k] == v for k, v in expected.items()))
        require(run["repository"]["full_name"] == repository and re.fullmatch(SHA, run["head_sha"]))
        workflow_id = run["workflow_id"]
        require(type(workflow_id) is int and workflow_id > 0)
        workflow = gh_json(f"{root}/actions/workflows/{workflow_id}")
        require(workflow["path"] == ".github/workflows/publish.yml" and workflow["name"] == "Publish image")
        def listing(endpoint, key):
            pages = gh_json(endpoint + "?per_page=100", paginate=True)
            require(type(pages) is list and pages and all(type(p[key]) is list for p in pages))
            return [item for page in pages for item in page[key]]
        artifacts = [a for a in listing(run_root + "/artifacts", "artifacts") if a["name"] == "release-evidence-" + tag]
        require(len(artifacts) == 1 and artifacts[0]["expired"] is False)
        artifact_id = artifacts[0]["id"]
        require(type(artifact_id) is int and artifact_id > 0)
        archive = subprocess.run(["gh", "api", f"{root}/actions/artifacts/{artifact_id}/zip", "--method", "GET"],
                                 capture_output=True, check=True, timeout=60).stdout
        # Never extract an archive path, including a disguised symlink, onto the filesystem.
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            entries = zipped.infolist()
            require(len(entries) == 1 and entries[0].filename == "release-evidence.json")
            require(not entries[0].is_dir() and stat.S_IFMT(entries[0].external_attr >> 16) in (0, stat.S_IFREG))
            data = json.loads(zipped.read(entries[0]).decode("utf-8"), object_pairs_hook=unique_fields)
        fields = {"schema", "repository", "run_id", "run_attempt", "workflow", "commit", "tag", "image", "digest", "published_at"}
        require(type(data) is dict and set(data) == fields)
        require(all(type(data[k]) is str for k in fields - {"run_id", "run_attempt"}))
        require(all(type(data[k]) is int and data[k] > 0 for k in ("run_id", "run_attempt")))
        expected = dict(schema="copyeditor-release-evidence-v1", repository=repository, run_id=run_id,
                        run_attempt=run_attempt, workflow="Publish image", commit=run["head_sha"], tag=tag,
                        image=f"ghcr.io/{repository.split('/')[0].lower()}/copyeditor:{tag}")
        require(all(data[k] == v for k, v in expected.items()))
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", data["digest"]))
        require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", data["published_at"]))
        target = gh_json(f"{root}/git/ref/tags/{tag}")["object"]
        visited = set()
        while target["type"] == "tag":
            sha = target["sha"]
            require(re.fullmatch(SHA, sha) and sha not in visited)
            visited.add(sha)
            target = gh_json(f"{root}/git/tags/{sha}")["object"]
        require(target["type"] == "commit" and target["sha"] == data["commit"])
        jobs = [j for j in listing(attempt_root + "/jobs", "jobs") if j["name"] == "publish"]
        require(len(jobs) == 1 and jobs[0]["conclusion"] == "success")
        def instant(value):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(result.utcoffset() is not None)
            return result
        require(instant(jobs[0]["started_at"]) <= instant(data["published_at"]) <= instant(jobs[0]["completed_at"]))
        return data
    except Exception:
        raise ValueError("GitHub evidence could not be read or validated.") from None


def check_release(path, owner):
    def require(condition):
        if not condition:
            raise ValueError("Invalid acceptance evidence")
    data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
    fields = {"schema", "owner", "recorded_at", "repository", "run_url", "run_attempt", "commit", "tag", "digest",
              "native_pr_url", "checks"}
    require(type(data) is dict and set(data) == fields)
    require(all(type(data[k]) is str for k in fields - {"run_attempt", "checks"}))
    require(data["schema"] == "copyeditor-acceptance-evidence-v1" and data["owner"] == owner)
    require(type(data["run_attempt"]) is int and data["run_attempt"] > 0)
    checks = {"google_oauth", "allowed_domain", "allowed_email", "anonymous_401", "none_derived_polish", "google_derived_polish"}
    require(type(data["checks"]) is dict and set(data["checks"]) == checks and all(v is True for v in data["checks"].values()))
    run_id, attempt = parse_release_run_url(data["repository"], data["run_url"])
    require(attempt == data["run_attempt"])
    match = re.fullmatch(r"https://github\.com/" + re.escape(data["repository"]) + r"/pull/([1-9][0-9]*)", data["native_pr_url"])
    require(match is not None)
    evidence = load_release_evidence(data["repository"], run_id, attempt, data["tag"])
    require(all(data[k] == evidence[k] for k in ("repository", "run_attempt", "commit", "tag", "digest")))
    require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", data["recorded_at"]))
    recorded = datetime.fromisoformat(data["recorded_at"].replace("Z", "+00:00"))
    published = datetime.fromisoformat(evidence["published_at"].replace("Z", "+00:00"))
    require(recorded >= published)
    require(check_publication(int(match[1]), owner, evidence["commit"]) is None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["native", "provenance", "publish-gate", "release"])
    parser.add_argument("--evidence")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--native-pr", type=int)
    parser.add_argument("--commit")
    parser.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    if args.command == "release":
        try:
            if (not args.evidence or any(v is not None for v in (args.pr, args.native_pr, args.commit))
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner)):
                raise ValueError("Invalid release arguments")
            check_release(args.evidence, args.owner)
        except Exception:
            print("RELEASE FAIL: GitHub evidence could not be read or validated.")
            return 1
        print("RELEASE PASS")
        return 0
    if args.evidence is not None:
        parser.error("Evidence path requires release.")
    publication = args.command == "publish-gate"
    prs = [args.native_pr] if publication else [args.pr]
    if publication and (args.pr is not None or not re.fullmatch(SHA, args.commit or "")):
        parser.error("Expected publication PRs and a full commit SHA.")
    if not publication and any(v is not None for v in (args.native_pr, args.commit)):
        parser.error("Publication arguments require publish-gate.")
    if any(pr is None or pr < 1 for pr in prs) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner):
        parser.error("Expected a positive PR number and a GitHub login.")
    try:
        reason = (check_publication(args.native_pr, args.owner, args.commit) if publication
                  else (check_native if args.command == "native" else check_provenance)(args.pr, args.owner))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        # gh stderr and malformed record contents may contain personal data.
        reason = "GitHub evidence could not be read or validated."
    label = args.command.upper()
    print(label + " FAIL: " + reason if reason else label + " PASS")
    return 1 if reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
