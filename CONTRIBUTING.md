# Contributing to loopmonitor

Thank you for your interest in contributing to `loopmonitor`.

## Reporting bugs

Please open an issue on GitHub with:
- A minimal reproducible example
- Your Python version and OS
- The full error message or unexpected output

## Suggesting features

Open an issue describing the use case and why the existing interface does not cover it.

## Submitting a pull request

1. Fork the repository and create a branch from `main`.
2. Install the package in editable mode with test dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
3. Make your changes.
4. Run the test suite and confirm all tests pass:
   ```bash
   pytest tests/
   ```
5. Open a pull request against `main` with a clear description of what changed and why.

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Keep functions focused and avoid unnecessary abstractions.
- Add tests for any new behaviour.

## Code of Conduct

All contributors are expected to follow the [Python Software Foundation Code of Conduct](https://policies.python.org/python.org/code-of-conduct/).

