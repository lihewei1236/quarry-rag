"""quarry-rag 的 Python 客户端。把这一个文件复制到上游项目即可，只依赖 httpx。

    async with QuarryClient("http://localhost:8100", api_key="qr_...") as client:
        result = await client.search("travel-guide", "兵马俑门票多少钱")
        prompt_block = render_for_prompt(result)
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import httpx

__version__ = "1.0.0"

NO_RETRY = {400, 401, 403, 404, 422}          # 重试也不会变好的错误


class QuarryError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 request_id: str | None = None):
        super().__init__(f"[{status} {code}] {message}（request_id={request_id}）")
        self.status, self.code, self.message, self.request_id = (
            status, code, message, request_id)


class QuarryAuthError(QuarryError):
    """401 / 403：key 不对，或者没有这个集合的权限。"""


class QuarryRateLimited(QuarryError):
    """429：打得太快。retry_after 是服务端建议等待的秒数。"""

    def __init__(self, *args, retry_after: float = 1.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class QuarryUnavailable(QuarryError):
    """5xx、超时、网络错误，重试之后仍然失败。"""


@dataclass
class Fact:
    ref: str
    entity: str
    label: str
    qualifier: str
    value: str
    status: str                       # verified | expired | unverified
    verified_at: str | None
    source_url: str | None


@dataclass
class Evidence:
    chunk_id: str
    text: str                         # 原文，用来高亮
    context: str                      # 给大模型读的上下文，开了事实层时数值已被遮蔽
    context_level: str
    source_uri: str
    heading_path: list[str]
    char_start: int
    char_end: int
    url: str | None
    verified_at: str | None
    facts: list[Fact] = field(default_factory=list)


@dataclass
class SearchResult:
    request_id: str
    query: str
    should_abstain: bool
    abstain_reason: str | None
    evidences: list[Evidence]
    timings: dict


def parse_result(data: dict) -> SearchResult:
    evidences = []
    for e in data["evidences"]:
        c = e["citation"]
        evidences.append(Evidence(
            chunk_id=e["chunk_id"], text=e["text"], context=e["context"],
            context_level=e["context_level"], source_uri=c["source_uri"],
            heading_path=c["heading_path"], char_start=c["char_start"],
            char_end=c["char_end"], url=c.get("url"), verified_at=c.get("verified_at"),
            facts=[Fact(ref=f["ref"], entity=f["entity"], label=f["label"],
                        qualifier=f["qualifier"], value=f["value"], status=f["status"],
                        verified_at=f.get("verified_at"), source_url=f.get("source_url"))
                   for f in e.get("facts", [])]))
    a = data["abstain"]
    return SearchResult(request_id=data["request_id"], query=data["query"],
                        should_abstain=a["should_abstain"], abstain_reason=a.get("reason"),
                        evidences=evidences, timings=data.get("timings", {}))


def _error_from(resp) -> QuarryError:
    try:
        err = resp.json().get("error", {})
    except Exception:
        err = {}
    args = (resp.status_code, err.get("code", "error"),
            err.get("message", resp.text[:200]),
            err.get("request_id") or resp.headers.get("X-Request-Id"))
    if resp.status_code in (401, 403):
        return QuarryAuthError(*args)
    if resp.status_code == 429:
        return QuarryRateLimited(*args, retry_after=float(resp.headers.get("Retry-After", 1)))
    if resp.status_code >= 500:
        return QuarryUnavailable(*args)
    return QuarryError(*args)


class QuarryClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 8.0,
                 retries: int = 2, http: httpx.AsyncClient | None = None):
        self.retries = retries
        self._http = http or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}",
                     "User-Agent": f"quarry-client/{__version__}"})

    async def __aenter__(self) -> QuarryClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, collection: str, query: str, *, top_k: int | None = None,
                     filters: dict | None = None) -> SearchResult:
        body = {"collection": collection, "query": query}
        if top_k is not None:
            body["top_k"] = top_k
        if filters:
            body["filters"] = filters
        return parse_result(await self._request("POST", "/v1/search", body))

    async def collections(self) -> list[dict]:
        return (await self._request("GET", "/v1/collections"))["collections"]

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        last: QuarryError | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = await self._http.request(method, path, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = QuarryUnavailable(0, "network", repr(e))
            else:
                if resp.status_code < 400:
                    return resp.json()
                last = _error_from(resp)
                if resp.status_code in NO_RETRY:
                    raise last
            if attempt < self.retries:
                await asyncio.sleep(self._backoff(attempt, last))
        raise last

    @staticmethod
    def _backoff(attempt: int, err: QuarryError) -> float:
        if isinstance(err, QuarryRateLimited):
            return min(err.retry_after, 10.0)
        return 0.5 * 2 ** attempt + random.uniform(0, 0.2)       # 0.5s、1s、2s……加抖动


def _fact_line(f: Fact) -> str:
    head = f"- {f.ref} {f.entity} {f.label}（{f.qualifier}）：{f.value}"
    if f.status == "verified":
        return f"{head}，核实于 {f.verified_at}"
    if f.status == "expired":
        return (f"{head}，核实于 {f.verified_at}，可能已有变化，"
                f"请提示用户以官方公布为准")
    return f"{head}，来自第三方攻略，未经核实"


def render_for_prompt(result: SearchResult, max_evidences: int = 5) -> str:
    """把证据拼成可以直接放进提示词的一段文字。第六章 6.6.2 的约定在这里落地。"""
    if result.should_abstain or not result.evidences:
        return "【检索结果】没有找到可靠的证据。请直接告诉用户暂时无法回答，不要凭常识作答。"

    lines = ["【检索结果】回答只能依据下面的证据；证据不够时，直接说明不知道。",
             "上下文里形如〔F1 门票价格〕的占位符，"
             "对应\u201c数值\u201d一栏里编号相同的那一项。"]
    shown: set[str] = set()
    for i, e in enumerate(result.evidences[:max_evidences], 1):
        lines += ["", f"[{i}] {e.source_uri} · {' > '.join(e.heading_path)}", e.context]
        new_facts = [f for f in e.facts if f.ref not in shown]
        if new_facts:
            lines.append("数值：")
            lines += [_fact_line(f) for f in new_facts]
            shown.update(f.ref for f in new_facts)
    return "\n".join(lines)