"""接口契约与客户端的单元测试：不连数据库、不起服务。"""
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from quarry.api.models import code_of, error_payload, to_payload
from quarry.context.models import Citation, Evidence, FactRef

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk"))   # SDK 不在 quarry 包里
import quarry_client as qc                                             # noqa: E402

FACT = FactRef(ref="F1", entity="秦始皇帝陵博物院", attribute="ticket_price",
               label="门票价格", qualifier="旺季", value="150", status="verified",
               verified_at=date(2026, 9, 1), source_url=None, written_values=["120"])
EXPIRED = FactRef(ref="F2", entity="大雁塔", attribute="ticket_price", label="门票价格",
                  qualifier="全年", value="50", status="expired",
                  verified_at=date(2026, 3, 1), source_url=None, written_values=["40"])


def evidence(facts):
    c = Citation(document_id=uuid4(), source_uri="travel-guide/西安.md", title="西安",
                 url=None, license=None, heading_path=["西安", "游览"],
                 char_start=10, char_end=90, locator_kind="char",
                 verified_at=date(2026, 3, 1))
    return Evidence(chunk_id="c1", text="门票 120 元", context="门票〔F1 门票价格〕",
                    context_level="section", citation=c, score=0.03, rerank_score=0.9,
                    dense_score=0.7, sparse_score=None, dense_rank=1, sparse_rank=None,
                    sibling_hits=0, facts=facts)


def result(evidences, abstain=False):
    return SimpleNamespace(
        query="兵马俑门票多少钱", evidences=evidences, timings={"total_ms": 412.3},
        abstain=SimpleNamespace(should_abstain=abstain, reason=None,
                                top_dense_score=0.74, threshold=0.45))


def test_payload_is_plain_json():
    payload = to_payload(result([evidence([FACT, EXPIRED])]), "rid-1")
    text = json.dumps(payload, ensure_ascii=False)          # 日期、UUID 都已转成字符串
    assert '"verified_at": "2026-09-01"' in text
    e = payload["evidences"][0]
    assert e["citation"]["verified_at"] == "2026-03-01"
    assert e["facts"][0]["written_values"] == ["120"]
    assert set(e) >= {"chunk_id", "text", "context", "context_level",
                      "citation", "facts", "score"}


def test_error_shape():
    assert error_payload(code_of(429), "太快了", "rid") == {
        "error": {"code": "rate_limited", "message": "太快了", "request_id": "rid"}}
    assert code_of(418) == "error"


def test_client_parses_what_server_sends():
    parsed = qc.parse_result(to_payload(result([evidence([FACT])]), "rid-2"))
    assert parsed.request_id == "rid-2" and parsed.evidences[0].facts[0].value == "150"
    assert parsed.evidences[0].heading_path == ["西安", "游览"]


def test_render_for_prompt():
    payload = to_payload(result([evidence([FACT, EXPIRED]), evidence([FACT])]), "rid")
    text = qc.render_for_prompt(qc.parse_result(payload))
    assert text.count("- F1 ") == 1                           # 同一个事实只列一次
    assert "可能已有变化" in text and "核实于 2026-09-01" in text
    abstain = qc.render_for_prompt(qc.parse_result(to_payload(result([], abstain=True), "rid")))
    assert "暂时无法回答" in abstain


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    async def request(self, method, path, json=None):
        self.calls += 1
        return self.responses.pop(0)

    async def aclose(self):
        pass


def test_client_retries_only_what_is_worth_retrying(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(qc.asyncio, "sleep", fake_sleep)
    ok_body = to_payload(result([evidence([])]), "rid")

    http = FakeHttp([FakeResponse(503), FakeResponse(200, ok_body)])       # 5xx 重试后成功
    asyncio.run(qc.QuarryClient("http://x", "k", http=http).search("travel-guide", "q"))
    assert http.calls == 2

    http = FakeHttp([FakeResponse(429, headers={"Retry-After": "3"}),
                     FakeResponse(200, ok_body)])
    asyncio.run(qc.QuarryClient("http://x", "k", http=http).search("travel-guide", "q"))
    assert sleeps[-1] == 3.0                                              # 按服务端建议等待

    http = FakeHttp([FakeResponse(401, {"error": {"code": "unauthorized",
                                                  "message": "无效", "request_id": "r"}})])
    try:
        asyncio.run(qc.QuarryClient("http://x", "k", http=http).search("travel-guide", "q"))
        raise AssertionError("401 应该直接抛出")
    except qc.QuarryAuthError as e:
        assert http.calls == 1 and e.request_id == "r"                    # 401 不重试

    http = FakeHttp([FakeResponse(503)] * 3)
    try:
        asyncio.run(qc.QuarryClient("http://x", "k", retries=2,
                                    http=http).search("travel-guide", "q"))
        raise AssertionError("一直 503 应该最终失败")
    except qc.QuarryUnavailable:
        assert http.calls == 3