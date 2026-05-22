import logging
import os
from pprint import pprint

from odoo_sdk.odoo_service import OdooClient

LOG_FORMAT = "%(levelname)-8s : %(name)-15.15s : %(message)s"


logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

ODOO_URL = os.getenv("ODOO_URL")
ODOO_DB = os.getenv("ODOO_DB")
ODOO_USERNAME = os.getenv("ODOO_USERNAME")
# Odoo API keys can be used here in place of a password.
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD")


def _require_env_configuration() -> None:
    required = {
        "ODOO_URL": ODOO_URL,
        "ODOO_DB": ODOO_DB,
        "ODOO_USERNAME": ODOO_USERNAME,
        "ODOO_PASSWORD": ODOO_PASSWORD,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Set them before running this example."
        )


if __name__ == "__main__":
    _require_env_configuration()

    odoo = OdooClient(
        url=ODOO_URL,
        db=ODOO_DB,
        username=ODOO_USERNAME,
        password=ODOO_PASSWORD,
    )

    partners = odoo["res.partner"]
    company_domain = [("is_company", "=", True)]

    print("Authenticated UID:", odoo.uid)

    print("\nCompany count:")
    print(partners.search(company_domain).count())

    print("\nFirst 5 companies:")
    first_page = partners.search(company_domain).limit(5).read(["name", "email"])
    pprint(first_page)

    print("\nNext 5 companies:")
    second_page = (
        partners.search(company_domain).limit(5).offset(5).read(["name", "email"])
    )
    pprint(second_page)

    print("\nDirect read by ID:")
    first_ids = partners.search(company_domain).limit(3).read(["id"])
    partner_ids = [record["id"] for record in first_ids]
    pprint(partners.read(partner_ids, ["name", "email"]))

    # Write operations are intentionally disabled by default.
    # Uncomment only after you replace the placeholders above and verify the target data.

    # new_partner_id = partners.create({"name": "Scratch Example Partner"})
    # print("Created partner ID:", new_partner_id)

    # partners.write(partner_ids, {"comment": "Updated from scratch example"})
    # partners.search(company_domain).write({"comment": "Updated from query example"})

    # partners.search([("name", "=", "Scratch Example Partner")]).unlink()
