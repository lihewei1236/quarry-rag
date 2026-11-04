"""权限红线。这些用例必须永远通过，挂了就是权限被绕过，不是功能 bug。"""
import pytest

import quarry.infra.embed as embed
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.retrieval.pg.searcher import hybrid_search


@pytest.fixture(autouse=True)
async def db_pool():
    """每个用例自己开关一次连接池，并重建向量化客户端。

    别写成 scope="session"：pytest-asyncio 默认每个用例换一个事件循环，
    连接池和向量化客户端里的 HTTP 连接都绑在创建它们的那个循环上，
    沿用到下一个用例会报 attached to a different loop 或 Event loop is closed。
    """
    embed._embedder = None
    await init_pool()
    yield
    await close_pool()


async def _coll_id(slug: str):
    async with acquire() as conn:
        cid = await conn.fetchval(
            "SELECT id FROM collections WHERE slug = $1 AND status = 'active'", slug)
    assert cid is not None, f"集合 {slug} 不存在，先跑第三章的 ingest_corpus"
    return cid


async def test_public_caller_cannot_see_corp_policy():
    """红线 1  public 调用者看不到企业制度。"""
    hits = await hybrid_search(
        collection_id=await _coll_id("corp-policy"),
        query="住宿标准是多少", caller_labels=["public"])
    assert hits == [], f"权限泄漏：public 拿到了 {len(hits)} 条企业制度"


async def test_corp_caller_can_see_own_content():
    """红线 2  有权限的调用者必须能召回，这是反向验证。"""
    hits = await hybrid_search(
        collection_id=await _coll_id("corp-policy"),
        query="住宿标准是多少", caller_labels=["org:demo-corp"])
    assert hits, "有权限却召回为空，说明过滤写得过严"


async def test_empty_labels_returns_nothing():
    """红线 3  空标签什么都看不到，哪怕查的是公开内容。"""
    hits = await hybrid_search(
        collection_id=await _coll_id("travel-guide"),
        query="兵马俑", caller_labels=[])
    assert hits == [], "空标签必须返回空"


async def test_wrong_org_cannot_see_other_org():
    """红线 4  A 组织看不到 B 组织的内容。"""
    hits = await hybrid_search(
        collection_id=await _coll_id("corp-policy"),
        query="住宿标准是多少", caller_labels=["org:other-corp"])
    assert hits == [], "跨组织泄漏"


async def test_manager_only_content_needs_role():
    """红线 5  内部笔记对 public 不可见。"""
    hits = await hybrid_search(
        collection_id=await _coll_id("internal-notes"),
        query="入职", caller_labels=["public"])
    assert hits == [], "public 不该看到内部笔记"