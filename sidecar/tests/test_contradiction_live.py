"""Live check C: the contradiction model step against a real local Ollama (marker `ollama`).

Deselected by default (`-m 'not ollama'` in pyproject) and never in CI. Run by the owner, by hand,
from `sidecar/`, with `ollama serve` running and `nomic-embed-text` and `llama3.1:8b` installed
(nothing here pulls a model). The exact commands, the success output and what to paste back are in
ADR-019, "Live check C". Selection is by `-k chosen_split` so that nothing else runs by accident.

The scoring is per item (see `contradictionlivekit.py`): each item gets its own detector over a
temporary copy of only its notes, and only the item's own pair is read off. The embedding model is
used by the gate's model check only: the detector reads chunks and keywords and never embeds, so
the index here is built with the offline fake embedder. The real template, the real client and the
real gate ask the real model; the template hash is recorded in the results file.

Safety: the results file is created exclusively (an existing file is never overwritten), the
held-out split needs a confirmation variable, and nothing selects, averages or discards a run.
"""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from grc_sidecar.contradictions.verdict import PAIR_TEMPLATE_SHA256, ModelAsker
from grc_sidecar.llm import limits
from grc_sidecar.llm.generation_client import GenerationClient
from grc_sidecar.llm.model_gate import ModelGate
from grc_sidecar.llm.ollama_embeddings import OllamaEmbeddingClient
from grc_sidecar.vault.clock import SystemClock

from .contradictionlivekit import (
    NEVER_CUT_CAP,
    ROW_HEADER,
    Record,
    format_report,
    format_row,
    item_cases,
    known_cases,
    results_path,
    run_cases,
)

pytestmark = pytest.mark.ollama

BASE_URL = "http://127.0.0.1:11434"
EMBEDDING = "nomic-embed-text"
GENERATION = "llama3.1:8b"
SPLIT_VAR = "ALIBI_CONTRADICTION_SPLIT"
CONFIRM_VAR = "ALIBI_CONTRADICTION_HELDOUT_CONFIRM"
CONFIRM_VALUE = "run-the-heldout-split-three-times"
DIR_VAR = "ALIBI_CONTRADICTION_OUT_DIR"
NAME_VAR = "ALIBI_CONTRADICTION_OUT_NAME"


def chosen_split() -> str:
    split = os.environ.get(SPLIT_VAR, "dev")
    if split not in ("dev", "heldout"):
        pytest.fail(f"{SPLIT_VAR} must be dev or heldout, not {split!r}")
    if split == "heldout" and os.environ.get(CONFIRM_VAR) != CONFIRM_VALUE:
        pytest.fail(
            "The held-out split is run 3 times at live check C, by the owner. To run it, set "
            f"{CONFIRM_VAR}={CONFIRM_VALUE}. Not run."
        )
    return split


def chosen_results_file() -> Path:
    try:
        path = results_path(os.environ.get(DIR_VAR, ""), os.environ.get(NAME_VAR, ""))
    except ValueError as error:
        pytest.fail(f"Set {DIR_VAR} (existing folder) and {NAME_VAR} (a name ending .txt): {error}")
    if path.exists():
        pytest.fail(f"{path.name} already exists. A run never overwrites one: use a new name.")
    return path


def test_the_chosen_split_with_the_real_model() -> None:
    split = chosen_split()
    out = chosen_results_file()
    cases = item_cases(split)
    known = known_cases()

    async def scenario() -> tuple[list[Record], list[Record], str]:
        embed = OllamaEmbeddingClient(BASE_URL, EMBEDDING, expected_dimension=768)
        gate = ModelGate(embed, (EMBEDDING, GENERATION), SystemClock())
        generator = GenerationClient(BASE_URL, GENERATION, gate)
        try:
            status = await gate.refresh()
            print(
                f"\nmodel gate: state={status.state} ollama={status.ollama} "
                f"missing={status.models_missing}"
            )
            if status.state != "ready":
                pytest.fail("The model gate is not ready: Ollama or a model is missing. Not run.")
            entry = await generator.read_model_entry()
            header = (
                f"=== CONTRADICTION LIVE CHECK C split={split} items={len(cases)} "
                f"known_cases={len(known)} model={GENERATION} digest={entry.digest} "
                f"template_sha256={PAIR_TEMPLATE_SHA256} cap={NEVER_CUT_CAP} "
                f"num_ctx={limits.NUM_CTX} num_predict={limits.NUM_PREDICT} seed={limits.SEED} "
                f"run_file={out.name}"
            )
            print(header)
            print(ROW_HEADER, flush=True)
            with out.open("x", encoding="utf-8", newline="\n") as handle:  # exclusive create
                handle.write(header + "\n" + ROW_HEADER + "\n")

                def show(record: Record) -> None:
                    line = format_row(record)
                    print(line, flush=True)
                    handle.write(line + "\n")
                    handle.flush()

                def make_asker() -> ModelAsker:
                    return ModelAsker(generator, model_name=GENERATION, gate=gate)

                with tempfile.TemporaryDirectory(prefix="grc-live-contradiction-") as tmp:
                    work = Path(tmp)
                    records = await run_cases(cases, make_asker, work / "split", on_record=show)
                    print("\nKNOWN CASES (scored apart)")
                    known_records = await run_cases(
                        known, make_asker, work / "known", on_record=show
                    )
                report = format_report(records, known_records, header=header)
                handle.write("\n" + report)
            return records, known_records, report
        finally:
            await generator.aclose()
            await embed.aclose()

    _, _, report = asyncio.run(scenario())
    print("\n" + report)
    print(f"results written (exclusive create): {out}")
