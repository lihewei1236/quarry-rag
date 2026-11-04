"""评测指标的单元测试：不连数据库、不调模型。"""
from dataclasses import dataclass
from uuid import uuid4

from quarry.eval.dataset import load_csv, qid_of
from quarry.eval.metrics import QueryResult, gold_rank, percentile, summarize

DOC = uuid4()


@dataclass
class FakeCitation:
    document_id: object
    char_start: int
    char_end: int


@dataclass
class FakeEvidence:
    citation: FakeCitation


def ev(doc, start, end):
    return FakeEvidence(FakeCitation(doc, start, end))


def test_gold_rank_matches_by_containment():
    gold = [(DOC, 100, 400)]
    # 第 1 条是别的文档，第 2 条落在金块区间里
    assert gold_rank([ev(uuid4(), 120, 200), ev(DOC, 120, 200)], gold) == 2
    assert gold_rank([ev(DOC, 500, 600)], gold) is None      # 同一文档但不在区间里
    assert gold_rank([ev(DOC, 380, 420)], gold) is None      # 越过右边界，不算命中
    assert gold_rank([ev(DOC, 100, 400)], gold) == 1         # 正好等于金块区间
    assert gold_rank([ev(DOC, 120, 200)], []) is None        # 无答案问题没有金块


def test_percentile_uses_nearest_rank():
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([], 95) == 0.0


def test_summarize():
    items = [
        QueryResult("a", "问题1", "answerable", 1, False, 3, 100.0, judge="supported"),
        QueryResult("b", "问题2", "answerable", 2, False, 3, 200.0, judge="partial"),
        QueryResult("c", "问题3", "answerable", None, True, 0, 300.0),
        QueryResult("d", "问题4", "unanswerable", None, True, 0, 400.0),
    ]
    items[0].fact_status = {"verified": 2}
    items[1].fact_status = {"expired": 1, "verified": 1}
    m = summarize(items)
    assert m["queries"] == 4
    assert m["recall"] == round(2 / 3, 4)
    assert m["mrr"] == round((1 + 0.5 + 0) / 3, 4)
    assert m["top1"] == round(1 / 3, 4)
    assert m["false_abstain"] == round(1 / 3, 4)      # 有答案却建议拒答
    assert m["abstain_correct"] == 1.0
    assert m["supported"] == 0.5 and m["judged"] == 2
    assert m["facts"] == {"verified": 3, "expired": 1}


def test_load_csv(tmp_path):
    good = tmp_path / "travel-guide.csv"
    good.write_text("query,kind,gold_chunks,note\n"
                    "兵马俑门票多少钱,answerable,aaa|bbb,西安\n"
                    "量子色动力学,unanswerable,,无关\n", encoding="utf-8")
    rows, errors = load_csv(good)
    assert not errors
    assert rows[0]["gold_chunks"] == ["aaa", "bbb"]
    assert rows[0]["qid"] == qid_of("兵马俑门票多少钱")
    assert rows[1]["gold_chunks"] == []

    bad = tmp_path / "bad.csv"
    bad.write_text("query,kind,gold_chunks,note\n"
                   "太短,answerable,aaa,\n"
                   "西安钟楼几点关门,maybe,aaa,\n"
                   "西安钟楼在哪里,answerable,,\n"
                   "量子色动力学,unanswerable,aaa,\n"
                   "大唐不夜城几点亮灯,answerable,ccc,\n"
                   "大唐不夜城几点亮灯,answerable,ccc,\n", encoding="utf-8")
    rows, errors = load_csv(bad)
    assert len(rows) == 1 and len(errors) == 5
    assert "问题太短" in errors[0] and "kind" in errors[1]
    assert "必须写 gold_chunks" in errors[2] and "不能写 gold_chunks" in errors[3]
    assert "重复" in errors[4]