import pytest
from guardrails import Guard
from guardrails.errors import ValidationError
from nace_sdk import Noul
from pydantic import BaseModel, Field

from drex_policy import DrexPolicy

from .fakes import noul

QUESTION = Noul(instructions="Is this unsafe?")


def test_use_accepts_multiple_validators_and_distinguishes_failures():
    clients = [noul(0.9), noul(0.9)]
    messages = []

    def record(value, failure):
        messages.append(failure.error_message)
        return value

    guard = Guard().use(
        DrexPolicy(QUESTION, 0.5, client=clients[0], on_fail=record),
        DrexPolicy(
            QUESTION, 0.5, question_id="scope", client=clients[1], on_fail=record
        ),
    )
    guard.validate("hello")
    assert [len(client.calls) for client in clients] == [1, 1]
    assert sorted(messages) == [
        "Drex P(yes)=0.9 >= fail_at=0.5",
        "Drex P(yes)=0.9 >= fail_at=0.5 [scope]",
    ]
    logs = guard.history.last.validator_logs
    assert {log.validation_result.metadata["drex_question_id"] for log in logs} == {
        "policy",
        "scope",
    }


def test_use_twice_replaces_the_previous_validator():
    first, second = noul(0.1), noul(0.1)
    guard = (
        Guard()
        .use(DrexPolicy(QUESTION, 0.5, client=first))
        .use(DrexPolicy(QUESTION, 0.5, client=second))
    )
    assert guard.validate("hello").validation_passed is True
    assert first.calls == []
    assert len(second.calls) == 1


def test_stream_checks_sentences_without_previous_context():
    client = noul(0.1)
    validator = DrexPolicy(QUESTION, 0.5, client=client)
    for chunk in ["Hello there. ", "How are ", "you today? ", "I am fine. ", "Bye"]:
        validator.validate_stream(chunk, {})
    validator.validate_stream("", {}, remainder=True)
    assert [call[0].strip() for call in client.calls] == [
        "Hello there.",
        "How are you today?",
        "I am fine.",
        "Bye",
    ]


def test_validates_input_messages_before_calling_the_model():
    client = noul(0.9)
    guard = Guard().use(
        DrexPolicy(QUESTION, 0.5, client=client, on_fail="exception"), on="messages"
    )

    def llm(*args, **kwargs):
        pytest.fail("The input guard must stop the model call")

    with pytest.raises(ValidationError, match="fail_at"):
        guard(llm, messages=[{"role": "user", "content": "synthetic unsafe input"}])
    assert [call[0] for call in client.calls] == ["synthetic unsafe input"]


def test_pydantic_field_validation():
    client = noul(0.9)

    class Reply(BaseModel):
        answer: str = Field(
            validators=[DrexPolicy(QUESTION, 0.5, client=client, on_fail="noop")]
        )
        topic: str

    guard = Guard.for_pydantic(Reply)
    result = guard.parse('{"answer": "example answer", "topic": "billing"}')
    assert result.validation_passed is False
    assert [call[0] for call in client.calls] == ["example answer"]


def test_sequential_chain_preserves_images_and_other_metadata(monkeypatch):
    monkeypatch.setenv("GUARDRAILS_RUN_SYNC", "true")
    clients = [noul(0.1), noul(0.1)]
    metadata = {"images": ["AAAA"], "other_validator": {"limit": 3}}
    guard = Guard().use(
        *[DrexPolicy(QUESTION, 0.5, client=client) for client in clients]
    )
    assert guard.validate("hello", metadata=dict(metadata)).validation_passed is True
    assert [client.calls[0][3] for client in clients] == [["AAAA"], ["AAAA"]]
    for log in guard.history.last.validator_logs:
        assert log.validation_result.metadata["other_validator"] == {"limit": 3}
    assert metadata == {"images": ["AAAA"], "other_validator": {"limit": 3}}


@pytest.mark.parametrize("action", ["exception", "noop"])
def test_full_sync_stream_emits_unchecked_remainder_and_retains_it(action):
    client = noul(0.1)
    validator = DrexPolicy(QUESTION, 0.5, client=client, on_fail=action)
    guard = Guard().use(validator)

    def first_stream(*args, **kwargs):
        yield "unfinished fragment"

    outcomes = list(
        guard(first_stream, messages=[{"role": "user", "content": "test"}], stream=True)
    )
    assert (
        "".join(outcome.validated_output for outcome in outcomes)
        == "unfinished fragment"
    )
    assert client.calls == []
    assert "".join(validator.accumulated_chunks) == "unfinished fragment"

    def second_stream(*args, **kwargs):
        yield " continued. "

    list(
        guard(
            second_stream, messages=[{"role": "user", "content": "test"}], stream=True
        )
    )
    assert [call[0].strip() for call in client.calls] == [
        "unfinished fragment continued."
    ]
