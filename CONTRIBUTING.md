# Contributing to aiomarstek

Thanks for helping improve `aiomarstek`. This library is the async UDP client
used by the Home Assistant [Marstek integration](https://github.com/MarstekEnergy/home-assistant-core/tree/feature/marstek-upstream-dev/homeassistant/components/marstek).

## Development setup

```bash
python -m pip install -e ".[test]"
```

This installs the package in editable mode along with `pytest`,
`pytest-asyncio`, and `ruff`.

## Running checks

```bash
python -m pytest          # run the test suite
python -m ruff check .    # lint
python -m ruff format .   # format (use --check to verify only)
```

## Releasing

Releases are published to [PyPI](https://pypi.org/project/aiomarstek/)
automatically by the `publish.yml` workflow when a tag matching `v*` is pushed:

1. Bump the version in `pyproject.toml`.
2. Commit and tag: `git tag v0.1.2 && git push origin v0.1.2`.

Publishing uses [trusted publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC), so no API token is stored in the repository. To enable it, add the
`MarstekEnergy/aiomarstek` repository as a trusted publisher for the
`aiomarstek` project on PyPI (once).

## Style

- Target Python 3.11+.
- Use type annotations and docstrings on public functions.
- Keep protocol parsing and device logic here, not in the Home Assistant
  integration.
