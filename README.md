# Wred organization resources

Shared resources for the [Wred organization](https://github.com/wred-asgi).

- [Organization profile](profile/README.md)
- [Extension contract v1.1](EXTENSIONS.md)
- [Production readiness and migration](PRODUCTION.md)
- [Organization validation workflow](.github/workflows/extensions.yml)
- [Cross-extension composition tests](tests/test_composition.py)

## Run the composition suite

Clone the core and extensions alongside this repository, then install all source
checkouts together so their current dependency constraints are resolved together:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ../wred \
  -e '../wred-auth[test]' -e '../wred-cache[test]' \
  -e '../wred-cors[test]' -e '../wred-errors[test]' \
  -e '../wred-jinja2[test]' -e '../wred-lifespan[test]' -e '../wred-pagination[test]' \
  -e '../wred-pydantic[test]' -e '../wred-ratelimit[test]'
python -m pytest tests
```

Package-specific tests remain in their respective repositories. Changes to shared
conventions should include a composition regression and be coordinated with the
affected package releases. `profile/README.md` is the organization's landing page.

For the full release gate, use `scripts/validate.py` with all service URLs set as
described in [PRODUCTION.md](PRODUCTION.md). The quick composition command above
does not substitute for service, installed-distribution, or dependency checks.
