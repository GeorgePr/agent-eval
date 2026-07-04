# Security policy

## Reporting a vulnerability

**Please do not open a public issue for security-sensitive reports.**

Report privately using GitHub's **[Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)**
(the repo's *Security → Report a vulnerability* tab), or contact the maintainer
directly.

> Maintainer contact: _EDIT ME_ — replace with a monitored email or preferred
> private channel before publishing the repo.

Please include: affected version/commit, a minimal reproduction, the impact you
observed, and any suggested fix. We aim to acknowledge reports promptly and will
coordinate a fix and disclosure timeline with you.

## Security posture

AgentEval is a local CLI, which keeps its attack surface small:

- **No network service or daemon** — it runs, prints, exits.
- **No default LLM/API calls** — the deterministic core makes zero outbound calls.
  Optional `semantic`/`judge` assertions are stubs and are never invoked by default.
- **HTTP target mode only calls URLs you supply** (`--target`), with headers/tokens
  you provide. It never contacts a third party on its own.
- **Bearer tokens are read from environment variables** (`--http-bearer-token-env`),
  never from the command line or committed config.
- **Agent loading imports your `module:function`.** Only run AgentEval against
  agents and scenario files you trust — importing a module runs its top-level code.
- **Releases use PyPI Trusted Publishing (OIDC)** — no API tokens are stored as
  secrets or committed to the repo.

## Supported versions

This is a pre-1.0 project; security fixes target the latest release and `main`.
