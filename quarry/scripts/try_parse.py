"""试解析，顺带校验区间。"""
import sys
from pathlib import Path

from quarry.ingest.parsers import get_parser


def main(target: str) -> None:
    path = Path(target)
    parser = get_parser(str(path))
    sections = parser.parse(path)
    raw = path.read_text(encoding="utf-8") if parser.tier == "A" else None

    for s in sections:
        flag = ""
        if raw is not None:
            flag = " ✅" if raw[s.char_start:s.char_end] == s.content else " ❌区间对不上"
        print(f"[{parser.tier}] {' > '.join(s.heading_path) or '(无标题)'}{flag}")
        print(f"    区间 [{s.char_start}, {s.char_end}] 长度 {len(s.content)}")
        print(f"    {s.content[:50]}...")
        print()

    print(f"共 {len(sections)} 个 Section")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "corpus/travel-guide/丽江.md")