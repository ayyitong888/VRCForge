# Contributing to VRCForge

Thank you for helping improve VRCForge. Contributions should keep the product's
local-first, supervised-write model intact and avoid changing working behavior
without a clearly documented reason.

## Before you start

- Search existing issues and pull requests before opening a duplicate.
- For a bug, include a minimal reproduction and the affected VRCForge, Windows,
  and Unity versions.
- For a feature or behavior change, open an issue first so scope and safety
  expectations can be agreed before implementation.
- Never post API keys, gateway tokens, paid asset contents, private project
  files, or unredacted support bundles.

## Development workflow

1. Fork <https://github.com/ayyitong888/VRCForge> and clone your fork.
2. Create a focused branch from the current default branch. Suggested names:
   `fix/short-description`, `feat/short-description`, or
   `docs/short-description`.
3. Make one coherent change. Keep unrelated cleanup and structural refactors
   in separate pull requests.
4. Run the smallest relevant validation first, then the broader checks required
   by the affected area. Do not use a development Unity project as release-gate
   evidence.
5. Commit with a clear conventional commit message, for example
   `fix(approval): preserve checkpoint on failure`.
6. Push the branch to your fork and open a pull request against this repository.

## Code style and safety

- Follow the style and patterns already used in the file or module you change.
- Keep modules focused and prefer existing project dependencies over new ones.
- Preserve public behavior unless the issue and pull request explicitly call
  for a behavior change.
- Every Unity asset write must remain behind explicit approval, checkpoint,
  validation, and rollback boundaries. Read-only and write paths must stay
  clearly separated.
- New processes, pipes, file handles, or external communication interfaces must
  define their permission scope, lifecycle owner, and authentication method at
  creation time.
- Never commit credentials, local session data, machine-specific paths, private
  add-on details, or generated release artifacts.
- Do not change dependencies or generated lock files unless the contribution
  specifically requires and explains that change.

## Pull requests

A pull request should:

- explain the user-visible problem and the chosen solution;
- link the relevant issue when one exists;
- list exactly what was tested and what remains unverified;
- include screenshots only for real UI changes and review them for private data;
- include a regression test for a bug fix when practical; and
- stay small enough to review without unrelated formatting or refactoring noise.

Maintainers may ask for a change to be split when functionality, refactoring,
dependency work, or documentation are mixed together.

## Issues

Use the repository issue templates for bug reports and feature requests. Keep
security vulnerabilities out of public issues and follow [SECURITY.md](SECURITY.md)
instead. Conduct concerns should follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

By submitting a contribution, you agree that it is licensed under the
repository's [GPL-3.0-only license](LICENSE).
