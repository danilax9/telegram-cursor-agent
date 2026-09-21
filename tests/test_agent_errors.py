"""Agent failure classification tests."""

from telegram_cursor_agent.services.agent_errors import (
    AgentFailureKind,
    AgentRunOutcome,
    classify_agent_result,
    is_account_switchable_failure,
)


def test_classify_auth_failure() -> None:
    kind = classify_agent_result(
        AgentRunOutcome(
            output="Error: session expired, please login",
            returncode=1,
        )
    )
    assert kind == AgentFailureKind.AUTH
    assert is_account_switchable_failure(kind)


def test_classify_limit_failure() -> None:
    kind = classify_agent_result(
        AgentRunOutcome(
            output="You have reached your usage limit for this billing period.",
            returncode=1,
        )
    )
    assert kind == AgentFailureKind.LIMIT


def test_classify_success() -> None:
    kind = classify_agent_result(
        AgentRunOutcome(output="Done.", returncode=0)
    )
    assert kind == AgentFailureKind.NONE
