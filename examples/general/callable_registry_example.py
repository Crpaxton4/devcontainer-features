"""Example: using the callable-based CommandDispatcher from a consumer's point of view.

This file demonstrates how a third-party consumer (installed via pip) would
instantiate a client, register callable commands (classes or factories), and
invoke them via `CommandDispatcher`.

Run as a script for a minimal demo; it intentionally uses a small fake client
so the example is self-contained and has no external dependencies.
"""

from odoo_sdk.commands import Command, Registry

class FakeClient:
    """A minimal fake client for demonstration purposes.

    In real usage you would use `odoo_sdk.OdooClient(...)` or another
    concrete client implementation provided by the package.
    """

    def __init__(self, uid: int):
        self.uid = uid

    def create_task(self, vals: dict) -> int:
        # pretend to persist and return a generated id
        print("FakeClient.create_task called with:", vals)
        return 1001


class GetUidCommand:
    """A small callable command that returns the current user's UID."""

    def __init__(self, client: FakeClient):
        self.client = client

    def __call__(self):
        return self.client.uid


class CreateTaskCommand:
    """A callable command that creates a task using the injected client."""

    def __init__(self, client: FakeClient):
        self.client = client

    def __call__(self, name: str, project_id: int, description: str = "") -> int:
        task_vals = {
            "name": name,
            "project_id": project_id,
            "description": description,
        }
        return self.client.create_task(task_vals)


def make_echo_command(client: FakeClient):
    """Factory that returns a simple echo callable bound to `client`."""

    def echo(msg: str) -> str:
        return f"{client.uid}:{msg}"

    return echo


def main() -> None:
    # In a real consumer you would do something like:
    # from odoo_sdk import OdooClient, CommandDispatcher
    # client = OdooClient(config_path="odoo.ini")
    # dispatcher = CommandDispatcher(client)

    client = FakeClient(uid=42)
    registry = Registry(client)

    # You may register a class directly (classes are callables that return
    # an instance when called with `client`). The dispatcher will call the
    # registered factory with the shared `client` when executing.
    registry.register("get_uid", GetUidCommand)
    registry.register("create_task", CreateTaskCommand)

    # Or register a factory function that returns a callable
    registry.register("echo", make_echo_command)

    print("get_uid ->", registry["get_uid"]())
    print(
        "create_task ->",
        registry["create_task"]("My task", 7, "a demo task"),
    )
    print("echo ->", registry["echo"]("hello"))


if __name__ == "__main__":
    main()
