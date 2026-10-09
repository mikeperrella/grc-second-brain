"""The embedding client protocol (what the indexer needs from "something that makes vectors").

The indexer depends on this protocol, not on Ollama, so tests use a deterministic fake
(`tests/fakes.py`) and no network or model is needed in CI.
"""

from collections.abc import Sequence
from typing import Final, Protocol

from grc_sidecar.repositories.base import EmbeddingProfile

# Documents are prefixed by the chunker (`embedding_input`), because the length cap has to count
# the prefix. Queries are prefixed by the client. Both are recorded in the index profile.
QUERY_PREFIX: Final = "search_query: "


class EmbeddingClient(Protocol):
    async def ensure_ready(self) -> EmbeddingProfile:
        """Check the model is installed and measure its vector size; return the index profile.

        Raises ModelMissingError, EmbeddingDimensionError or EmbeddingUnavailableError, always
        before anything has been written anywhere.
        """

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for texts that already carry the document prefix, in the same order."""

    async def embed_query(self, text: str) -> list[float]:
        """The vector for a search query (the client adds the query prefix)."""

    async def aclose(self) -> None: ...
