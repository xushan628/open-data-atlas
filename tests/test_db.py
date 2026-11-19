#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据仓库的单元测试：表结构、行数阈值、关键内容抽样。

运行：python -m unittest discover tests -v
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "atlas.sqlite"


class AtlasDatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DB.exists():
            raise unittest.SkipTest("data/atlas.sqlite 不存在，先运行 scripts/build_db.py")
        cls.con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
        cls.con.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls) -> None:
        cls.con.close()

    def count(self, table: str) -> int:
        return self.con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def test_all_tables_exist(self) -> None:
        names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        expected = {
            "meta_datasets",
            "poetry_tang",
            "poetry_ci",
            "poetry_authors",
            "words",
            "words_senses",
            "words_examples",
            "words_collocations",
            "ex_exercises",
            "geo_countries",
            "geo_states",
            "geo_cities",
            "well_datasets",
        }
        self.assertTrue(expected.issubset(names), f"缺失表：{expected - names}")

    def test_row_thresholds(self) -> None:
        self.assertGreaterEqual(self.count("poetry_tang"), 10_000)
        self.assertGreaterEqual(self.count("poetry_ci"), 18_000)
        self.assertGreaterEqual(self.count("poetry_authors"), 4_000)
        self.assertEqual(self.count("words"), 7_298)
        self.assertGreaterEqual(self.count("words_senses"), 10_000)
        self.assertGreaterEqual(self.count("words_examples"), 10_000)
        self.assertEqual(self.count("ex_exercises"), 1_324)
        self.assertEqual(self.count("geo_countries"), 250)
        self.assertGreaterEqual(self.count("geo_states"), 5_000)
        self.assertGreaterEqual(self.count("geo_cities"), 150_000)
        self.assertGreaterEqual(self.count("well_datasets"), 20)

    def test_meta_has_five_datasets(self) -> None:
        rows = [dict(r) for r in self.con.execute("SELECT * FROM meta_datasets ORDER BY sort_order")]
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            [r["dataset_id"] for r in rows], ["poetry", "words", "exercises", "geo", "thewell"]
        )
        for r in rows:
            self.assertTrue(r["repo"].startswith(("chinese-poetry/", "designfuchen/", "hasaneyldrm/", "dr5hn/", "PolymathicAI/")))
            self.assertTrue(r["license"], f"{r['dataset_id']} 缺少许可证字段")
            self.assertGreater(r["row_count"], 0)
            self.assertTrue(str(r["video_ts"]).count(":") == 1)

    def test_poetry_content(self) -> None:
        n = self.con.execute("SELECT COUNT(*) FROM poetry_ci WHERE author='辛弃疾'").fetchone()[0]
        self.assertGreater(n, 300)
        # 样本只取了《全唐诗》前 12 个分片，不能假设《静夜思》一定在里面；
        # 改为验证分片内容本身是真实唐诗：作者数、句长、常见意象。
        authors = self.con.execute("SELECT COUNT(DISTINCT author) FROM poetry_tang").fetchone()[0]
        self.assertGreater(authors, 500)
        row = self.con.execute(
            "SELECT title, paragraphs FROM poetry_tang WHERE paragraphs LIKE '%明月%' ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row, "唐诗样本里应该能找到带“明月”的句子")
        self.assertGreater(row["paragraphs"].count("\n") + 1, 1)
        longest = self.con.execute("SELECT MAX(char_count) FROM poetry_tang").fetchone()[0]
        self.assertGreater(longest, 50)

    def test_words_content(self) -> None:
        row = self.con.execute(
            "SELECT w.word, s.zh FROM words w JOIN words_senses s ON s.word_id=w.word_id "
            "WHERE w.word='library' ORDER BY s.idx LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("图书馆", row["zh"])
        # 例句与搭配都应该是最常见的 1~2 条起步
        ex = self.con.execute("SELECT COUNT(*) FROM words_examples WHERE word_id='library'").fetchone()[0]
        self.assertGreaterEqual(ex, 1)
        col = self.con.execute("SELECT COUNT(*) FROM words_collocations WHERE word_id='library'").fetchone()[0]
        self.assertGreaterEqual(col, 1)
        # CEFR 等级都落在 A1..C2
        levels = {r[0] for r in self.con.execute("SELECT DISTINCT cefr FROM words WHERE cefr IS NOT NULL")}
        self.assertTrue(levels.issubset({"A1", "A2", "B1", "B2", "C1", "C2"}), levels)

    def test_exercises_content(self) -> None:
        row = self.con.execute("SELECT * FROM ex_exercises WHERE exercise_id='0001'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "3/4 sit-up")
        self.assertTrue(row["instructions_en"])
        cats = {r[0] for r in self.con.execute("SELECT DISTINCT category FROM ex_exercises")}
        self.assertIn("waist", cats)

    def test_geo_content(self) -> None:
        china = self.con.execute("SELECT * FROM geo_countries WHERE iso2='CN'").fetchone()
        self.assertIsNotNone(china)
        self.assertEqual(china["name"], "China")
        states = self.con.execute("SELECT COUNT(*) FROM geo_states WHERE country_code='CN'").fetchone()[0]
        self.assertGreater(states, 20)
        cities = self.con.execute("SELECT COUNT(*) FROM geo_cities WHERE country_name='China'").fetchone()[0]
        self.assertGreater(cities, 500)

    def test_thewell_metadata(self) -> None:
        rows = {r[0]: r for r in self.con.execute("SELECT dataset, dims, n_files FROM well_datasets")}
        self.assertIn("MHD_64", rows)
        self.assertGreaterEqual(rows["MHD_64"]["n_files"], 10)

    def test_database_is_read_only_from_app_view(self) -> None:
        """图鉴对外是只读的：确认 schema 里没有触发器/视图之类的写入口子。"""
        rows = list(self.con.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger','view')"))
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
