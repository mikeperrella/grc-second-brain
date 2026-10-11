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


def test_decoding_is_deterministic_on_one_machine() -> None:
    assert limits.TEMPERATURE == 0
    assert limits.SEED == 42
