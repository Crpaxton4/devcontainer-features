from typing import Any, Dict, List, Optional

from odoo_sdk.state.config import ModelIdsNotWritableError

from ..command import Command
from ._registration import builtin_command


@builtin_command
class GetModelsCommand(Command):
    """List the available Odoo models, optionally recording ids into the config."""

    _name = "get_models"
    _description = (
        "Get a list of all models with their technical and display names. This "
        "is the one command that reads the administrative ir.model table, which "
        "is why it is gated. Pass 'persist' — a list of model names, e.g. "
        '\'{"persist": ["project.task"]}\' — to also write each resolved id into '
        "the [model_ids] section of the Odoo SDK config file, which is where "
        "schedule_activity reads it from; that turns a hand-edited config entry "
        "into a command anyone can run once. Returns {'models': [...], "
        "'persisted': {model: id}}; when the config file cannot be written the "
        "read still succeeds, 'persisted' is empty and 'warning' says why."
    )

    def execute(self, persist: Optional[List[str]] = None) -> Dict[str, Any]:
        """Return every ``ir.model`` record, persisting the named ids on request.

        Read-only unless ``persist`` names at least one model. Persisting is
        best-effort by design: an unwritable config file is reported through
        ``warning`` rather than raised, so the read the caller asked for is
        never lost to a filesystem permission.

        :param persist: Model names whose resolved ``ir.model`` id should be
            written into the config's ``[model_ids]`` section. ``None`` (the
            default) writes nothing; an empty list is an explicit no-op; a
            non-empty list without a usable id yields a ``warning``.
        :return: ``{"models": [...], "persisted": {model: id}}``, plus a
            ``"warning"`` string when something asked for could not be written.
        :raises ValueError: When a resolved id is not a positive integer, which
            would mean ``ir.model`` answered with something unusable.
        """
        models = self._client["ir.model"].search([]).read(["model", "name"])
        by_name = {row.get("model"): row.get("id") for row in models}
        persisted: Dict[str, int] = {}
        warnings: List[str] = []
        for model in persist or ():
            ir_model_id = by_name.get(model)
            if not ir_model_id:
                warnings.append(
                    f"No ir.model record named {model!r} was returned, so no "
                    "[model_ids] entry was written for it."
                )
                continue
            try:
                self.config.set_model_id(model, ir_model_id)
            except ModelIdsNotWritableError as exc:
                warnings.append(str(exc))
                continue
            persisted[model] = ir_model_id
        result: Dict[str, Any] = {"models": models, "persisted": persisted}
        if warnings:
            result["warning"] = " ".join(warnings)
        return result
