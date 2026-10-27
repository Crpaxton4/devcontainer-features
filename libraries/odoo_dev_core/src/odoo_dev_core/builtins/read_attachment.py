from dataclasses import dataclass
from odoo_dev_core.services.attachments import read_attachment

from ..ports import Services
from ..registry import command


@dataclass(frozen=True)
class Args:
    attachment_id: int
    mode: str = "text"


@command(
    "read_attachment",
    args=Args,
    description="Read one document already stored in Odoo (an ir.attachment). Strictly "
    "read-only: never uploads or attaches anything. 'mode' selects the "
    "payload. 'metadata' returns id/name/mimetype/file_size/res_model/"
    "res_id/create_date with no bytes. 'text' decodes the binary payload and "
    "converts it to Markdown via markitdown (PDF/docx/xlsx/CSV/HTML -> "
    "Markdown), capping the decoded payload at 10 MiB and flagging "
    "'truncated' when the cap is hit; an unsupported or unconvertible format "
    "(or an empty payload) degrades to empty text plus a 'note' rather than "
    "erroring. 'raw' returns the base64 'datas' payload but refuses anything "
    "over the 10 MiB cap. A missing/inaccessible id raises a missing-record "
    "error; an invalid mode raises ValueError.",
)
def handle(services: Services, args: Args) -> dict:
    return read_attachment(services.client, args.attachment_id, mode=args.mode)
