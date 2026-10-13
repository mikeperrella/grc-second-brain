"""The reality-check runtime (ADR-016), separate from the questionnaire runtime.

The questionnaire runtime needs three folders and the models; the reality check needs the import
folder (and, from chunk 1e, the vault) and no model. With either folder unset there is no runtime
and the routes refuse with a typed error instead of guessing a folder.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from grc_sidecar.config import Settings
from grc_sidecar.errors import ConfigurationError, ImportRootNotConfiguredError
from grc_sidecar.reality.export_reader import DEFAULT_CAPS, Caps
from grc_sidecar.reality.inspect import ExportInspection, inspect_export


@dataclass(frozen=True)
class RealityRuntime:
    import_root: Path
    caps: Caps = DEFAULT_CAPS

    async def inspect(self, file: str) -> ExportInspection:
        """Inspect an export, off the event loop so `/health` keeps answering."""
        unusable = False
        result: ExportInspection | None = None
        try:
            result = await asyncio.to_thread(inspect_export, file, self.import_root, self.caps)
        except ConfigurationError:
            unusable = True  # the import folder itself is missing or not a directory
        if unusable or result is None:
            raise ImportRootNotConfiguredError
        return result


def build_reality_runtime(settings: Settings) -> RealityRuntime | None:
    """The runtime for these settings, or None when the import or vault folder is not set."""
    if settings.import_dir is None or settings.vault_dir is None:
        return None
    return RealityRuntime(settings.import_dir)
