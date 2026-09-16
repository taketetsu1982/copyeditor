# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Use GitHub's private vulnerability reporting instead:
**Security tab → Report a vulnerability** on this repository, or open
<https://github.com/taketetsu1982/copyeditor/security/advisories/new>.

Include what you can of the following:

- The affected version or commit
- Steps to reproduce, or a minimal request that triggers the problem
- The impact you expect (for example: text sent to an unintended destination,
  text or credentials written to logs, authentication bypass)

You will get an acknowledgement within 7 days. Fixes are published as a new
tagged release with a note in the release description. Reporters are credited
in the advisory unless they ask not to be.

## Supported versions

Only the latest tagged release receives security fixes.

## What counts as a security issue here

copyeditor sends the text you ask it to polish to a model provider (Vertex AI
in the reference implementation). Reports in these areas are especially
welcome:

- Text, context, or credentials leaking into server logs or responses
- Text being sent somewhere other than the configured provider
- Bypassing the `google-workspace` authentication mode when it is enabled
- Prompt or input handling that lets one request read or alter another

Questions about configuration hardening that are not vulnerabilities belong in
GitHub Discussions.
