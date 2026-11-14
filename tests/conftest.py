import pytest

from award_search import server


@pytest.fixture(autouse=True)
def empty_search_cache():
    """Each test starts with no remembered searches, so one test's results can't leak into another."""
    server.clear_cache()
    yield
    server.clear_cache()
