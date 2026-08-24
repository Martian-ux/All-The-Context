"""Small Packet G composition helper over the controlled reference host.

This is evidence code, not a product runtime. It compiles only through
``ControlledReferenceHostV0`` and injected Core Retrieval V3. It does not own
canonical records, persist checkpoints, or treat lifecycle envelopes as current
memory.
"""

from __future__ import annotations

from collections.abc import Sequence

from .client_runtime import ContextDeliveryReceipt, GenerationReceipt, UnsupportedHookReport
from .experimental_reference_host import (
    ControlledReferenceHostV0,
    CoreContextCompiler,
    MissingCorePrincipal,
    ReferenceHostError,
)
from .models import BootstrapRequest, BootstrapResponse
from .retrieval import RetrievalEngine
from .security import ClientPrincipal


def core_retrieval_compiler(retrieval: RetrievalEngine) -> CoreContextCompiler:
    """Return the existing Retrieval V3 bootstrap path as the host compiler."""

    def compile_context(
        request: BootstrapRequest,
        principal: ClientPrincipal | None = None,
    ) -> BootstrapResponse:
        if not isinstance(principal, ClientPrincipal):
            raise MissingCorePrincipal()
        return retrieval.bootstrap(request, principal)

    return compile_context


def compile_authorized_pack(
    host: ControlledReferenceHostV0,
    compiler: CoreContextCompiler,
    principal: ClientPrincipal,
    *,
    generation_id: str,
    requested_scopes: Sequence[str] = (),
    budget_chars: int = 4_000,
    conversation_id: str | None = None,
    task_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    query: str = "",
) -> tuple[BootstrapResponse, ContextDeliveryReceipt, GenerationReceipt]:
    """Compile once through the controlled host; envelopes are not Core truth."""

    result = host.compile_before_generation(
        compiler,
        generation_id=generation_id,
        requested_scopes=requested_scopes,
        budget_chars=budget_chars,
        conversation_id=conversation_id,
        task_id=task_id,
        workspace_id=workspace_id,
        project_id=project_id,
        query=query,
        principal=principal,
    )
    if isinstance(result, UnsupportedHookReport):
        raise ReferenceHostError("authorized L1+ compilation was not accepted")
    return result


def pack_contents(response: BootstrapResponse) -> tuple[str, ...]:
    return tuple(item.content for item in response.items)


__all__ = [
    "compile_authorized_pack",
    "core_retrieval_compiler",
    "pack_contents",
]
