"""Keep static analysis protections from being silently weakened."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _quality_config() -> dict[str, object]:
    raw_config = tomllib.loads((_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return cast(dict[str, object], raw_config)


def test_mutable_default_arguments_are_not_globally_ignored() -> None:
    config = _quality_config()
    tool = config["tool"]
    assert isinstance(tool, dict)
    ruff = tool["ruff"]
    assert isinstance(ruff, dict)
    lint = ruff["lint"]
    assert isinstance(lint, dict)
    ignored_rules = lint.get("ignore", [])
    assert isinstance(ignored_rules, list)

    assert "B006" not in ignored_rules


def test_all_type_checker_rules_are_ci_errors() -> None:
    config = _quality_config()
    tool = config["tool"]
    assert isinstance(tool, dict)
    ty = tool["ty"]
    assert isinstance(ty, dict)
    rules = ty["rules"]
    assert isinstance(rules, dict)

    assert rules["all"] == "error"
