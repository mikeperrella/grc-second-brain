# Dependency licenses (sidecar)

**Snapshot dated 2026-10-06.** Regenerate and re-review whenever `sidecar/pyproject.toml` or `sidecar/constraints.txt` changes. Versions below match `sidecar/constraints.txt`.

Method: a throwaway stdlib-only script read `importlib.metadata` for every installed distribution in `sidecar/.venv` (Python 3.11.9), skipping pip, setuptools and the editable `grc-sidecar`. For each package it read the license metadata and the license files in `dist-info`, and matched known license text. Mike reviewed and approved the results on 2026-10-06.

Allowed set: MIT, BSD-2-Clause, BSD-3-Clause, Apache-2.0, ISC, PSF-2.0. MPL-2.0 is accepted for `certifi` and `pathspec` only; revisit at M9 (public release / license decision).

| Package | Version | License | Required by |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | fastapi |
| annotated-types | 0.8.0 | MIT | pydantic |
| anyio | 4.15.1 | MIT | httpx, starlette |
| ast_serialize | 0.12.1 | MIT | mypy |
| certifi | 2026.7.22 | MPL-2.0 (approved exception) | httpcore, httpx |
| click | 8.5.0 | BSD-3-Clause | uvicorn |
| colorama | 0.4.6 | BSD-3-Clause | pytest (Windows) |
| fastapi | 0.142.2 | MIT | direct |
| h11 | 0.16.0 | MIT | httpcore, uvicorn |
| httpcore | 1.0.9 | BSD-3-Clause | httpx |
| httpx | 0.28.1 | BSD-3-Clause | direct |
| idna | 3.20 | BSD-3-Clause | anyio, httpx |
| iniconfig | 2.3.0 | MIT | pytest |
| librt | 0.16.0 | MIT | mypy |
| mypy | 2.4.0 | MIT (bundles typeshed: Apache-2.0 and MIT) | direct (dev) |
| mypy_extensions | 1.1.0 | MIT | mypy |
| opentelemetry-api | 1.45.0 | Apache-2.0 | fastapi |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | pytest |
| pathspec | 1.1.1 | MPL-2.0 (approved exception) | mypy |
| pluggy | 1.6.0 | MIT | pytest |
| pydantic | 2.13.5 | MIT | direct |
| pydantic_core | 2.46.5 | MIT | pydantic |
| pydantic-settings | 2.15.0 | MIT | direct |
| Pygments | 2.21.0 | BSD-2-Clause | pytest |
| pytest | 9.1.1 | MIT | direct (dev) |
| python-dotenv | 1.2.4 | BSD-3-Clause | pydantic-settings |
| ruff | 0.16.10 | MIT | direct (dev) |
| starlette | 1.7.0 | BSD-3-Clause | fastapi |
| typing_extensions | 4.16.0 | PSF-2.0 | anyio, fastapi, mypy, opentelemetry-api, pydantic, pydantic_core, starlette, typing-inspection, uvicorn |
| typing-inspection | 0.4.4 | MIT | fastapi, pydantic, pydantic-settings |
| uvicorn | 0.54.0 | BSD-3-Clause | direct |

## Notes

- `opentelemetry-api` is a runtime dependency of fastapi. Only the API package is installed (no SDK or exporters); `tests/test_dependency_hygiene.py` enforces that, and the tests set `OTEL_SDK_DISABLED=true` and `OTEL_TRACES_EXPORTER=none`.
- `certifi`'s license file is a notice about the CA bundle; its MPL-2.0 designation comes from the package metadata and classifier.
- `python-dotenv` is installed because `pydantic-settings` depends on it. `Settings` sets `env_file=None`, so no `.env` file is read.
- Semgrep and Bandit are not installed locally (they arrive with CI at M1.5); this snapshot makes no claim about their results.
