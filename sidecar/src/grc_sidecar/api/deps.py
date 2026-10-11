"""FastAPI dependencies: how a router reaches the objects the app was built with."""

from fastapi import Request

from grc_sidecar.errors import ModelsUnavailableError
from grc_sidecar.llm.model_gate import ModelGate


def get_model_gate(request: Request) -> ModelGate:
    gate = getattr(request.app.state, "gate", None)
    if not isinstance(gate, ModelGate):
        # An app built without settings has no gate. Refuse like a missing model: nothing here
        # may proceed as if the models had been checked.
        raise ModelsUnavailableError
    return gate
