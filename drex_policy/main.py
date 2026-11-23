import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import Future
from importlib.metadata import version
from threading import Event, Lock
from typing import Any, Literal, Protocol, TypeVar, cast

from guardrails.validator_base import (
    FailResult,
    PassResult,
    ValidationResult,
    Validator,
    register_validator,
)
from nace_sdk import (
    AsyncNaceClient,
    ImageInput,
    NaceAPIConnectionError,
    NaceAPIError,
    NaceAPIResponseValidationError,
    NaceClient,
    NaceError,
    Noul,
    NoulAnswer,
    RetryPolicy,
    Score,
    ScoreAnswer,
    UploadedFile,
    WorkspaceFileSource,
    image_input,
)

VERSION = version("drex-policy")
CLIENT_INFO = f"drex-policy/{VERSION}"
DEFAULT_MODEL = "drex-max-v1.1"
TEXT_ONLY_MODELS = {"drex-v2.0"}
MAX_IMAGES = 8

Question = Noul | Score
OnError = Literal["raise", "fail", "pass"]
ResponseT_co = TypeVar("ResponseT_co", covariant=True)
logger = logging.getLogger("drex_policy")
_client_lock = Lock()


class DrexPolicyError(Exception):
    """Invalid policy input or an unusable Drex answer."""


class _UnusableAnswer(DrexPolicyError):
    """Drex returned a response without a usable answer."""


class SystemOneCaller(Protocol[ResponseT_co]):
    def system_one(
        self,
        state: str,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
        images: Sequence[ImageInput] | None = None,
    ) -> ResponseT_co: ...


def _decide(kind: str, measured: float, fail_at: float, fail_when: str) -> bool:
    """Return whether a finite measurement crosses the configured threshold."""
    if (
        isinstance(measured, bool)
        or not isinstance(measured, (int, float))
        or not math.isfinite(measured)
        or (kind == "noul" and not 0 <= measured <= 1)
    ):
        raise _UnusableAnswer(f"Drex returned an unusable {kind} value")
    return measured < fail_at if fail_when in ("no", "below") else measured >= fail_at


def _classify_error(error: Exception) -> Literal["availability", "unusable", "caller"]:
    if isinstance(error, _UnusableAnswer):
        return "unusable"
    if isinstance(error, NaceAPIConnectionError):
        return "availability"
    if isinstance(error, NaceAPIError):
        status = getattr(error, "status", None)
        if status in (408, 429) or (isinstance(status, int) and 500 <= status < 600):
            return "availability"
        if isinstance(error, NaceAPIResponseValidationError):
            return "unusable"
    return "caller"


def _request_id(response: Any) -> str | None:
    try:
        request_id = getattr(response, "request_id", None)
    except NaceError:
        return None
    if isinstance(request_id, str) and re.fullmatch(
        r"[A-Za-z0-9._:-]{1,128}", request_id
    ):
        return request_id
    return None


def _is_base64(value: str) -> bool:
    return (
        len(value) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value) is not None
    )


def _images(metadata: dict[str, Any] | None, model: str) -> list[ImageInput] | None:
    images = (metadata or {}).get("images")
    if images is None:
        return None
    if not isinstance(images, (list, tuple)):
        raise DrexPolicyError("metadata['images'] must be a list of images")
    if not images:
        return None
    if model in TEXT_ONLY_MODELS:
        raise DrexPolicyError(
            f"{model!r} is text only. Use 'drex-max-v1.1' for images."
        )
    if len(images) > MAX_IMAGES:
        raise DrexPolicyError(f"Pass at most {MAX_IMAGES} images with 'drex-max-v1.1'.")
    for image in images:
        if isinstance(image, str) and (
            (
                image.startswith("data:")
                and ";base64," in image
                and _is_base64(image.split(";base64,", 1)[1])
            )
            or _is_base64(image)
        ):
            continue
        if isinstance(image, (Mapping, UploadedFile, WorkspaceFileSource)):
            try:
                reference = (
                    image_input(image)
                    if isinstance(image, (UploadedFile, WorkspaceFileSource))
                    else image
                )
                if (
                    not isinstance(reference, Mapping)
                    or reference.get("type") != "workspace_file"
                ):
                    raise ValueError("Expected a workspace_file reference")
                WorkspaceFileSource.model_validate(reference, strict=True)
            except (ValueError, TypeError) as error:
                raise DrexPolicyError(
                    "A workspace_file image needs string workspace_id and file_id fields."
                ) from error
            continue
        raise DrexPolicyError(
            "Pass base64, a data URL, or an image_input() value for each image."
        )
    return list(images)


def _answer(
    response: Any, question_id: str, question: Question
) -> NoulAnswer | ScoreAnswer:
    answers = getattr(response, "answers", None)
    if not isinstance(answers, Mapping) or question_id not in answers:
        raise _UnusableAnswer(f"Drex response has no answer for {question_id!r}")
    answer = answers[question_id]
    expected = NoulAnswer if isinstance(question, Noul) else ScoreAnswer
    if not isinstance(answer, expected):
        raise _UnusableAnswer(
            f"Drex answer for {question_id!r} is {type(answer).__name__}, expected {expected.__name__}"
        )
    return answer


@register_validator(name="nace/drex_policy", data_type="string")
class DrexPolicy(Validator):
    """Check text against one Drex noul or score question."""

    def __init__(
        self,
        question: Question | dict[str, Any],
        fail_at: float,
        fail_when: str | None = None,
        question_id: str = "policy",
        model: str | None = None,
        client: SystemOneCaller[Any] | None = None,
        on_fail: str | Callable[[Any, FailResult], Any] | None = None,
        *,
        on_error: OnError = "raise",
        fix_value: str | None = None,
        async_client: SystemOneCaller[Awaitable[Any]] | None = None,
        **kwargs: Any,
    ):
        if isinstance(question, dict):
            if question.get("type") == "noul":
                question = Noul.model_validate(question)
            elif question.get("type") == "score":
                question = Score.model_validate(question)
        if not isinstance(question, (Noul, Score)):
            raise TypeError(
                "question must be a nace_sdk.Noul or nace_sdk.Score, or its dictionary"
            )
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("question_id must be a non-empty string")
        if isinstance(fail_at, bool) or not isinstance(fail_at, (int, float)):
            raise TypeError("fail_at must be a number")
        if model is not None and (not isinstance(model, str) or not model):
            raise ValueError("model must be a non-empty string")
        if on_error not in ("raise", "fail", "pass"):
            raise ValueError("on_error must be 'raise', 'fail', or 'pass'")
        if fix_value is not None and not isinstance(fix_value, str):
            raise TypeError("fix_value must be a string")
        if (
            isinstance(on_fail, str)
            and on_fail.lower() in ("fix", "fix_reask")
            and fix_value is None
        ):
            raise ValueError("on_fail='fix' and 'fix_reask' require fix_value")
        if isinstance(question, Noul):
            if not question.instructions and not any(
                (question.criteria or {}).values()
            ):
                raise ValueError(
                    "a noul needs instructions or criteria describing an outcome"
                )
            fail_when = "yes" if fail_when is None else fail_when
            if fail_when not in ("yes", "no"):
                raise ValueError("noul fail_when must be 'yes' or 'no'")
            if not 0.0 <= fail_at <= 1.0:
                raise ValueError("noul fail_at must be between 0 and 1")
        else:
            fail_when = "at_or_above" if fail_when is None else fail_when
            if fail_when not in ("at_or_above", "below"):
                raise ValueError("score fail_when must be 'at_or_above' or 'below'")
            if not math.isfinite(fail_at):
                raise ValueError("score fail_at must be finite")
        super().__init__(
            on_fail=cast(Any, on_fail),
            question=question.model_dump(mode="json"),
            fail_at=fail_at,
            fail_when=fail_when,
            question_id=question_id,
            model=model,
            on_error=on_error,
            fix_value=fix_value,
            **kwargs,
        )
        self._question = question
        self._fail_at = float(fail_at)
        self._fail_when = fail_when
        self._question_id = question_id
        self._model = model or DEFAULT_MODEL
        self._on_error = on_error
        self._fix_value = fix_value
        self._client = client
        self._async_client = async_client
        self._owned_client: NaceClient | None = None
        self._owned_async_clients: dict[asyncio.AbstractEventLoop, AsyncNaceClient] = {}
        self._pending_closes: list[Future[None]] = []

    def _get_client(self) -> SystemOneCaller[Any]:
        if self._client is not None:
            return self._client
        with _client_lock:
            if self._owned_client is None:
                self._owned_client = NaceClient(
                    client_info=CLIENT_INFO, timeout=60, retry=RetryPolicy(timeout=180)
                )
            return self._owned_client

    def _get_async_client(self) -> SystemOneCaller[Awaitable[Any]]:
        if self._async_client is not None:
            return self._async_client
        loop = asyncio.get_running_loop()
        with _client_lock:
            if loop not in self._owned_async_clients:
                self._owned_async_clients[loop] = AsyncNaceClient(
                    client_info=CLIENT_INFO, timeout=60, retry=RetryPolicy(timeout=180)
                )
            return self._owned_async_clients[loop]

    def _take_owned_clients(self):
        with _client_lock:
            client, self._owned_client = self._owned_client, None
            clients, self._owned_async_clients = self._owned_async_clients, {}
            pending, self._pending_closes = self._pending_closes, []
        if client is not None:
            client.close()
        return clients, pending

    async def _close_async_clients(
        self,
        clients: dict[asyncio.AbstractEventLoop, AsyncNaceClient],
        pending: list[Future[None]],
    ) -> None:
        for future in pending:
            await asyncio.wrap_future(future)
        current_loop = asyncio.get_running_loop()
        for loop, client in clients.items():
            try:
                if loop is not current_loop and loop.is_running():
                    await asyncio.wrap_future(
                        asyncio.run_coroutine_threadsafe(client.aclose(), loop)
                    )
                elif loop is not current_loop and not loop.is_closed():
                    await asyncio.to_thread(loop.run_until_complete, client.aclose())
                else:
                    await client.aclose()
            except RuntimeError as error:
                if not loop.is_closed() or str(error) != "Event loop is closed":
                    raise
                logger.warning(
                    "An owned client's event loop was closed before cleanup. "
                    "Call aclose() before closing its loop to release all connections."
                )

    def close(self) -> None:
        """Close owned clients, scheduling async cleanup if a loop is running."""
        clients, pending = self._take_owned_clients()
        if not clients and not pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._close_async_clients(clients, pending))
        else:
            with _client_lock:
                self._pending_closes.append(
                    asyncio.run_coroutine_threadsafe(
                        self._close_async_clients(clients, pending), loop
                    )
                )

    async def aclose(self) -> None:
        """Await cleanup of every owned client after validation has finished."""
        clients, pending = self._take_owned_clients()
        await self._close_async_clients(clients, pending)

    def _call(
        self,
        client: SystemOneCaller[ResponseT_co],
        value: str,
        images: list[ImageInput] | None,
        cancelled: Event | None = None,
    ) -> ResponseT_co:
        if cancelled is not None and cancelled.is_set():
            raise asyncio.CancelledError
        return client.system_one(
            value, {self._question_id: self._question}, model=self._model, images=images
        )

    def _result(
        self, response: Any, input_metadata: dict[str, Any] | None
    ) -> ValidationResult:
        answer = _answer(response, self._question_id, self._question)
        if isinstance(answer, NoulAnswer):
            label, measured = "P(yes)", answer.noul
        else:
            label, measured = "score", answer.score
        failed = _decide(answer.type, measured, self._fail_at, self._fail_when)
        usage = getattr(response, "usage", None)
        metadata = {
            **(input_metadata or {}),
            "drex_model": self._model,
            "drex_question_id": self._question_id,
            "drex_response_model": getattr(response, "model", None),
            "drex_usage": usage.model_dump(mode="json", exclude_none=True)
            if usage is not None
            else None,
            "drex_request_id": _request_id(response),
            "drex_evaluation_time_ms": getattr(response, "evaluation_time_ms", None),
            "drex_measured": measured,
            "drex_fail_at": self._fail_at,
            "drex_fail_when": self._fail_when,
            "drex_answer": answer.model_dump(mode="json", exclude_none=True),
        }
        if failed:
            relation = "<" if self._fail_when in ("no", "below") else ">="
            suffix = f" [{self._question_id}]" if self._question_id != "policy" else ""
            return FailResult(
                error_message=f"Drex {label}={float(measured)} {relation} fail_at={self._fail_at}{suffix}",
                metadata=metadata,
                fix_value=self._fix_value,
            )
        return PassResult(metadata=metadata)

    def _error_result(
        self,
        error: Exception,
        response: Any,
        input_metadata: dict[str, Any] | None,
        value: str,
    ) -> ValidationResult | None:
        category = _classify_error(error)
        if category != "caller":
            self._check_replacement(value, None)
        if (
            category == "unusable"
            and self._on_error != "fail"
            and not isinstance(error, DrexPolicyError)
        ):
            raise _UnusableAnswer("Drex returned an unusable answer") from error
        if (
            self._on_error == "raise"
            or category == "caller"
            or (category == "unusable" and self._on_error == "pass")
        ):
            return None
        error_type = (
            "DrexPolicyError" if category == "unusable" else type(error).__name__
        )
        status = getattr(error, "status", None)
        error_message = (
            error_type if status is None else f"{error_type} (HTTP {status})"
        )
        metadata = {
            **(input_metadata or {}),
            "drex_model": self._model,
            "drex_question_id": self._question_id,
            "drex_error": {
                "type": error_type,
                "status": getattr(error, "status", None),
                "request_id": _request_id(error) or _request_id(response),
                "message": error_message,
            },
        }
        message = f"Drex could not decide: {error_type}"
        if self._on_error == "fail":
            return FailResult(
                error_message=message, metadata=metadata, fix_value=self._fix_value
            )
        logger.warning("%s. Passing because on_error='pass'.", message)
        return PassResult(metadata=metadata)

    def _check_replacement(self, value: str, result: ValidationResult | None) -> None:
        if (
            self.on_fail_descriptor == "fix_reask"
            and value == self._fix_value
            and not isinstance(result, PassResult)
        ):
            raise DrexPolicyError(
                "The fix_value does not pass the policy, or could not be checked."
            )

    def validate(
        self, value: Any, metadata: dict[str, Any] | None = None
    ) -> ValidationResult:
        return self._validate(value, metadata)

    def _validate(
        self,
        value: Any,
        metadata: dict[str, Any] | None = None,
        cancelled: Event | None = None,
    ) -> ValidationResult:
        if not isinstance(value, str):
            raise TypeError("drex_policy validates a string")
        images = _images(metadata, self._model)
        response = None
        try:
            response = self._call(self._get_client(), value, images, cancelled)
            result = self._result(response, metadata)
        except Exception as error:
            result = self._error_result(error, response, metadata, value)
            if result is None:
                raise
        self._check_replacement(value, result)
        return result

    async def async_validate(
        self, value: Any, metadata: dict[str, Any] | None = None
    ) -> ValidationResult:
        if self._client is not None and self._async_client is None:
            cancelled = Event()
            future = asyncio.get_running_loop().run_in_executor(
                None, self._validate, value, metadata, cancelled
            )
            try:
                return await future
            except asyncio.CancelledError:
                cancelled.set()
                raise
        if not isinstance(value, str):
            raise TypeError("drex_policy validates a string")
        images = _images(metadata, self._model)
        response = None
        try:
            response = await self._call(self._get_async_client(), value, images)
            result = self._result(response, metadata)
        except Exception as error:
            result = self._error_result(error, response, metadata, value)
            if result is None:
                raise
        self._check_replacement(value, result)
        return result
