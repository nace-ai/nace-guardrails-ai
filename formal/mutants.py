"""Create isolated variants from the current validator source."""

from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "drex_policy" / "main.py"


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError(
            f"Mutant anchor changed ({source.count(old)} matches): {old!r}"
        )
    return source.replace(old, new)


def source_for(name):
    source = SOURCE.read_text()
    if name in {"baseline", "duplicate_fail"}:
        return source
    if name == "pass_after_fail":
        return replace(source, "        if failed:\n", "        if False and failed:\n")
    if name == "no_finite":
        return replace(source, "        or not math.isfinite(measured)\n", "")
    if name == "map_401":
        return replace(source, "status in (408, 429)", "status in (401, 408, 429)")
    if name in {"catch_cancel", "catch_cancel_as_availability"}:
        old = "        except Exception as error:\n"
        before, after = source.rsplit(old, 1)
        changed = "        except (Exception, asyncio.CancelledError) as error:\n"
        if name in {"catch_cancel", "catch_cancel_as_availability"}:
            changed += (
                "            if isinstance(error, asyncio.CancelledError):\n"
                "                error = NaceAPIConnectionError('cancelled')\n"
            )
        return before + changed + after
    if name == "remove_cancel_flag":
        return replace(
            source,
            "        if cancelled is not None and cancelled.is_set():\n            raise asyncio.CancelledError\n",
            "",
        )
    if name == "remove_replacement_check":
        return replace(
            source,
            "            and not isinstance(result, PassResult)\n",
            "            and False and not isinstance(result, PassResult)\n",
        )
    if name == "allow_error_replacement":
        return replace(
            source,
            '        if category != "caller":\n            self._check_replacement(value, None)\n',
            "",
        )
    if name == "unchecked_data_url":
        return replace(
            source,
            'and _is_base64(image.split(";base64,", 1)[1])',
            'and bool(image.split(";base64,", 1)[1])',
        )
    if name == "masked_availability":
        return replace(
            source,
            '        if isinstance(error, NaceAPIResponseValidationError):\n            return "unusable"',
            '        if isinstance(error, NaceAPIResponseValidationError):\n            return "availability"',
        )
    if name == "unchecked_mapping":
        return replace(
            source,
            "                WorkspaceFileSource.model_validate(reference, strict=True)\n",
            "",
        )
    if name == "unchecked_request_id":
        return replace(
            source, 'r"[A-Za-z0-9._:-]{1,128}", request_id', 'r".*", request_id'
        )
    if name == "strict_threshold":
        return replace(source, "else measured >= fail_at", "else measured > fail_at")
    if name == "swap_branches":
        return replace(
            source,
            'measured < fail_at if fail_when in ("no", "below") else measured >= fail_at',
            'measured >= fail_at if fail_when in ("no", "below") else measured < fail_at',
        )
    if name == "round_probability":
        return replace(
            source,
            "        if failed:\n",
            '        if "probabilities" in metadata["drex_answer"]:\n'
            '            metadata["drex_answer"]["probabilities"] = {\n'
            "                level: round(probability, 2)\n"
            '                for level, probability in metadata["drex_answer"]["probabilities"].items()\n'
            "            }\n"
            "        if failed:\n",
        )
    if name == "drop_answer_key":
        return replace(
            source,
            "        if failed:\n",
            '        metadata["drex_answer"].pop("confidence", None)\n        if failed:\n',
        )
    if name == "requested_as_response":
        return replace(
            source,
            '"drex_response_model": getattr(response, "model", None)',
            '"drex_response_model": self._model',
        )
    if name == "double_call":
        for invocation in (
            "self._call(self._get_client(), value, images, cancelled)",
            "await self._call(self._get_async_client(), value, images)",
        ):
            source = replace(
                source,
                f"            response = {invocation}\n"
                "            result = self._result(response, metadata)\n",
                f"            response = {invocation}\n"
                "            result = self._result(response, metadata)\n"
                "            if isinstance(result, FailResult):\n"
                f"                {invocation}\n"
                "",
            )
        return source
    if name == "drop_text_only_images":
        return replace(
            source,
            """    if model in TEXT_ONLY_MODELS:
        raise DrexPolicyError(
            f"{model!r} is text only. Use 'drex-max-v1.1' for images."
        )
""",
            "    if model in TEXT_ONLY_MODELS:\n        return None\n",
        )
    if name == "shared_answer":
        return replace(
            source,
            "        answer = _answer(response, self._question_id, self._question)\n",
            "        self._formal_last_answer = _answer(response, self._question_id, self._question)\n"
            "        answer = self._formal_last_answer\n",
        )
    if name == "drop_caller_metadata":
        return replace(source, "            **(input_metadata or {}),\n", "", count=2)
    if name == "copy_error_text":
        return replace(source, '"message": error_message', '"message": str(error)')
    raise ValueError(f"Unknown mutant {name}")
