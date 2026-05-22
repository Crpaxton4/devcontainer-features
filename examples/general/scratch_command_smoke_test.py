import logging

from examples.commands.get_uid_command import GetUidCommand
from odoo_sdk.command_registry.command_registry import CommandDispatcher
from odoo_sdk.odoo_service import OdooClient

LOG_FORMAT = "%(levelname)-8s : %(name)-15.15s : %(message)s"


logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def main() -> None:
    client = OdooClient(config_path="odoo.ini")

    dispatcher = CommandDispatcher(client)
    dispatcher.register("get_uid", GetUidCommand)

    print("Running get_uid command...")
    print(dispatcher["get_uid"]())


if __name__ == "__main__":
    main()
