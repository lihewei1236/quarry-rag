"""验证 make_chunk_id 的必要性质：

1. 确定性：同样的输入，同样的输出
2. 归一化：全角半角、多余空白不影响结果
3. 区分性：内容不同、标题不同，结果就不同
4. 无歧义：标题路径的拼接不会把两种不同的路径算成一个
5. 跨进程稳定：新开一个 Python 进程算，结果和本进程相同
"""
import subprocess
import sys

from quarry.common.ids import make_chunk_id

BASE = ("travel-guide", "xian-guide.md", ["必游景点", "兵马俑博物馆"], "门票 120 元")


def check_deterministic():
    a = make_chunk_id(*BASE)
    b = make_chunk_id(*BASE)
    assert a == b, f"同一进程内两次结果不同: {a} vs {b}"
    print(f"✅ 确定性:同样的输入 → {a}")


def check_normalization():
    a = make_chunk_id("travel-guide", "xian-guide.md",
                      ["必游景点", "兵马俑博物馆"], "门票 １２０ 元")
    b = make_chunk_id(*BASE)
    assert a == b, f"归一化没生效: {a} vs {b}"
    print("✅ 归一化:全角「１２０」和半角「120」→ 同一个 ID")


def check_content_matters():
    a = make_chunk_id(*BASE)
    b = make_chunk_id("travel-guide", "xian-guide.md",
                      ["必游景点", "兵马俑博物馆"], "门票 130 元")
    assert a != b, "内容不同却算出了相同的 ID"
    print("✅ 区分性:内容不同 → ID 不同")


def check_heading_matters():
    a = make_chunk_id("travel-guide", "xian-guide.md", ["大雁塔"], "详见官网")
    b = make_chunk_id("travel-guide", "xian-guide.md", ["华清宫"], "详见官网")
    assert a != b, "heading_path 没有参与计算"
    print("✅ 区分性:内容相同、标题不同 → ID 不同")


def check_path_separator():
    a = make_chunk_id("travel-guide", "visa.md", ["出入境/签证"], "详见官网")
    b = make_chunk_id("travel-guide", "visa.md", ["出入境", "签证"], "详见官网")
    assert a != b, "标题路径拼接有歧义"
    print('✅ 无歧义:["出入境/签证"] 和 ["出入境", "签证"] → ID 不同')


def check_cross_process():
    here = make_chunk_id(*BASE)
    code = (
        "from quarry.common.ids import make_chunk_id;"
        "print(make_chunk_id('travel-guide', 'xian-guide.md',"
        " ['必游景点', '兵马俑博物馆'], '门票 120 元'))"
    )
    there = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert here == there, f"跨进程不一致: 本进程={here} 新进程={there}"
    print(f"✅ 跨进程稳定:新开一个 Python 进程也算出 {there}")


if __name__ == "__main__":
    check_deterministic()
    check_normalization()
    check_content_matters()
    check_heading_matters()
    check_path_separator()
    check_cross_process()
    print("\n全部通过。这个函数可以放心用作块标识。")