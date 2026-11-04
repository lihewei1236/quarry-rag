"""第八章新增的红线：权限标签只能来自服务端；没有有效的 key 什么都拿不到。"""
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from quarry.api import routes
from quarry.api.models import SearchRequest
from quarry.api.security import Caller, fetch_caller, hash_key, require_caller
from quarry.infra.db import acquire, close_pool, init_pool

TEST_KEY = "qr_redline_test_key_do_not_use"


class FakeRedis:
    def __init__(self):
        self.counts = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        pass


def fake_request(redis=None):
    return SimpleNamespace(
        state=SimpleNamespace(request_id="00000000-0000-0000-0000-000000000000"),
        app=SimpleNamespace(state=SimpleNamespace(redis=redis or FakeRedis())))


@pytest.fixture(autouse=True)
async def db_pool():
    await init_pool()
    async with acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_id = 'redline0'")
        await conn.execute(
            """INSERT INTO api_keys (key_id, key_hash, caller_id, labels,
                                     collections, rate_limit)
               VALUES ('redline0', $1, 'redline-test', '{public}', '{travel-guide}', 2)""",
            hash_key(TEST_KEY))
    yield
    async with acquire() as conn:
        await conn.execute("DELETE FROM api_keys WHERE key_id = 'redline0'")
    await close_pool()


async def test_labels_come_from_the_key_not_the_request(monkeypatch):
    """红线 10  请求体里夹带的权限字段不生效；不在白名单的集合直接 403。"""
    seen = {}

    async def fake_search(collection, query, caller_labels, **kwargs):
        seen["labels"] = caller_labels
        return SimpleNamespace(query=query, evidences=[], timings={},
                               abstain=SimpleNamespace(should_abstain=True, reason="empty",
                                                       top_dense_score=None, threshold=0.45))

    async def no_log(**kwargs):
        pass

    monkeypatch.setattr(routes, "search_evidence", fake_search)
    monkeypatch.setattr(routes, "log_query", no_log)
    caller = Caller("k", "zhixing", ["public"], ["travel-guide"], 60)

    req = SearchRequest.model_validate({"collection": "travel-guide", "query": "q",
                                        "caller_labels": ["org:demo-corp"]})
    assert not hasattr(req, "caller_labels")
    await routes.search(req, fake_request(), BackgroundTasks(), caller=caller)
    assert seen["labels"] == ["public"]

    with pytest.raises(HTTPException) as e:
        await routes.search(SearchRequest(collection="corp-policy", query="q"),
                            fake_request(), BackgroundTasks(), caller=caller)
    assert e.value.status_code == 403


async def test_no_valid_key_no_caller():
    """红线 11  没有 key、错的 key、停用的 key 一律 401；有效的 key 能换出调用方，超限 429。"""
    for header in ("", "Token abc", "Bearer qr_wrong_key"):
        with pytest.raises(HTTPException) as e:
            await require_caller(fake_request(), authorization=header)
        assert e.value.status_code == 401

    request = fake_request()        # 同一个 FakeRedis，计数才会累加
    caller = await require_caller(request, authorization=f"Bearer {TEST_KEY}")
    assert caller.caller_id == "redline-test" and caller.labels == ["public"]

    await require_caller(request, authorization=f"Bearer {TEST_KEY}")      # 第 2 次，到上限
    with pytest.raises(HTTPException) as e:
        await require_caller(request, authorization=f"Bearer {TEST_KEY}")  # 第 3 次，超限
    assert e.value.status_code == 429

    async with acquire() as conn:
        await conn.execute(
            "UPDATE api_keys SET status = 'disabled' WHERE key_id = 'redline0'")
        assert await fetch_caller(conn, TEST_KEY) is None