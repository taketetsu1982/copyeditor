"""Rebuild distribution copies without touching manifests or operator URLs."""
import argparse
from pathlib import Path

from plugin_checks import check_layout, check_plugin, generated_content


def build(root, client, check=False):
    if check:
        check_plugin(root, client)
        return
    package = check_layout(root, client, generated_required=False)
    for relative, raw in generated_content(root).items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    check_plugin(root, client)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("claude", "codex"), required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        build(Path(__file__).resolve().parents[1], args.client, args.check)
    except (ValueError, OSError, TypeError, KeyError):
        parser.exit(1, "Plugin distribution check failed.\n")


if __name__ == "__main__":
    main()
