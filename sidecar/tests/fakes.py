"""A deterministic fake embedding client, so the indexer is tested without Ollama (tests only).

Vectors are unit vectors built from a hashed bag of words, so texts that share words are closer
than texts that do not, and the same text always gives the same vector. Failures can be injected
per document (by a marker word in its text), and as a `BaseException` to simulate a crash.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from functools import cache
from typing import Literal

from grc_sidecar.errors import EmbeddingResponseError, EmbeddingUnavailableError, ModelMissingError
from grc_sidecar.llm.embeddings import QUERY_PREFIX
from grc_sidecar.repositories.base import EmbeddingProfile
from grc_sidecar.vault.chunker import EMBED_DOCUMENT_PREFIX

DIMENSION = 768
WORD = re.compile(r"[a-z0-9][a-z0-9.\-]*")

FailMode = Literal["response", "unavailable", "crash"]


class SimulatedCrash(BaseException):
    """Stands for the process dying: a BaseException that no `except Exception` will catch."""


@cache
def _word_vector(word: str) -> tuple[float, ...]:
    raw = hashlib.shake_256(word.encode("utf-8")).digest(DIMENSION * 2)
    return tuple(
        (int.from_bytes(raw[i : i + 2], "big") / 32767.5) - 1.0 for i in range(0, len(raw), 2)
    )


def vector_for(text: str) -> list[float]:
    body = text.removeprefix(EMBED_DOCUMENT_PREFIX).removeprefix(QUERY_PREFIX).lower()
    total = [0.0] * DIMENSION
    for word in WORD.findall(body) or [body]:
        for index, value in enumerate(_word_vector(word)):
            total[index] += value
    norm = math.sqrt(sum(value * value for value in total)) or 1.0
    return [value / norm for value in total]


class FakeEmbeddingClient:
    def __init__(
        self,
        *,
        model: str = "nomic-embed-text",
        digest: str = "sha256:fake-digest",
        dimension: int = DIMENSION,
        fail_marker: str | None = None,
        fail_mode: FailMode = "response",
        fail_after_calls: int | None = None,
        unreachable: bool = False,
        model_missing: bool = False,
    ) -> None:
        self.model_missing = model_missing
        self.model = model
        self.digest = digest
        self.dimension = dimension
        self.fail_marker = fail_marker  # a document whose text contains this fails
        self.fail_mode = fail_mode
        self.fail_after_calls = fail_after_calls  # every embed call after this many succeeds fails
        self.unreachable = unreachable
        self.ready_calls = 0
        self.document_calls: list[list[str]] = []

    @property
    def texts_embedded(self) -> int:
        return sum(len(call) for call in self.document_calls)

    def _fail(self) -> None:
        if self.fail_mode == "crash":
            raise SimulatedCrash
        if self.fail_mode == "unavailable":
            raise EmbeddingUnavailableError
        raise EmbeddingResponseError

    async def ensure_ready(self) -> EmbeddingProfile:
        self.ready_calls += 1
        if self.unreachable:
            raise EmbeddingUnavailableError
        if self.model_missing:
            raise ModelMissingError
        return EmbeddingProfile(
            model=self.model,
            model_digest=self.digest,
            dimension=self.dimension,
            doc_prefix=EMBED_DOCUMENT_PREFIX,
            query_prefix=QUERY_PREFIX,
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if self.unreachable:
            raise EmbeddingUnavailableError
        calls_so_far = len(self.document_calls)
        self.document_calls.append(list(texts))
        if self.fail_after_calls is not None and calls_so_far >= self.fail_after_calls:
            self._fail()
        if self.fail_marker is not None and any(self.fail_marker in text for text in texts):
            self._fail()
        return [vector_for(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.unreachable:
            raise EmbeddingUnavailableError
        return vector_for(QUERY_PREFIX + text)

    async def aclose(self) -> None:
        return None
