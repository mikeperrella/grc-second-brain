"""The ledger routes (ADR-017). Thin: every decision is made in `ledger/service.py`.

All of them sit behind the auth middleware that wraps the whole app, so they need the token like
every other route. There is no URL, token, credential or host field in any request: the ledger
never connects anywhere. Responses are for the authenticated owner; they hold customer names,
questions and answers, and none of it is ever logged.

- `POST /ledger/import`      turn the approved rows of a reviewed draft into promises
- `GET  /ledger/customers`   the customers
- `GET  /ledger/promises`    a page of promises (voided ones only with `include_voided=true`)
- `POST /ledger/recall`      check live promises against the current vault (a POST: it can append)
- `POST /ledger/promises/{id}/void`         correct a mistake: a fixed reason, no free text
- `POST /ledger/promises/{id}/acknowledge`  record that the owner saw a flag
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.errors import LedgerRootNotConfiguredError
from grc_sidecar.ledger.service import (
    CustomerChoice,
    ImportOutcome,
    LedgerService,
    PromisePage,
    PromiseView,
    RecallItem,
    RecallReport,
)
from grc_sidecar.repositories.base import Customer, PromiseEvent, VoidReason

router = APIRouter()

RUN_ID_PATTERN = r"^[0-9a-f]{8,64}$"
ID_PATTERN = r"^[0-9a-f]{8,64}$"
PROMISE_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
MAX_FILE_NAME_CHARS = 260
MAX_NEW_NAME_CHARS = 1_000  # a bound on the request; the ledger's own name rules decide the rest
MAX_PAGE = 100_000


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


class RecallRequest(_Body):
    customer_id: str | None = Field(default=None, pattern=ID_PATTERN)


class VoidRequest(_Body):
    reason: VoidReason  # one of a fixed set: free text cannot be sent


class EmptyRequest(_Body):
    """A body with nothing in it, so an unknown field is refused like everywhere else."""


class LocationResponse(_Body):
    start_line: int
    end_line: int


class RecallItemResponse(_Body):
    promise_id: str
    state: str
    detail: str | None
    locations: list[LocationResponse]
    flags: list[str]
    previous_state: str | None
    event_recorded: bool
    statement: str
    scope: str


class RecallResponse(_Body):
    checked: int
    events_recorded: int
    counts: dict[str, int]
    items: list[RecallItemResponse]
    statement: str
    scope: str


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
        state=item.state,
        detail=item.detail,
        locations=[LocationResponse(start_line=s, end_line=e) for s, e in item.locations],
        flags=list(item.flags),
        previous_state=item.previous_state,
        event_recorded=item.event_recorded,
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
    )
    return _import_response(outcome)


@router.get("/ledger/customers")
async def customers(ledger: Ledger) -> list[CustomerResponse]:
    return [_customer_response(c) for c in await ledger.customers()]


@router.get("/ledger/promises")
async def promises(
    ledger: Ledger,
    customer_id: Annotated[str | None, Query(pattern=ID_PATTERN)] = None,
    include_voided: bool = False,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
) -> PromisesResponse:
    return _promises_response(await ledger.promises(customer_id, include_voided, page))


@router.post("/ledger/recall")
async def recall(ledger: Ledger, body: RecallRequest | None = None) -> RecallResponse:
    customer_id = None if body is None else body.customer_id
    return _recall_response(await ledger.recall(customer_id))


PromiseId = Annotated[str, PathParam(pattern=PROMISE_ID_PATTERN)]


@router.post("/ledger/promises/{promise_id}/void")
async def void_promise(promise_id: PromiseId, body: VoidRequest, ledger: Ledger) -> EventResponse:
    return _event_response(await ledger.void(promise_id, body.reason))


@router.post("/ledger/promises/{promise_id}/acknowledge")
async def acknowledge_promise(
    promise_id: PromiseId, ledger: Ledger, body: EmptyRequest | None = None
) -> EventResponse:
    return _event_response(await ledger.acknowledge(promise_id))
