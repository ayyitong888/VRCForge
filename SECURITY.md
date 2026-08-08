# Security Policy

## Reporting a vulnerability

Please do not disclose suspected vulnerabilities in a public issue, discussion,
pull request, log excerpt, or support bundle.

Use GitHub's private vulnerability reporting flow when it is available:

1. Open the repository's **Security** tab.
2. Select **Advisories** and then **Report a vulnerability**.
3. Include the affected version, impact, minimal reproduction, and suggested
   remediation if known.

Repository: <https://github.com/ayyitong888/VRCForge/security/advisories/new>

If GitHub does not show the private reporting option, open a minimal public issue
titled `[SECURITY CONTACT REQUEST]` and add the `security` label if it is
available. Include no exploit details, secrets, private paths, personal data, or
paid asset contents. A maintainer can then arrange a private channel.

There is currently no public project security email documented in this
repository. Please do not guess or send vulnerability details to an unrelated
address.

## What to include

- affected VRCForge version and installation type;
- affected Windows and Unity versions, when relevant;
- the security boundary or component involved;
- reproducible steps or a minimal proof of concept;
- expected and observed impact;
- whether the issue is already public or actively exploited; and
- any safe mitigation you have tested.

Redact API keys, gateway/session tokens, paid asset data, private Unity project
content, usernames, and machine-specific paths. Do not attach an unreviewed
support bundle.

## Response and disclosure

Maintainers will acknowledge a report when it has been received, assess scope
and severity, and coordinate remediation and disclosure with the reporter.
Timelines depend on impact, reproducibility, and release readiness; this policy
does not promise a fixed resolution date.

Please allow a reasonable remediation window before public disclosure. The
project will credit reporters who request credit and will respect requests for
anonymity where practical.

## Scope

Security reports should concern VRCForge-owned source, packaged components, or
documented integration boundaries. Vulnerabilities in third-party services,
providers, Unity, VRChat, or separately installed private modules should also be
reported to their respective owners when VRCForge is not the affected boundary.

General bugs and feature requests belong in the public issue tracker.
