"""第五章新增的红线：整条链路（含父块、邻块回捞）不能绕过权限。"""
import pytest

import quarry.infra.embed as embed
from quarry.infra.db import close_pool, init_pool
from quarry.pipeline import search_evidence
from quarry.rerank import reset_reranker


@pytest.fixture(autouse=True)
async def db_pool():
    # pytest-asyncio 每个用例换一个事件循环：
    # 连接池、向量化客户端、重排客户端都要跟着重建
    embed._embedder = None
    reset_reranker()
    await init_pool()
    yield
    await close_pool()


async def test_public_caller_gets_no_corp_evidence():
    """红线 6  public 调用者拿不到企业制度的任何证据。"""
    r = await search_evidence("corp-policy", "住宿标准是多少", caller_labels=["public"])
    assert r.evidences == [], f"权限泄漏：public 拿到了 {len(r.evidences)} 条企业制度证据"


async def test_corp_caller_gets_evidence_with_context():
    """红线 7  有权限的调用者能拿到证据，而且上下文里一定包含命中块本身。"""
    r = await search_evidence("corp-policy", "住宿标准是多少",
                              caller_labels=["org:demo-corp"])
    assert r.evidences, "有权限却拿不到证据，过滤写得过严"
    for e in r.evidences:
        assert e.text in e.context, "上下文里必须包含命中块本身"