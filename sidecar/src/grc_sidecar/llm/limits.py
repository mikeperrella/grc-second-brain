"""The sizes of one generation call, derived from named caps (ADR-012, decision 4).

Nothing here is typed in twice: the context window is computed from the caps, and a test asserts
the result, so changing a cap cannot leave a stale `num_ctx` behind.

How much to trust the arithmetic. The 1.38 characters per token behind the token bound was measured
with nomic-embed-text's tokenizer on identifier-dense text (`vault/chunker.py`, ADR-009), not with
the generation model's tokenizer, and a character cap is not a token bound for hostile text (non-
Latin text and emoji can cost more than one token per character). These numbers are therefore
secondary controls and starting values. The control that matters is that every request sets
`shift: false` and carries exactly two messages (ADR-012, decisions 2 and 3); the live oversize
probe at live check A tests that on the real server.
"""

import math
from typing import Final

# ----- the prompt, in characters ---------------------------------------------------------------
SYSTEM_MAX_CHARS: Final = 3_000  # the instructions (the system message)
QUESTION_MAX_CHARS: Final = 1_000  # one questionnaire cell
SOURCES_MAX_CHARS: Final = 5_520  # equals retrieval.selection.DEFAULT_CHAR_BUDGET (tested)
FRAMING_MAX_CHARS: Final = 1_000  # source headers, delimiters, labels
RETRY_TEXT_MAX_CHARS: Final = 1_200  # the exact error shown to the model on attempt 2
TEMPLATE_OVERHEAD_CHARS: Final = 500  # the chat template's own tokens, as characters

# The most characters the two messages together may hold. Checked before anything is sent.
MAX_MESSAGE_CHARS: Final = (
    SYSTEM_MAX_CHARS
    + QUESTION_MAX_CHARS
    + SOURCES_MAX_CHARS
    + FRAMING_MAX_CHARS
    + RETRY_TEXT_MAX_CHARS
)
MAX_PROMPT_CHARS: Final = MAX_MESSAGE_CHARS + TEMPLATE_OVERHEAD_CHARS

# The worst characters-per-token ratio measured in this project (see the module docstring).
WORST_CHARS_PER_TOKEN: Final = 1.38
PROMPT_TOKEN_BOUND: Final = math.ceil(MAX_PROMPT_CHARS / WORST_CHARS_PER_TOKEN)

# ----- the output, from the output schema's maximum ----------------------------------------------
ANSWER_MAX_CHARS: Final = 1_200
QUOTE_MAX_CHARS: Final = 400
OTHER_OUTPUT_CHARS: Final = 300  # the other fields and the JSON punctuation
MAX_OUTPUT_CHARS: Final = ANSWER_MAX_CHARS + QUOTE_MAX_CHARS + OTHER_OUTPUT_CHARS
# One token per character is an ASSUMPTION, not a bound. A longer output ends with
# done_reason "length", which the client turns into OutputTruncatedError (fail closed).
NUM_PREDICT: Final = MAX_OUTPUT_CHARS

# ----- the context window ------------------------------------------------------------------------
CONTEXT_MARGIN_TOKENS: Final = 256
_CONTEXT_ROUNDING: Final = 1_024
NUM_CTX: Final = (
    math.ceil((PROMPT_TOKEN_BOUND + NUM_PREDICT + CONTEXT_MARGIN_TOKENS) / _CONTEXT_ROUNDING)
    * _CONTEXT_ROUNDING
)

# ----- decoding ----------------------------------------------------------------------------------
TEMPERATURE: Final = 0
# A fixed seed makes a run repeatable on one machine. It does NOT guarantee identical output across
# machines, GPUs or Ollama versions; the run summary records the model digest instead.
SEED: Final = 42
