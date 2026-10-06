import pytest
from conftest import live_call as call

pytestmark = [pytest.mark.anyio, pytest.mark.live]


async def test_pz_faq(client):
    data = await call(client, "pz_faq", {"query": "لغو نوبت", "limit": 3})
    assert data["total"] > 0 and "لغو" in data["answers"][0]["question"]
