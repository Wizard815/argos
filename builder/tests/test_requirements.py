"""Every third-party module the app imports is declared in requirements.txt.

This is here because psycopg was added to the source and not to the file the
image installs from. Nothing caught it: the tests ran with it installed by hand,
and the only symptom was the container dying at startup with a bare
ModuleNotFoundError from inside a constructor.
"""
import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# import name -> the package that provides it, where they differ
PACKAGE_OF = {
    "yaml": "PyYAML",
    "psycopg": "psycopg",
    "flask": "flask",
    "waitress": "waitress",
    "numpy": "numpy",
}
# Everything the standard library provides, plus the app's own package.
STDLIB = set(sys.stdlib_module_names)


def declared() -> set[str]:
    with open(os.path.join(ROOT, "requirements.txt")) as f:
        lines = [l.split("#")[0].strip() for l in f]
    names = set()
    for line in lines:
        if not line:
            continue
        # strip any version specifier and extras: psycopg[binary]>=3.2 -> psycopg
        name = line.split("[")[0]
        for sep in (">=", "==", "<=", "~=", ">", "<", "="):
            name = name.split(sep)[0]
        names.add(name.strip().lower())
    return names


def imported() -> set[str]:
    """Top-level third-party modules imported anywhere in src/."""
    found = set()
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, "src")):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(dirpath, filename)) as f:
                tree = ast.parse(f.read(), filename)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        found.add(node.module.split(".")[0])
    return found


def test_every_import_is_declared():
    have = declared()
    missing = []
    for module in sorted(imported()):
        if module in STDLIB or module in ("src",):
            continue
        package = PACKAGE_OF.get(module, module)
        if package.lower() not in have:
            missing.append(f"{module} (declare {package})")
    assert not missing, "imported but not in requirements.txt: " + ", ".join(missing)


def test_the_postgres_driver_is_declared():
    # The one that actually got missed.
    assert "psycopg" in declared()
