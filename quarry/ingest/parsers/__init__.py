"""Parser 注册。按扩展名派发到对应实现。"""
from quarry.ingest.parsers.base import Parser, Section
from quarry.ingest.parsers.docx import DocxParser
from quarry.ingest.parsers.markdown import MarkdownParser
# from quarry.ingest.parsers.xlsx import XlsxParser
# from quarry.ingest.parsers.pdf import PdfParser

_REGISTRY: list[Parser] = [
    MarkdownParser(),
    DocxParser(),
    # XlsxParser(),
    # PdfParser(),
]


def get_parser(source_uri: str) -> Parser:
    """按扩展名派发，找不到就抛异常。"""
    for parser in _REGISTRY:
        if parser.can_parse(source_uri):
            return parser
    raise ValueError(f"没有解析器能处理：{source_uri}")


__all__ = ["Parser", "Section", "get_parser"]