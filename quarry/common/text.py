"""文本归一化。全项目唯一的一份。"""
import re
import unicodedata

_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """统一全角半角、压缩连续空白、去掉首尾空格。

    调用点（全项目只允许调用这一份）：
    - quarry.common.ids.make_chunk_id 生成块标识时
    - 第三章写入 tokens 前的预处理
    - 第四章处理查询词时

    注意：只用于"计算"，不要用它改写要存进 content 的原文。

    ⚠️ 修改这个函数的规则，会让所有已有块标识失效。修改前必须准备好全量重建。
    """
    t = unicodedata.normalize("NFKC", text)   # 全角 １２０ → 120，全角 ： → :
    t = _WS.sub(" ", t)                       # 连续空白（含换行、制表符）→ 一个空格
    return t.strip()