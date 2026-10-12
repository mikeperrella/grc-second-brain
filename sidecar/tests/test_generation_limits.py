"""The sizes of one generation call (ADR-012, decision 4) are derived, and the arithmetic is pinned.

The expected numbers below are written out by hand, with their own integer arithmetic, so that a cap
changed in `limits.py` without the owner noticing makes a test fail instead of silently moving the
context window.
"""

from grc_sidecar.llm import limits
from grc_sidecar.retrieval import selection


def ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def test_the_message_and_prompt_caps_add_up() -> None:
    assert limits.MAX_MESSAGE_CHARS == 3_000 + 1_000 + 5_520 + 1_000 + 1_200 == 11_720
    assert limits.MAX_PROMPT_CHARS == 11_720 + 500 == 12_220


def test_the_token_bound_uses_the_worst_measured_ratio_with_independent_arithmetic() -> None:
    # 12,220 characters / 1.38 characters per token, rounded up, in integers: 1,222,000 / 138
    assert limits.PROMPT_TOKEN_BOUND == ceil_div(12_220 * 100, 138) == 8_856


def test_num_predict_is_derived_from_the_output_schema_maximum() -> None:
    assert limits.MAX_OUTPUT_CHARS == 1_200 + 400 + 300 == 1_900
    assert limits.NUM_PREDICT == 1_900


def test_num_ctx_is_derived_from_the_caps() -> None:  # [NEG] mutation: change a cap, keep this
    needed = 8_856 + 1_900 + 256
    assert needed == 11_012
    assert limits.NUM_CTX == ceil_div(needed, 1_024) * 1_024 == 11_264
    assert limits.NUM_CTX % 1_024 == 0
    assert limits.NUM_CTX >= limits.PROMPT_TOKEN_BOUND + limits.NUM_PREDICT + 256
    assert limits.NUM_CTX - limits.NUM_PREDICT > limits.PROMPT_TOKEN_BOUND  # the post-check window


def test_the_sources_cap_and_the_ratio_equal_retrievals_own_constants() -> None:  # [NEG]
    """`limits.py` repeats these two numbers so that `llm` does not import `retrieval`; this test
    is what keeps the copies from drifting apart."""
    assert limits.SOURCES_MAX_CHARS == selection.DEFAULT_CHAR_BUDGET == 5_520
    assert limits.WORST_CHARS_PER_TOKEN == selection.WORST_CHARS_PER_TOKEN == 1.38


# ----- the read timeout (M5k, ADR-012 decision 9) ------------------------------------------------
# Written out with independent integer arithmetic, like the context window above.


def test_the_decode_time_of_a_maximal_reply_at_the_measured_speed() -> None:
    # 1,900 tokens at 5.8 tokens per second: 19,000 / 58 = 327.6, rounded up
    assert limits.MEASURED_DECODE_TOKENS_PER_SECOND_TENTHS == 58
    assert limits.DECODE_SECONDS_AT_MAX_OUTPUT == ceil_div(1_900 * 10, 58) == 328


def test_the_read_timeout_is_derived_from_the_output_cap_and_the_decode_speed() -> None:  # [NEG]
    assert limits.READ_TIMEOUT_MARGIN_FACTOR == 2
    assert limits.READ_TIMEOUT_SECONDS == ceil_div(328 * 2, 60) * 60 == 660
    assert limits.FIRST_CALL_READ_TIMEOUT_SECONDS == 660 + 60 == 720
    assert limits.READ_TIMEOUT_SECONDS % 60 == 0


def test_the_read_timeout_outlasts_a_maximal_reply_with_room_to_spare() -> None:  # [NEG]
    # At the measured speed a maximal reply takes 328 seconds. The old value, 120, would have cut
    # it off and stopped a run as if Ollama were gone.
    assert limits.READ_TIMEOUT_SECONDS > limits.DECODE_SECONDS_AT_MAX_OUTPUT
    assert limits.READ_TIMEOUT_SECONDS >= 2 * limits.DECODE_SECONDS_AT_MAX_OUTPUT
    assert limits.READ_TIMEOUT_SECONDS > 165.8  # the slowest question seen at live check A


def test_the_timeout_follows_the_inputs_it_is_derived_from() -> None:  # [NEG]
    # The formula, with other inputs: a larger output cap, a slower machine or a bigger margin each
    # move the timeout, so a typed-in number could not pass this.
    assert limits.derive_read_timeout(1_900, 58, 2) == 660
    assert limits.derive_read_timeout(3_800, 58, 2) == 1_320  # twice the output
    assert limits.derive_read_timeout(1_900, 29, 2) == 1_320  # half the speed
    assert limits.derive_read_timeout(1_900, 58, 4) == 1_320  # twice the margin
    assert limits.derive_read_timeout(60, 60, 1) == 60  # exact minutes are not rounded up


def test_decoding_is_deterministic_on_one_machine() -> None:
    assert limits.TEMPERATURE == 0
    assert limits.SEED == 42
