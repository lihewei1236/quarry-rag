"""试跑切分器,顺带校验区间。"""
import sys
from pathlib import Path

from quarry.ingest.parsers import get_parser
from quarry.ingest.splitter import split


def main(target: str) -> None:
    path = Path(target)
    raw = path.read_text(encoding="utf-8")
    parser = get_parser(str(path))

    bad = 0
    for section in parser.parse(path):
        parent, children = split(section)
        print(f"\n📄 父块 {' > '.join(parent.heading_path) or '(无标题)'}")
        print(f"   [{parent.char_start}, {parent.char_end}] 长度 {len(parent.content)}")
        for c in children:
            ok = raw[c.char_start:c.char_end] == c.content
            inside = (c.char_start >= parent.char_start
                      and c.char_end <= parent.char_end)
            if not (ok and inside):
                bad += 1
            mark = "✅" if ok and inside else "❌"
            print(f"   └─ 子块 #{c.ord} {mark} [{c.char_start}, {c.char_end}] "
                  f"长度 {len(c.content)}")
            print(f"      {c.content[:40]}...")

    print(f"\n区间校验:{'全部通过' if bad == 0 else f'{bad} 个有问题'}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "corpus/travel-guide/丽江.md")