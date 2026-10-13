"""The reality-check runtime (ADR-016), separate from the questionnaire runtime.

The questionnaire runtime needs three folders and the models; the reality check needs the import
folder and the vault (for the claim) and no model. With either folder unset there is no runtime
and the routes refuse with a typed error instead of guessing a folder.
"""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from grc_sidecar.config import Settings
from grc_sidecar.errors import ConfigurationError, ImportRootNotConfiguredError
from grc_sidecar.reality.check import CheckRequest, CheckResult, run_check
from grc_sidecar.reality.claims import ClaimReport, find_claims
from grc_sidecar.reality.export_reader import DEFAULT_CAPS, Caps
from grc_sidecar.reality.inspect import ExportInspection, inspect_export
from grc_sidecar.vault.clock import Clock, SystemClock


@dataclass(frozen=True)
class RealityRuntime:
    import_root: Path
    caps: Caps = DEFAULT_CAPS
    vault_root: Path | None = None  # needed by `claims` and `check`, not by `inspect`
    clock: Clock = field(default_factory=SystemClock)

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

    async def claims(self) -> ClaimReport:
        """List the candidate claims in the vault, off the event loop."""
        vault = self._vault()
        unusable = False
        result: ClaimReport | None = None
        try:
            result = await asyncio.to_thread(find_claims, vault)
        except ConfigurationError:
            unusable = True  # the vault folder itself is missing or not a directory
        if unusable or result is None:
            raise ImportRootNotConfiguredError
        return result

    async def check(self, request: CheckRequest) -> CheckResult:
        """Verify the chosen claim and count the export against it, off the event loop."""
        vault = self._vault()
        unusable = False
        result: CheckResult | None = None
        try:
            result = await asyncio.to_thread(
                run_check, request, self.import_root, vault, self.clock, self.caps
            )
        except ConfigurationError:
            unusable = True  # the import or vault folder itself is unusable
        if unusable or result is None:
            raise ImportRootNotConfiguredError
        return result

    def _vault(self) -> Path:
        if self.vault_root is None:
            raise ImportRootNotConfiguredError
        return self.vault_root


def build_reality_runtime(settings: Settings) -> RealityRuntime | None:
    """The runtime for these settings, or None when the import or vault folder is not set."""
    if settings.import_dir is None or settings.vault_dir is None:
        return None
    return RealityRuntime(settings.import_dir, vault_root=settings.vault_dir)
