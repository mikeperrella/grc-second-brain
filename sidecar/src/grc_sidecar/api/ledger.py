"""The ledger routes (ADR-017). Thin: every decision is made in `ledger/service.py`.

All of them sit behind the auth middleware that wraps the whole app, so they need the token like
every other route. There is no URL, token, credential or host field in any request: the ledger
never connects anywhere. Responses are for the authenticated owner; they hold customer names,
questions and answers, and none of it is ever logged.

- `POST /ledger/import`      turn the approved rows of a reviewed draft into promises
- `GET  /ledger/customers`   the customers
- `GET  /ledger/promises`    a page of promises (voided ones only with `include_voided=true`)
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from grc_sidecar.errors import LedgerRootNotConfiguredError
from grc_sidecar.ledger.service import (
    CustomerChoice,
    ImportOutcome,
    LedgerService,
    PromisePage,
    PromiseView,
)
from grc_sidecar.repositories.base import Customer

router = APIRouter()

RUN_ID_PATTERN = r"^[0-9a-f]{8,64}$"
ID_PATTERN = r"^[0-9a-f]{8,64}$"
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
