"""上游接入示例：知行旅行助手怎么用 quarry-rag。

用法：
    $env:QUARRY_URL = "http://127.0.0.1:8100"
    $env:QUARRY_KEY = "qr_..."
    python sdk/example.py "兵马俑门票多少钱"
"""
import asyncio
import os
import sys

from quarry_client import QuarryAuthError, QuarryClient, QuarryError, render_for_prompt


async def main(question: str) -> None:
    async with QuarryClient(os.environ["QUARRY_URL"], os.environ["QUARRY_KEY"]) as client:
        try:
            result = await client.search("travel-guide", question, top_k=5)
        except QuarryAuthError as e:
            print(f"鉴权失败，检查 key 和集合权限：{e}")
            return
        except QuarryError as e:
            print(f"检索暂时不可用，用降级话术回复用户：{e}")
            return

    # 这一段放进上游自己的提示词，再调用上游自己的大模型。生成答案不属于平台
    print(render_for_prompt(result))

    # 原文高亮：text 没有被遮蔽，区间能直接对回原文
    print("\n来源卡片：")
    for i, e in enumerate(result.evidences, 1):
        date = (f"核实于 {e.verified_at}" if e.verified_at
                else "含未核实信息" if e.facts else "")
        print(f"  [{i}] {' > '.join(e.heading_path)}  "
              f"[{e.char_start}, {e.char_end}]  {date}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "兵马俑门票多少钱"))