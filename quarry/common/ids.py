"""稳定块标识：同样的内容在同样的位置，永远算出同样的 chunk_id。"""
import hashlib

from quarry.common.text import normalize

_FIELD_SEP = "\0"     # 四个部分之间的分隔符（空字节）
_PATH_SEP = "\x1f"    # 标题路径各层之间的分隔符（单元分隔符）
# 两个都是正常文本里不会出现的控制字符，拼接起来不会有歧义。
# 不用 "/"、":"、"|"：标题和正文里都可能出现，例如 "出入境/签证"。


def make_chunk_id(
    collection_slug: str,
    source_uri: str,
    heading_path: list[str] | None,
    content: str,
) -> str:
    """由四个部分算出 32 位十六进制标识。

    参数：
        collection_slug: 集合短名
        source_uri:      文档来源，区分不同文档
        heading_path:    标题路径，区分同一文档内内容相同的块
        content:         正文，归一化之后参与计算

    返回：
        32 个十六进制字符。同样的输入永远返回同样的值，
        不管在哪个进程、哪台机器、什么时候算。

    注意：它只保证在同一个集合内唯一，数据库主键是 (collection_id, chunk_id)，见 2.8.3。
    """
    path = _PATH_SEP.join(normalize(h) for h in (heading_path or []))
    raw = _FIELD_SEP.join([
        collection_slug,
        source_uri,
        path,
        normalize(content),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]