#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API 层的集成测试：起一个临时端口的真实服务，打所有端点，顺带验证只读边界。

运行：python -m unittest tests.test_api -v
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AtlasApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (ROOT / "data" / "atlas.sqlite").exists():
            raise unittest.SkipTest("data/atlas.sqlite 不存在，先运行 scripts/build_db.py")
        cls.port = free_port()
        env = {**os.environ, "ATLAS_PORT": str(cls.port), "ATLAS_HOST": "127.0.0.1", "PYTHONIOENCODING": "utf-8"}
        cls.proc = subprocess.Popen(
            [PY, "scripts/serve.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{cls.port}"
        cls.base = base
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1).read()
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.15)
        raise RuntimeError("服务 12 秒内没有起来")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()

    # ---------- helpers ----------
    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def query(self, sql: str):
        req = urllib.request.Request(
            self.base + "/api/query",
            data=json.dumps({"sql": sql}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    # ---------- tests ----------
    def test_health(self) -> None:
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertGreater(body["rows"], 200_000)
        self.assertEqual(body["datasets"], 5)
        self.assertGreater(body["dbBytes"], 20 * 1024 * 1024)

    def test_datasets_payload(self) -> None:
        status, body = self.get("/api/datasets")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["datasets"]), 5)
        ids = [d["dataset_id"] for d in body["datasets"]]
        self.assertEqual(ids, ["poetry", "words", "exercises", "geo", "thewell"])
        self.assertEqual(len(body["tables"]), 12)
        for table in body["tables"]:
            self.assertTrue(table["columns"], table["name"])
            self.assertGreater(table["rows"], 0)
        self.assertGreaterEqual(len(body["examples"]), 5)

    def test_rows_pagination(self) -> None:
        status, body = self.get("/api/rows?table=geo_cities&limit=5&page=2")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["rows"]), 5)
        self.assertGreater(body["total"], 150_000)
        self.assertEqual(body["page"], 2)
        self.assertGreater(body["pages"], 1)

    def test_rows_search(self) -> None:
        status, body = self.get("/api/rows?table=words&q=library&limit=10")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(body["total"], 1)
        words = [row[1] for row in body["rows"]]
        self.assertTrue(any("library" in w for w in words), words)

    def test_rows_rejects_unknown_table(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/rows?table=sqlite_master")
        self.assertEqual(ctx.exception.code, 400)

    def test_query_select(self) -> None:
        status, body = self.query("SELECT COUNT(*) AS n FROM geo_countries")
        self.assertEqual(status, 200)
        self.assertEqual(body["rows"], [[250]])
        self.assertIn("n", body["columns"])

    def test_query_join(self) -> None:
        status, body = self.query(
            "SELECT w.word, s.zh FROM words w JOIN words_senses s ON s.word_id=w.word_id "
            "WHERE w.word='library' ORDER BY s.idx LIMIT 1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["rows"][0][0], "library")
        self.assertIn("图书馆", body["rows"][0][1])

    def test_query_is_capped(self) -> None:
        status, body = self.query("SELECT * FROM geo_cities")
        self.assertEqual(status, 200)
        self.assertLessEqual(body["rowCount"], 500)
        self.assertTrue(body["truncated"])

    def test_write_operations_rejected(self) -> None:
        for sql in (
            "DELETE FROM words",
            "UPDATE words SET word='x'",
            "INSERT INTO words (word_id, word) VALUES ('x','y')",
            "DROP TABLE words",
            "PRAGMA table_info(words)",
            "ATTACH DATABASE 'x.db' AS x",
            "SELECT 1; DROP TABLE words",
        ):
            status, body = self.query(sql)
            self.assertEqual(status, 400, f"{sql} 应该被拒绝")
            self.assertFalse(body["ok"])

    def test_static_index_served(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=10) as resp:
            html = resp.read().decode("utf-8")
        self.assertEqual(resp.status, 200)
        for word in ("开源数据集图鉴", "SQL 控制台", "五个数据集"):
            self.assertIn(word, html)

    def test_samples_served(self) -> None:
        with urllib.request.urlopen(self.base + "/samples/meta.json", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(len(data), 5)


if __name__ == "__main__":
    unittest.main()
