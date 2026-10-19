"""Typed domain exceptions for the sidecar.

Every message is a fixed, generic string. Messages never echo a URL, path, or any
other caller-supplied value, because those can be attacker-controlled or sensitive
and error text ends up in logs. Mapping these errors to HTTP responses happens in
one place, added with the first router.
"""

from typing import Literal


class GrcError(Exception):
    """Base class for all sidecar domain errors."""

    default_message = "GRC sidecar error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class ConfigurationError(GrcError):
    """Settings or required local resources are invalid."""

    default_message = "Invalid configuration"


class NonLoopbackURLError(GrcError, ValueError):
    """A URL is not a plain http URL pointing at a loopback IP address."""

    default_message = "URL must be http and point at a loopback IP address"


class MissingAuthTokenError(GrcError):
    """The auth token environment variable is absent or empty."""

    default_message = "Authentication token is missing"


class InvalidAuthTokenError(GrcError):
    """The auth token is not exactly 64 lowercase hex characters."""

    default_message = "Authentication token is invalid"


class PathNotAllowedError(GrcError):
    """A path is outside the approved root or is not acceptable input."""

    default_message = "Path is not allowed"


class PathNotFoundError(GrcError):
    """A path is inside the approved root but does not exist."""

    default_message = "Path was not found"


class ProcessGuardError(GrcError):
    """The process-creation guard could not be installed or is not working."""

    default_message = "Process guard could not be installed"


class SpawnBlockedError(GrcError, PermissionError):
    """Starting a process was refused by the process guard.

    It is a PermissionError (an OSError) on purpose: the standard library already treats a
    failed `ver` lookup on Windows as "unknown" and carries on, but only for OSError.
    """

    default_message = "Starting a process is not allowed"


class ExtensionLoadBlockedError(GrcError, PermissionError):
    """Loading a native SQLite extension was refused by the process guard.

    Loading a native library runs arbitrary code inside the sidecar, so only the one vendored
    sqlite-vec library is allowed (ADR-007 amendment). A PermissionError, like SpawnBlockedError.
    """

    default_message = "Loading this extension is not allowed"


class StdoutSealError(GrcError):
    """Standard output could not be redirected away from the host's pipe."""

    default_message = "Standard output could not be sealed"


class VaultError(GrcError):
    """A vault file could not be indexed. Carries *where* and *why* as attributes.

    `str(exc)` stays a fixed string (see the module docstring). The file, field and line are
    structured attributes so the index report can show them without the message ever echoing
    document content. `source_path` is always vault-relative, never absolute.
    """

    default_message = "Vault file could not be indexed"
    code = "vault_error"

    def __init__(
        self,
        *,
        source_path: str | None = None,
        field: str | None = None,
        line: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__()
        self.source_path = source_path
        self.field = field
        self.line = line
        if code is not None:
            self.code = code


class MissingFrontmatterError(VaultError):
    """The file does not start with a `---` frontmatter block."""

    default_message = "Frontmatter block is missing"
    code = "missing_frontmatter"


class FrontmatterSyntaxError(VaultError):
    """The frontmatter block is not valid, safe YAML."""

    default_message = "Frontmatter is not valid"
    code = "frontmatter_syntax"


class FrontmatterFieldError(VaultError):
    """A frontmatter field is missing or has an invalid value."""

    default_message = "Frontmatter field is missing or invalid"
    code = "frontmatter_field"


class InvalidEncodingError(VaultError):
    """The file is not valid UTF-8 text (or contains a NUL byte)."""

    default_message = "File is not valid UTF-8 text"
    code = "invalid_encoding"


class FileTooLargeError(VaultError):
    """The file is larger than the indexer accepts."""

    default_message = "File is too large to index"
    code = "file_too_large"


class DuplicateDocIdError(VaultError):
    """Two or more files declare the same doc_id."""

    default_message = "doc_id is used by more than one file"
    code = "duplicate_doc_id"


class UnsafePathError(VaultError):
    """A vault file is a link, escapes the vault, or otherwise fails the path guard."""

    default_message = "Path is not safe to index"
    code = "unsafe_path"


class AuthControlsError(GrcError):
    """The packaged list of authentication controls is missing or malformed."""

    default_message = "Authentication controls list is invalid"
    code = "auth_controls_invalid"


class ControlMapTooLargeError(GrcError):
    """The vault has more documents than the control map accepts (a partial map would mislead)."""

    default_message = "Vault has too many documents for the control map"
    code = "control_map_too_large"


ExportRefusal = Literal[
    # caps (ADR-016 decision 9)
    "file_too_large",
    "too_many_rows",
    "field_too_long",
    # content refused for safety
    "encoding_not_allowed",
    "nul_character",
    # not an export ALIBI can read
    "missing_column",
    "duplicate_column",
    "unreadable",
]


class ExportRefusedError(GrcError):
    """An IT export was refused before or while it was read (ADR-016).

    `code` is one of a fixed set of words, never text from the file. `column` is set only for the
    two header refusals and is always one of ALIBI's own four required column names, never a value
    taken from the file, so the message is safe to show and to log.
    """

    default_message = "The export was refused"

    def __init__(self, code: ExportRefusal, column: str | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: ExportRefusal = code
        self.column = column


CheckRefusal = Literal[
    "exported_at_in_future",  # the owner's export date is after today
    "claim_not_verbatim",  # the claim is not exactly a candidate of the current vault
    "no_users_in_scope",  # no row has an active status, so "no exceptions" would mean nothing
]


class CheckRefusedError(GrcError):
    """A reality check was refused before it could give a finding (ADR-016).

    `code` is one of a fixed set of words, never text from the request, the vault or the export.
    """

    default_message = "The reality check was refused"

    def __init__(self, code: CheckRefusal) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: CheckRefusal = code


class ImportRootNotConfiguredError(GrcError):
    """The reality routes were asked for work but the import folder is not configured.

    The routes need `GRC_IMPORT_DIR` and `GRC_VAULT_DIR`; with either unset they refuse instead of
    guessing a folder (ADR-016).
    """

    default_message = "The import folder is not configured"


class LedgerRootNotConfiguredError(GrcError):
    """The ledger routes were asked for work but the ledger folder is not configured.

    The ledger needs `GRC_LEDGER_DIR` and has no default: with it unset the routes refuse instead
    of guessing a folder, and a questionnaire run still works (ADR-017).
    """

    default_message = "The ledger folder is not configured"


class LedgerStoreError(GrcError):
    """The ledger (SQLite file) could not be opened, read or written."""

    default_message = "Ledger store error"


class LedgerSchemaMismatchError(LedgerStoreError):
    """The ledger file is from another schema version, or is not a ledger at all.

    The ledger cannot be rebuilt, so it fails closed: nothing is migrated, repaired or deleted.
    """

    default_message = "The ledger file has an unsupported schema"


LedgerRefusal = Literal[
    "customer_name_invalid",
    "customer_exists",
    "customer_unknown",
    "run_exists",
    "run_unknown",
    "promise_exists",
    "promise_unknown",
    "event_invalid",
]


class LedgerRefusedError(GrcError):
    """A ledger write was refused (ADR-017). `code` is a fixed word, never caller text.

    `customer_id` is set only for `customer_exists`: the id of the customer the name collided
    with, so the caller can use that customer instead of making a second one.
    """

    default_message = "The ledger write was refused"

    def __init__(self, code: LedgerRefusal, customer_id: str | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: LedgerRefusal = code
        self.customer_id = customer_id


ImportRefusal = Literal[
    "run_record_missing",
    "headers_changed",
    "question_mismatch",
    "customer_choice_invalid",
    "new_customer_not_confirmed",
    "sent_on_invalid",
    "sent_on_in_future",
    "sent_before_drafted",
]


class ImportRefusedError(GrcError):
    """A reviewed-workbook import was refused as a whole (ADR-017 decision 5).

    `code` is a fixed word and `counts` holds only integers (for `question_mismatch`: how many rows
    differ and how many rows each side has), never text from the workbook, the request or the vault.
    """

    default_message = "The import was refused"

    def __init__(self, code: ImportRefusal, counts: dict[str, int] | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: ImportRefusal = code
        self.counts: dict[str, int] = {} if counts is None else dict(counts)


class DraftRefusedError(GrcError):
    """The draft-rows route refused the whole draft (ADR-021 decision 5).

    `code` is a fixed word: `run_record_missing`, `headers_changed`, `question_mismatch`, or the
    loader's or the question mapping's own code (never text from a file). `counts`
    holds only integers (for `question_mismatch`), never text from the workbook or the request. A
    refusal returns no rows.
    """

    default_message = "The draft was refused"

    def __init__(self, code: str, counts: dict[str, int] | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: str = code
        self.counts: dict[str, int] = {} if counts is None else dict(counts)


RecallRefusal = Literal[
    "too_many_promises", "promise_voided", "acknowledge_stale", "too_many_paths"
]


class RecallRefusedError(GrcError):
    """A recall, void or acknowledge was refused (ADR-017 decisions 13, 17 and 20).

    `code` is a fixed word and `counts` holds only integers (for `too_many_promises`: how many
    promises there are and the cap), never text from the ledger or the vault.
    """

    default_message = "The request was refused"

    def __init__(self, code: RecallRefusal, counts: dict[str, int] | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: RecallRefusal = code
        self.counts: dict[str, int] = {} if counts is None else dict(counts)


GraphRefusal = Literal["graph_too_large"]


class GraphRefusedError(GrcError):
    """The vault graph was refused as a whole (ADR-023 decision 3). No partial graph is returned.

    `code` is a fixed word and `counts` holds only integers (how many nodes and edges there are
    and the two caps), never a path or any text from the vault.
    """

    default_message = "The vault graph was refused"

    def __init__(self, code: GraphRefusal, counts: dict[str, int] | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: GraphRefusal = code
        self.counts: dict[str, int] = {} if counts is None else dict(counts)


BlastRadiusRefusal = Literal[
    "too_many_promises",
    "proposed_text_too_large",
    "proposed_text_has_nul",
    "proposed_text_not_utf8",
]


class BlastRadiusRefusedError(GrcError):
    """A blast-radius preview was refused (ADR-018). Nothing is checked after a refusal.

    `code` is a fixed word and `counts` holds only integers (for `too_many_promises`: how many
    promises and the cap; for `proposed_text_too_large`: the byte count and the cap), never any
    part of the proposed text.
    """

    default_message = "The preview was refused"

    def __init__(self, code: BlastRadiusRefusal, counts: dict[str, int] | None = None) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: BlastRadiusRefusal = code
        self.counts: dict[str, int] = {} if counts is None else dict(counts)


class VaultRootNotConfiguredError(GrcError):
    """Recall was asked to read the vault but the vault folder is not configured.

    Recall reads the current vault files from `GRC_VAULT_DIR`; with it unset recall refuses and
    names that folder, instead of the ledger's own `ledger_root_not_configured`.
    """

    default_message = "The vault folder is not configured"


class ExportRootNotConfiguredError(GrcError):
    """The import was asked to read a reviewed workbook but the export folder is not configured.

    The import reads the reviewed copy of a draft from `GRC_EXPORT_DIR`; with it unset the import
    refuses and names that folder, instead of the ledger's own `ledger_root_not_configured`.
    """

    default_message = "The export folder is not configured"


class IndexStoreError(GrcError):
    """The vector index (SQLite file) could not be opened, read or written."""

    default_message = "Index store error"


class IndexRebuildRequiredError(IndexStoreError):
    """The index file was made by another schema version, or is not an index at all.

    The index is disposable derived state: the answer is to rebuild it, never to migrate it.
    """

    default_message = "Index must be rebuilt"


class IndexProfileMismatchError(IndexStoreError):
    """The index was built with a different embedding profile than the one requested.

    `fields` lists the profile field names that differ, never their values. Re-embedding every
    document is the user's decision, so nothing is changed.
    """

    default_message = "Index was built with a different embedding profile"

    def __init__(self, fields: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.fields = fields


class VectorDimensionError(IndexStoreError):
    """A vector does not have the dimension the index was created with."""

    default_message = "Vector dimension does not match the index"


class ExtensionUnavailableError(IndexStoreError):
    """The sqlite-vec extension could not be loaded into the SQLite connection."""

    default_message = "Vector extension could not be loaded"


class ModelMissingError(GrcError):
    """A model the app needs is not installed in Ollama.

    The app never downloads models (CLAUDE.md 3.7): a human installs it with Ollama.
    """

    default_message = (
        "Required model is not installed in Ollama; install it yourself (the app never downloads)"
    )


class IndexNotBuiltError(GrcError):
    """There is no search index to retrieve from.

    Retrieval never builds one: indexing needs the embedding model and can take minutes, so it is
    started deliberately (the UI, M6), not as a side effect of a question.
    """

    default_message = "No search index has been built yet"


class RetrievalUnavailableError(GrcError):
    """Retrieval was refused because it could not be certified fresh.

    Raised when the vault cannot be checked against the index (the vault folder is unusable, or
    the index could not be updated). Nothing is returned: stale text must never be served by
    default (CLAUDE.md 8.1).
    """

    default_message = "Retrieval is not available"


class ModelsUnavailableError(GrcError):
    """A model-dependent operation was refused because a required model is not confirmed present.

    Raised by the model gate (CLAUDE.md 3.7, ADR-011), before any request to a model is made.
    `missing` lists model names only (our own configuration, never response text). Empty means
    the gate itself is not set up.
    """

    default_message = "A required model is not available"

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.missing = missing


class EmbeddingUnavailableError(GrcError):
    """Ollama could not be reached, timed out, or answered with a server-side failure.

    A connection-level problem: it aborts an indexing run rather than failing one file.
    """

    default_message = "Embedding service is unavailable"


class EmbeddingResponseError(GrcError):
    """Ollama answered, but not with usable embeddings (wrong shape, NaN, input too long).

    Affects the text that was sent, so an indexing run records it for that file and goes on.
    The message never contains the response body or the input text.
    """

    default_message = "Embedding response was not usable"


class EmbeddingDimensionError(GrcError):
    """The model's vectors do not have the dimension the settings expect."""

    default_message = "Embedding dimension is not the expected one"


class EndpointNotAllowedError(GrcError):
    """A request to an Ollama endpoint a client may not call was refused.

    The embedding client may only call `GET /api/tags` and `POST /api/embed`; the generation client
    only the model listing and the chat endpoint (ADR-012). This is the runtime control behind
    "the app never downloads models".
    """

    default_message = "This Ollama endpoint is not allowed"


class EmptyQuestionError(GrcError):
    """A questionnaire cell has no question in it (empty or only whitespace)."""

    default_message = "The question is empty"


class QuestionTooLongError(GrcError):
    """A questionnaire cell is longer than a question may be.

    The cell is untrusted data, so it is capped, never silently cut: a cut question could ask
    something different from what the cell says (ADR-013, `question_too_long`).
    """

    default_message = "The question is too long"


class GenerationUnavailableError(GrcError):
    """Ollama could not be reached for a generation call, timed out, or failed on its side.

    A connection-level problem: it is not about the question that was asked, so a run that meets
    it stops asking and blanks the rest (ADR-013, "Ollama fails partway").
    """

    default_message = "Generation service is unavailable"


class GenerationResponseError(GrcError):
    """Ollama answered a generation call, but not with a usable reply (ADR-012).

    Wrong shape, not JSON, a stop reason that is not "stop", or an HTTP status the client does
    not expect. The message never contains the response body or any text that was sent.
    """

    default_message = "Generation response was not usable"


class GenerationRejectedError(GrcError):
    """Ollama refused the generation request itself (HTTP 400).

    Requests are built by our own code, so the usual cause is a prompt longer than the context
    window with context shifting disabled (ADR-012, row 4). The body is never read.
    """

    default_message = "Ollama rejected the generation request"


class PromptTooLargeError(GrcError):
    """The assembled prompt is larger than the generation limits allow.

    Checked before anything is sent, so an oversize prompt never reaches the model.
    """

    default_message = "Prompt is larger than the generation limits"


class ContextWindowReachedError(GrcError):
    """The prompt token count Ollama reported reaches the context window, so text may be cut.

    A secondary check (ADR-012, decision 3): the primary control is the request's `shift: false`.
    """

    default_message = "Prompt reached the context window"


class OutputTruncatedError(GrcError):
    """The model stopped at the output limit, so its JSON may be incomplete."""

    default_message = "Generation output was cut off"


class NonLocalModelError(GrcError):
    """A generation reply says it came from a remote host.

    A tripwire, not a control: by the time a reply arrives the prompt has already left the machine.
    The controls are the gate's refusal of cloud names and remote listings, before any request.
    """

    default_message = "The model answered from a non-local host"


WorkbookRefusal = Literal[
    # size caps (ADR-014)
    "file_too_large",
    "uncompressed_too_large",
    "too_many_parts",
    "ratio_too_high",
    # not a workbook ALIBI can read
    "not_a_zip",
    "legacy_or_encrypted",
    "unsupported_type",
    "no_workbook_part",
    "unreadable",
    # content refused for safety
    "doctype",
    "entity",
    "utf32",
    "encoding_not_allowed",
    "duplicate_part",
]

QuestionMappingRefusal = Literal[
    "no_mapping",
    "sheet_not_found",
    "bad_position",
    "sheet_too_large",
    "too_many_questions",
    "already_has_answer_columns",
]


class WorkbookRefusedError(GrcError):
    """An input workbook was refused before or while it was read (ADR-014).

    `code` is one of a fixed set of words, never text taken from the file, so the message is safe
    to show and to log. The file is untrusted: a refusal says which rule fired, not what the file
    contains.
    """

    default_message = "The workbook was refused"

    def __init__(self, code: WorkbookRefusal) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: WorkbookRefusal = code


WorkbookWriteRefusal = Literal[
    "answers_incomplete",  # a question has no result to write
    "bad_output_name",  # the source file name or run id cannot make a safe output name
    "target_not_empty",  # an output cell is not empty or lies in a merged range
    "input_changed",  # the input file differs from the bytes that were read and answered
    "verify_failed",  # the saved workbook, read back from memory, differs from what was intended
    "output_exists",  # exclusive create failed: a file by that name is already there
    "write_failed",  # the file could not be written
    "readback_mismatch",  # the file on disk does not hash like what was written (it is removed)
]


class WorkbookWriteError(GrcError):
    """The answers file could not be written, and nothing was left behind (ADR-014).

    `code` is a fixed word, never a path or text from the workbook. The input file is never
    touched, and a file that was created and then found wrong is removed before this is raised.
    """

    default_message = "The answers file was not written"

    def __init__(self, code: WorkbookWriteRefusal) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: WorkbookWriteRefusal = code


class QuestionnaireNotConfiguredError(GrcError):
    """The questionnaire routes were asked for a run but the folders are not configured.

    A questionnaire run needs the vault, the questionnaire folder and the export folder. Until the
    folder pickers exist (M6) they come from `GRC_VAULT_DIR`, `GRC_QUESTIONNAIRE_DIR` and
    `GRC_EXPORT_DIR`; with any of them unset the routes refuse instead of guessing a folder.
    """

    default_message = "The questionnaire folders are not configured"


class RunInProgressError(GrcError):
    """A questionnaire run or a contradiction run is already going. One heavy job at a time
    (ADR-014, ADR-019 decision 11)."""

    default_message = "A questionnaire run is already in progress"


class ContradictionsNotConfiguredError(GrcError):
    """The contradiction route was asked for a run but the vault folder is not configured.

    A contradiction run needs only the vault (`GRC_VAULT_DIR`) and its index; with it unset the
    route refuses instead of guessing a folder.
    """

    default_message = "The vault folder is not configured"


class RunNotFoundError(GrcError):
    """No run has this id (or it is older than the few kept in memory)."""

    default_message = "No such questionnaire run"


class VaultChangedError(GrcError):
    """Vault files changed after they were indexed, so a run refuses to start (ADR-014).

    Answers must not be drafted from an index that is known to be behind the vault. The remedy is
    to re-index. The files are not named: the caller is a UI that already knows its vault.
    """

    default_message = "The vault changed after it was indexed; index it again before running"


class QuestionMappingError(GrcError):
    """The chosen question columns do not fit the workbook, or the workbook is too big to map.

    `code` is a fixed word, as for `WorkbookRefusedError`.
    """

    default_message = "The question mapping was refused"

    def __init__(self, code: QuestionMappingRefusal) -> None:
        super().__init__(f"{self.default_message}: {code}")
        self.code: QuestionMappingRefusal = code


class PackIntegrityError(GrcError):
    """A framework pack does not match its manifest, or its folder holds something unexpected.

    Packs fail closed: nothing from any pack is indexed. `pack_id` is the directory name (our own
    content, never user text); the message never contains file contents.
    """

    default_message = "Framework pack failed its integrity check"

    def __init__(self, pack_id: str = "") -> None:
        super().__init__()
        self.pack_id = pack_id


class PackMetadataError(GrcError):
    """A framework pack's pack.json is missing fields or has invalid values.

    `fields` lists the offending field names only, never their values.
    """

    default_message = "Pack metadata is invalid"

    def __init__(self, fields: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.fields = fields
