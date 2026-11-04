"""裁判模型：判断一组证据够不够回答这个问题。它只评证据，不写答案。"""
from __future__ import annotations

import json

from openai import AsyncOpenAI

from quarry.infra.settings import Settings

VERDICTS = ("supported", "partial", "unsupported")
MAX_CONTEXT_CHARS = 4000

SYSTEM_PROMPT = """你是检索结果的评审员。用户会给出一个问题和检索到的证据。
判断这些证据够不够回答这个问题，只输出 JSON：
{"verdict": "supported", "reason": "一句话说明"}

verdict 只能是下面三个值之一：
- supported：证据里有直接回答问题的信息
- partial：证据沾边，但缺关键信息，照着回答会不完整
- unsupported：证据回答不了这个问题

注意：
1. 只看证据够不够，不要用你自己的知识补充。
2. 证据里形如〔F1 门票价格〕的占位符，表示平台已经单独提供了这个数值，按"已经给出"处理。
3. reason 不超过 30 个字。"""


def make_client(s: Settings) -> AsyncOpenAI:
    key = s.judge_api_key.get_secret_value() or s.llm_api_key.get_secret_value()
    if not s.judge_model or not s.judge_base_url:
        raise ValueError("先在 .env 里配置 JUDGE_MODEL 和 JUDGE_BASE_URL")
    if not key or "REPLACE-ME" in key:
        raise ValueError("没有可用的 API Key：先填好 LLM_API_KEY，或单独填 JUDGE_API_KEY")
    return AsyncOpenAI(base_url=s.judge_base_url, api_key=key,
                       timeout=s.judge_timeout_s, max_retries=2)


async def judge_one(client: AsyncOpenAI, model: str, query: str,
                    contexts: list[str]) -> tuple[str | None, str]:
    """返回 (verdict, reason)。模型给出的 verdict 不在清单里时，返回 (None, 原因)。"""
    evidence = "\n---\n".join(contexts)[:MAX_CONTEXT_CHARS]
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"问题：{query}\n\n证据：\n{evidence}"},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        extra_body={"enable_thinking": False},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    verdict = data.get("verdict") if isinstance(data, dict) else None
    reason = str(data.get("reason", ""))[:60] if isinstance(data, dict) else ""
    if verdict not in VERDICTS:
        return None, "裁判输出不合规"
    return verdict, reason