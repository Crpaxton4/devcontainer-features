import pytest

from odoo_dev_data.events import EventStore


@pytest.fixture
def store(tmp_path):
    with EventStore(home=tmp_path) as event_store:
        yield event_store
