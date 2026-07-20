from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from odoo_sdk.records.recordset import OdooRecordset


class RpcClient(Protocol):
    """Structural contract for the RPC client the command layer depends on.

    :class:`~odoo_sdk.commands.command.Command` and
    :class:`~odoo_sdk.commands.command_registry.Registry` are typed against this
    Protocol instead of the concrete
    :class:`~odoo_sdk.client.client.OdooClient` so any object exposing the same
    members can drive a command. The members are exactly those the command
    bodies and their utilities use:

    * ``uid`` — the authenticated Odoo user id.
    * ``execute(model, method, ...)`` — a raw model-method call.
    * ``__getitem__(model_name)`` — a model-bound recordset.

    :class:`OdooClient` satisfies this Protocol structurally, with no change.
    """

    @property
    def uid(self) -> int: ...

    def execute(
        self, model: str, method: str, *args: Any, **kwargs: Any
    ) -> Any: ...

    def __getitem__(self, model_name: str) -> "OdooRecordset": ...
