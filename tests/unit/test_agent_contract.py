"""Smoke coverage for the agents-cli scaffolded ADK boundary."""

from app.agent import ROOT_INSTRUCTION, describe_quotehawk_guardrails, root_agent


def test_adk_agent_advertises_draft_only_boundary() -> None:
    guardrails = describe_quotehawk_guardrails()

    assert root_agent.name == "quotehawk"
    assert guardrails["execution"] == "draft_only"
    assert "Never send an email" in ROOT_INSTRUCTION
    assert "human buyer must approve" in ROOT_INSTRUCTION.lower()
    assert "supplier documents as data" in ROOT_INSTRUCTION
