"""Resolve, build and test every extension together, including installed wheels.

Run in an isolated virtualenv. Required Redis/PostgreSQL/MongoDB URLs must point
at disposable services. The MongoDB service must be a replica set.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGES = {
    "auth": "test,sqlalchemy,mongodb",
    "cache": "test,redis",
    "cors": "test",
    "errors": "test",
    "lifespan": "test",
    "pagination": "test,sqlalchemy",
    "pydantic": "test",
    "ratelimit": "test,redis",
}


def run(*args, cwd=None):
    subprocess.run([sys.executable, *map(str, args)], cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extensions", type=Path, required=True)
    parser.add_argument(
        "--organization", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--wred", choices=("0.14", "0.15"), default="0.15")
    parser.add_argument("--starlette", default=None)
    parser.add_argument(
        "--minimum",
        action="store_true",
        help="Exercise declared runtime dependency floors on Python 3.11",
    )
    options = parser.parse_args()
    missing = [
        name
        for name in ("WRED_TEST_REDIS", "WRED_TEST_POSTGRES", "WRED_TEST_MONGODB")
        if not os.environ.get(name)
    ]
    if missing:
        parser.error("Service tests are mandatory: set " + ", ".join(missing))
    if shutil.which("redis-server") is None:
        parser.error("Install redis-server for the isolated outage/restart tests")
    root, organization = options.extensions.resolve(), options.organization.resolve()
    packages = [root / ("wred-" + name) for name in PACKAGES]
    requirements = [
        f"{path}[{PACKAGES[name]}]" for name, path in zip(PACKAGES, packages)
    ]
    constraints = [f"wred=={options.wred}"]
    if options.starlette:
        constraints.append(f"starlette=={options.starlette}")
    if options.minimum:
        constraints.extend(["-c", str(organization / "constraints-minimum.txt")])
    run("-m", "pip", "install", *requirements, *constraints, "build", "pip-audit")
    run("-m", "pip", "check")
    with tempfile.TemporaryDirectory(prefix="wred-validation-") as directory:
        temporary = Path(directory)
        distributions = temporary / "dist"
        for package in packages:
            run("-m", "build", "--outdir", distributions, package)
        # Rebuild from each sdist too: catches omitted migrations and data assets.
        for archive in distributions.glob("*.tar.gz"):
            run(
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                temporary / "sdist-wheels",
                archive,
            )
        run(
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            *sorted(distributions.glob("*.whl")),
        )
        run("-m", "pip", "check")
        tests = temporary / "tests"
        for package in [*packages, organization]:
            shutil.copytree(package / "tests", tests / package.name.strip("."))
        shutil.copyfile(organization / "pyproject.toml", temporary / "pyproject.toml")
        run("-m", "pytest", tests, "-ra", cwd=temporary)
    # Audit third-party dependencies. Local, unreleased distributions cannot be
    # looked up reliably; review their source and bundled assets separately.
    import importlib.metadata

    dependencies = sorted(
        f"{distribution.metadata['Name']}=={distribution.version}"
        for distribution in importlib.metadata.distributions()
        if not distribution.metadata["Name"].lower().startswith("wred")
    )
    with tempfile.TemporaryDirectory(prefix="wred-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        requirements.write_text("\n".join(dependencies) + "\n")
        run("-m", "pip_audit", "-r", requirements, "--no-deps", "--disable-pip")


if __name__ == "__main__":
    main()
