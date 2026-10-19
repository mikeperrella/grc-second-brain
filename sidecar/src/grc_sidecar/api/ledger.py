"""The ledger routes (ADR-017). Thin: every decision is made in `ledger/service.py`.

All of them sit behind the auth middleware that wraps the whole app, so they need the token like
every other route. There is no URL, token, credential or host field in any request: the ledger
never connects anywhere. Responses are for the authenticated owner; they hold customer names,
questions and answers, and none of it is ever logged.

- `POST /ledger/import`      turn the approved rows of a reviewed draft into promises
- `GET  /ledger/customers`   the customers
- `GET  /ledger/promises`    a page of promises (voided ones only with `include_voided=true`;
                             `page_size` 1 to 100)
- `GET  /ledger/cited-paths` the distinct paths live promises cite, with counts (read-only)
- `POST /ledger/recall`      check live promises against the current vault (a POST: it can append)
- `POST /ledger/blast-radius`  preview what saving or deleting one vault file would do to the
                              promises that cite it (promises only; nothing saved or recorded)
- `POST /ledger/promises/{id}/void`        correct a mistake: a fixed reason, no free text
- `POST /ledger/promises/{id}/acknowledge`  record the recall state the owner saw (it must still
                              be the latest one, else `acknowledge_stale`)
"""

import re
from datetime import date
from typing import Annotated, Self

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grc_sidecar.errors import LedgerRootNotConfiguredError
from grc_sidecar.ledger.service import (
    MAX_PAGE_SIZE,
    PAGE_SIZE,
    BlastItem,
    BlastReport,
    CustomerChoice,
    ImportOutcome,
    LedgerService,
    PromisePage,
    PromiseView,
    RecallItem,
    RecallReport,
)
from grc_sidecar.repositories.base import Customer, PromiseEvent, VoidReason
from grc_sidecar.vault.normalize import MAX_FILE_BYTES

router = APIRouter()

_DRIVE_LETTER = re.compile(r"[A-Za-z]:")
MAX_PATH_CHARS = 1_000  # a bound on the request; promises never cite anything longer

RUN_ID_PATTERN = r"^[0-9a-f]{8,64}$"
ID_PATTERN = r"^[0-9a-f]{8,64}$"
PROMISE_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
MAX_FILE_NAME_CHARS = 260
MAX_NEW_NAME_CHARS = 1_000  # a bound on the request; the ledger's own name rules decide the rest
MAX_PAGE = 100_000
MAX_EVENT_ID = 2**63 - 1  # SQLite's largest integer; an event id can never be larger


def get_ledger(request: Request) -> LedgerService:
    service = getattr(request.app.state, "ledger", None)
    if not isinstance(service, LedgerService):
        raise LedgerRootNotConfiguredError
    return service


Ledger = Annotated[LedgerService, Depends(get_ledger)]


class _Body(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ImportRequest(_Body):
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    file: str = Field(min_length=1, max_length=MAX_FILE_NAME_CHARS)
    existing_customer_id: str | None = Field(default=None, pattern=ID_PATTERN)
    new_customer_name: str | None = Field(default=None, min_length=1, max_length=MAX_NEW_NAME_CHARS)
    confirm_new: bool = False
    sent_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    preview: bool = False  # every check and count, nothing written, no customer made


class OtherValueResponse(_Body):
    text: str
    truncated: bool
    count: int


class ImportResponse(_Body):
    created: int
    created_edited: int
    customer_id: str | None
    customer_created: bool
    skipped: dict[str, int]
    review: dict[str, int]
    other_values: list[OtherValueResponse]
    promise_ids: list[str]
    already_changed_since_draft: int | None = None  # information only; None with no vault folder
    preview: bool = False  # True: nothing was written; `created` is what a real import would make


class CitedPathResponse(_Body):
    source_path: str
    promise_count: int


class CitedPathsResponse(_Body):
    paths: list[CitedPathResponse]


class CustomerResponse(_Body):
    customer_id: str
    display_name: str


class PromiseResponse(_Body):
    promise_id: str
    customer_id: str
    run_id: str
    run_row_index: int
    question_text: str
    answer_text: str
    status: str
    source_path: str
    doc_id: str
    start_line: int
    end_line: int
    quote: str
    edited_after_draft: bool
    sent_on: date
    created_on: date
    voided: bool
    recall_event_id: int | None
    acknowledged_event_id: int | None
    acknowledged_state: str | None
    acknowledgement_current: bool


class RecallRequest(_Body):
    customer_id: str | None = Field(default=None, pattern=ID_PATTERN)


class VoidRequest(_Body):
    reason: VoidReason  # one of a fixed set: free text cannot be sent


class AcknowledgeRequest(_Body):
    """The recall event the owner saw (G35). A whole number from 1: a boolean or text is refused."""

    seen_recall_event_id: int = Field(ge=1, le=MAX_EVENT_ID, strict=True)


class LocationResponse(_Body):
    start_line: int
    end_line: int


class RecallItemResponse(_Body):
    promise_id: str
    customer_id: str
    source_path: str
    start_line: int
    end_line: int
    state: str
    detail: str | None
    locations: list[LocationResponse]
    locations_not_listed: int
    flags: list[str]
    previous_state: str | None
    event_recorded: bool
    recall_event_id: int | None
    acknowledged_event_id: int | None
    acknowledged_state: str | None
    acknowledgement_current: bool
    statement: str
    scope: str


class RecallResponse(_Body):
    checked: int
    events_recorded: int
    counts: dict[str, int]
    items: list[RecallItemResponse]
    statement: str
    scope: str


class BlastRadiusRequest(_Body):
    """Exactly one of `proposed_text` (the whole new file) or `delete_file=true`.

    There is no URL, token, credential or host field: nothing here connects anywhere. A bad
    request is a 422 that names the field and never echoes its value (the text may be sensitive).
    """

    path: str = Field(max_length=MAX_PATH_CHARS)
    # A first bound only: characters are at most bytes, so the byte cap in the service is the real
    # limit and this never refuses text the byte cap would accept.
    proposed_text: str | None = Field(default=None, max_length=MAX_FILE_BYTES)
    delete_file: bool = False
    customer_id: str | None = Field(default=None, pattern=ID_PATTERN)

    @field_validator("path")
    @classmethod
    def _vault_relative_posix_path(cls, value: str) -> str:
        bad = (
            not value
            or value.startswith("/")
            or _DRIVE_LETTER.match(value) is not None
            or "\\" in value
            or "\x00" in value
            or ".." in value.split("/")
        )
        if bad:
            raise ValueError("not a vault-relative POSIX path")
        return value

    @model_validator(mode="after")
    def _exactly_one_of_text_or_delete(self) -> Self:
        if (self.proposed_text is not None) == self.delete_file:
            raise ValueError("send exactly one of proposed_text or delete_file=true")
        return self


class BlastItemResponse(_Body):
    promise_id: str
    customer_id: str
    source_path: str
    start_line: int
    end_line: int
    state_now: str
    state_proposed: str
    would_change: bool
    proposed_detail: str | None
    locations: list[LocationResponse]
    locations_not_listed: int
    flags: list[str]
    flags_now: list[str]
    statement: str
    scope: str
    preview: str


class BlastRadiusResponse(_Body):
    checked: int
    would_change: int
    counts: dict[str, int]
    items: list[BlastItemResponse]
    statement: str
    scope: str
    coverage: str
    preview: str
    note: str | None


class EventResponse(_Body):
    promise_id: str
    event_id: int
    kind: str


class PromisesResponse(_Body):
    page: int
    page_size: int
    total: int
    promises: list[PromiseResponse]


def _import_response(outcome: ImportOutcome) -> ImportResponse:
    return ImportResponse(
        created=outcome.created,
        created_edited=outcome.created_edited,
        customer_id=outcome.customer_id,
        customer_created=outcome.customer_created,
        skipped=outcome.skipped,
        review=outcome.review,
        other_values=[OtherValueResponse(**vars(v)) for v in outcome.other_values],
        promise_ids=list(outcome.promise_ids),
        already_changed_since_draft=outcome.already_changed_since_draft,
        preview=outcome.preview,
    )


def _customer_response(customer: Customer) -> CustomerResponse:
    return CustomerResponse(customer_id=customer.customer_id, display_name=customer.display_name)


def _promise_response(view: PromiseView) -> PromiseResponse:
    p = view.promise
    return PromiseResponse(
        promise_id=p.promise_id,
        customer_id=p.customer_id,
        run_id=p.run_id,
        run_row_index=p.run_row_index,
        question_text=p.question_text,
        answer_text=p.answer_text,
        status=p.status,
        source_path=p.source_path,
        doc_id=p.doc_id,
        start_line=p.start_line,
        end_line=p.end_line,
        quote=p.quote,
        edited_after_draft=p.edited_after_draft,
        sent_on=p.sent_on,
        created_on=p.created_on,
        voided=view.voided,
        recall_event_id=view.ack.recall_event_id,
        acknowledged_event_id=view.ack.acknowledged_event_id,
        acknowledged_state=view.ack.acknowledged_state,
        acknowledgement_current=view.ack.acknowledgement_current,
    )


def _promises_response(found: PromisePage) -> PromisesResponse:
    return PromisesResponse(
        page=found.page,
        page_size=found.page_size,
        total=found.total,
        promises=[_promise_response(view) for view in found.items],
    )


def _recall_item_response(item: RecallItem) -> RecallItemResponse:
    return RecallItemResponse(
        promise_id=item.promise_id,
        customer_id=item.customer_id,
        source_path=item.source_path,
        start_line=item.start_line,
        end_line=item.end_line,
        state=item.state,
        detail=item.detail,
        locations=[LocationResponse(start_line=s, end_line=e) for s, e in item.locations],
        locations_not_listed=item.locations_not_listed,
        flags=list(item.flags),
        previous_state=item.previous_state,
        event_recorded=item.event_recorded,
        recall_event_id=item.ack.recall_event_id,
        acknowledged_event_id=item.ack.acknowledged_event_id,
        acknowledged_state=item.ack.acknowledged_state,
        acknowledgement_current=item.ack.acknowledgement_current,
        statement=item.statement,
        scope=item.scope,
    )


def _recall_response(report: RecallReport) -> RecallResponse:
    return RecallResponse(
        checked=report.checked,
        events_recorded=report.events_recorded,
        counts=report.counts,
        items=[_recall_item_response(item) for item in report.items],
        statement=report.statement,
        scope=report.scope,
    )


def _blast_item_response(item: BlastItem) -> BlastItemResponse:
    return BlastItemResponse(
        promise_id=item.promise_id,
        customer_id=item.customer_id,
        source_path=item.source_path,
        start_line=item.start_line,
        end_line=item.end_line,
        state_now=item.state_now,
        state_proposed=item.state_proposed,
        would_change=item.would_change,
        proposed_detail=item.proposed_detail,
        locations=[LocationResponse(start_line=s, end_line=e) for s, e in item.locations],
        locations_not_listed=item.locations_not_listed,
        flags=list(item.flags),
        flags_now=list(item.flags_now),
        statement=item.statement,
        scope=item.scope,
        preview=item.preview,
    )


def _blast_response(report: BlastReport) -> BlastRadiusResponse:
    return BlastRadiusResponse(
        checked=report.checked,
        would_change=report.would_change,
        counts=report.counts,
        items=[_blast_item_response(item) for item in report.items],
        statement=report.statement,
        scope=report.scope,
        coverage=report.coverage,
        preview=report.preview,
        note=report.note,
    )


def _event_response(event: PromiseEvent) -> EventResponse:
    return EventResponse(promise_id=event.promise_id, event_id=event.event_id, kind=event.kind)


@router.post("/ledger/import")
async def import_reviewed(body: ImportRequest, ledger: Ledger) -> ImportResponse:
    outcome = await ledger.import_reviewed(
        body.run_id,
        body.file,
        CustomerChoice(
            existing_customer_id=body.existing_customer_id,
            new_customer_name=body.new_customer_name,
            confirm_new=body.confirm_new,
        ),
        body.sent_on,
        body.preview,
    )
    return _import_response(outcome)


@router.get("/ledger/customers")
async def customers(ledger: Ledger) -> list[CustomerResponse]:
    return [_customer_response(c) for c in await ledger.customers()]


@router.get("/ledger/cited-paths")
async def cited_paths(ledger: Ledger) -> CitedPathsResponse:
    return CitedPathsResponse(
        paths=[
            CitedPathResponse(source_path=c.source_path, promise_count=c.promise_count)
            for c in await ledger.cited_paths()
        ]
    )


@router.get("/ledger/promises")
async def promises(
    ledger: Ledger,
    customer_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    include_voided: bool = False,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_SIZE,
) -> PromisesResponse:
    return _promises_response(await ledger.promises(customer_id, include_voided, page, page_size))


@router.post("/ledger/recall")
async def recall(ledger: Ledger, body: RecallRequest | None = None) -> RecallResponse:
    customer_id = None if body is None else body.customer_id
    return _recall_response(await ledger.recall(customer_id))


@router.post("/ledger/blast-radius")
async def blast_radius(body: BlastRadiusRequest, ledger: Ledger) -> BlastRadiusResponse:
    return _blast_response(
        await ledger.blast_radius(body.path, body.proposed_text, body.customer_id)
    )


PromiseId = Annotated[str, PathParam(pattern=PROMISE_ID_PATTERN)]


@router.post("/ledger/promises/{promise_id}/void")
async def void_promise(promise_id: PromiseId, body: VoidRequest, ledger: Ledger) -> EventResponse:
    return _event_response(await ledger.void(promise_id, body.reason))


@router.post("/ledger/promises/{promise_id}/acknowledge")
async def acknowledge_promise(
    promise_id: PromiseId, body: AcknowledgeRequest, ledger: Ledger
) -> EventResponse:
    return _event_response(await ledger.acknowledge(promise_id, body.seen_recall_event_id))
