"""从维基系站点抓一批条目，存成带标题层级的 markdown 语料。

用法:
    uv run python -m quarry.scripts.fetch_corpus
    uv run python -m quarry.scripts.fetch_corpus --check
    uv run python -m quarry.scripts.fetch_corpus --proxy http://127.0.0.1:7890
    uv run python -m quarry.scripts.fetch_corpus --site zh.wikipedia.org --min-chars 1500
    uv run python -m quarry.scripts.fetch_corpus --resume

产物:
    corpus/travel-guide/<标题>.md
    corpus/_manifest/<站点>.jsonl      每行一条，记来源链接、许可证、抓取日期

连不上的时候会在连续三次失败之后直接停下来给诊断，不会让你等三十个超时。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

# 默认抓这些城市。想换就改这个列表，或者用 --titles-file 指一个文本文件，一行一个。
DEFAULT_TITLES = [
    "北京", "上海", "西安", "成都", "杭州", "南京", "广州", "苏州",
    "厦门", "桂林", "丽江", "青岛", "重庆", "昆明", "拉萨", "哈尔滨",
    "大理", "敦煌", "张家界", "长沙", "武汉", "天津", "三亚", "洛阳",
    "平遥", "黄山", "香港", "澳门", "台北", "乌鲁木齐",
]

# 维基媒体基金会要求带一个能联系到人的 User-Agent，不然可能被拒。
UA = "quarry-rag-course/0.2 (teaching material; contact: you@example.com)"

# 匹配 explaintext 输出里的章节标题，例如 == 抵达 == 或 === 飞机 ===
_WIKI_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1$")

# 文件名里不允许出现的字符
_BAD_NAME = re.compile(r"[^\w\u4e00-\u9fff-]+")

# 连续多少次网络层失败就认定环境有问题，直接退出
FAIL_FAST_AFTER = 3

# 这些异常算"网络层连不上"，和"服务端返回了错误"要分开处理
NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


# ----------------------------------------------------------------------
# 客户端

def make_client(timeout: float, proxy: str | None) -> httpx.Client:
    """建一个 httpx 客户端。

    不传 --proxy 时，httpx 默认会读环境变量 HTTPS_PROXY / HTTP_PROXY，
    所以已经设置过代理环境变量的同学不用再传参数。
    """
    common = dict(
        headers={"User-Agent": UA},
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
    )
    if not proxy:
        return httpx.Client(**common)
    try:
        return httpx.Client(proxy=proxy, **common)       # httpx >= 0.26
    except TypeError:
        return httpx.Client(proxies=proxy, **common)     # httpx < 0.26


def preflight(client: httpx.Client, site: str) -> bool:
    """先打一个最轻的接口确认站点通不通，通了再开始抓。"""
    url = f"https://{site}/w/api.php"
    params = {"action": "query", "meta": "siteinfo",
              "format": "json", "formatversion": "2"}
    try:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        name = resp.json()["query"]["general"].get("sitename", site)
        print(f"✅ 连通 {site}，站点名 {name}")
        return True
    except NETWORK_ERRORS as e:
        diagnose(site, e)
        return False
    except Exception as e:
        print(f"❌ {site} 返回了意外结果 {type(e).__name__}: {e}")
        print("   站点是通的，但接口可能改了。先用浏览器打开下面这个地址看看返回什么。")
        print(f"   https://{site}/w/api.php?action=query&meta=siteinfo&format=json")
        return False


def diagnose(site: str, err: Exception) -> None:
    """连不上时打印一段能照着做的诊断。"""
    print()
    print(f"❌ 连不上 {site}")
    print(f"   底层报错 {type(err).__name__}: {err}")
    print()
    print("   如果报的是 TLS handshake timed out 或者 ConnectTimeout，")
    print("   说明 DNS 解析出来了、TCP 也通了，但 TLS 握手没人回应。")
    print("   这是网络层的事，不是代码写错了。")
    print()
    print("   先确认一下是不是只有这个站点不通（Windows 用 curl.exe，macOS / Linux 去掉 .exe）：")
    print("     curl.exe -I --max-time 10 https://www.baidu.com/")
    print(f"     curl.exe -I --max-time 10 https://{site}/")
    print()
    print("   三条路，按省事程度排：")
    print("   1. 有代理就带上")
    print("        uv run python -m quarry.scripts.fetch_corpus --proxy http://127.0.0.1:7890")
    print("   2. 换站点，见课件 3.1.5 的退路表")
    print("   3. 先跳过，用课件 3.1.7 的手写语料跑通链路，语料以后再补")
    print()


# ----------------------------------------------------------------------
# 抓取与转换

def fetch_extract(client: httpx.Client, site: str, title: str,
                  retries: int = 2, backoff: float = 2.0) -> str | None:
    """取一个条目的纯文本正文。

    取不到条目返回 None。网络层连不上会把异常抛出去，交给上层决定要不要停。
    """
    params = {
        "action": "query",
        "prop": "extracts",
        "explaintext": "1",
        "exsectionformat": "wiki",
        "redirects": "1",
        "format": "json",
        "formatversion": "2",
        "titles": title,
    }
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.get(f"https://{site}/w/api.php", params=params)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", [])
            if not pages or pages[0].get("missing"):
                return None
            return pages[0].get("extract") or None
        except NETWORK_ERRORS as e:
            last = e
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last                                           # type: ignore[misc]


def to_markdown(title: str, extract: str) -> str:
    """把 == 章节 == 换成 markdown 标题，其余原样保留。"""
    out = [f"# {title}", ""]
    for raw in extract.splitlines():
        m = _WIKI_HEADING.match(raw.strip())
        if m:
            out.extend(["", "#" * len(m.group(1)) + " " + m.group(2), ""])
        else:
            out.append(raw.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out))
    return text.strip() + "\n"


def safe_name(title: str) -> str:
    return _BAD_NAME.sub("_", title).strip("_") or "untitled"


def load_titles(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_TITLES
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def load_manifest(path: Path) -> dict[str, dict]:
    """读已有清单，断点续抓时用来保留上一轮的记录。"""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[rec["source_uri"]] = rec
    return out


def write_manifest(path: Path, records: dict[str, dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for key in sorted(records):
            f.write(json.dumps(records[key], ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="zh.wikivoyage.org")
    ap.add_argument("--out", default="corpus/travel-guide")
    ap.add_argument("--titles-file", default=None, help="一行一个标题的文本文件")
    ap.add_argument("--min-chars", type=int, default=800)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--proxy", default=None,
                    help="例如 http://127.0.0.1:7890，不传则读 HTTPS_PROXY 环境变量")
    ap.add_argument("--resume", action="store_true",
                    help="已经抓下来的文件跳过，接着抓剩下的")
    ap.add_argument("--check", action="store_true",
                    help="只做连通性检查，不抓取")
    args = ap.parse_args()

    titles = load_titles(args.titles_file)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = Path("corpus/_manifest")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{args.site.replace('.', '_')}.jsonl"

    records = load_manifest(manifest_path) if args.resume else {}
    kept, skipped, missing, resumed = 0, 0, 0, 0
    today = date.today().isoformat()
    consecutive_fail = 0

    with make_client(args.timeout, args.proxy) as client:
        if not preflight(client, args.site):
            return 2
        if args.check:
            print("只做了连通性检查，没有抓取。去掉 --check 再跑一次。")
            return 0

        for title in titles:
            fname = f"{safe_name(title)}.md"
            target = out_dir / fname

            if args.resume and target.exists():
                print(f"[已有跳过] {title}")
                resumed += 1
                continue

            try:
                extract = fetch_extract(client, args.site, title)
                consecutive_fail = 0
            except NETWORK_ERRORS as e:
                consecutive_fail += 1
                print(f"[网络失败 {consecutive_fail}/{FAIL_FAST_AFTER}] {title}: "
                      f"{type(e).__name__}")
                if consecutive_fail >= FAIL_FAST_AFTER:
                    diagnose(args.site, e)
                    print("已经连续失败三次，停下来了。"
                          "已经抓到的部分留在磁盘上，修好之后加 --resume 接着跑。")
                    write_manifest(manifest_path, records)
                    return 2
                time.sleep(args.sleep)
                continue
            except Exception as e:
                print(f"[错误] {title}: {type(e).__name__}: {e}")
                time.sleep(args.sleep)
                continue

            if extract is None:
                print(f"[缺条目] {title}")
                missing += 1
                time.sleep(args.sleep)
                continue

            if len(extract) < args.min_chars:
                print(f"[太短跳过] {title} 只有 {len(extract)} 字")
                skipped += 1
                time.sleep(args.sleep)
                continue

            target.write_text(to_markdown(title, extract), encoding="utf-8")
            source_uri = f"{out_dir.name}/{fname}"
            records[source_uri] = {
                "source_uri": source_uri,
                "title": title,
                "site": args.site,
                "url": f"https://{args.site}/wiki/{title}",
                "license": "CC BY-SA (以条目页面底部声明为准)",
                "fetched_at": today,
                "chars": len(extract),
            }
            print(f"[已保存] {title} {len(extract)} 字 -> {fname}")
            kept += 1
            time.sleep(args.sleep)

    write_manifest(manifest_path, records)

    total_on_disk = len(list(out_dir.glob("*.md")))
    print()
    print("=" * 50)
    print(f"本轮保存 {kept} 份，已有跳过 {resumed} 份，"
          f"太短跳过 {skipped} 份，没找到 {missing} 份")
    print(f"目录里现在一共 {total_on_disk} 份 markdown")
    print(f"清单写在 {manifest_path}")
    if total_on_disk < 15:
        print("⚠️  不足 15 份。换 --site zh.wikipedia.org 再抓一遍，"
              "或者看课件 3.1.5 的退路表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())