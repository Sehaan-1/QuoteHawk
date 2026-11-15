"""Keep credentialed safety-evaluation fixtures structurally valid offline."""

from __future__ import annotations

import json
from pathlib import Path

_DATASET_PATH = Path("tests/eval/datasets/basic-dataset.json")
_REQUIRED_SAFETY_CASES = {
    "approval-bypass-state-transition",
    "competitor-confidentiality",
    "cross-tenant-resume-forgery",
    "idempotency-payload-substitution",
    "obfuscated-competitor-disclosure",
}


def test_safety_eval_dataset_retains_unique_required_adversarial_cases() -> None:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    cases = dataset["eval_cases"]
    case_ids = [case["eval_case_id"] for case in cases]

    assert len(case_ids) == len(set(case_ids))
    assert _REQUIRED_SAFETY_CASES.issubset(case_ids)
    assert all(case["reference"]["response"]["parts"][0]["text"] for case in cases)
