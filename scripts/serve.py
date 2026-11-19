#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开源数据集图鉴 · 本地 Web 服务（只用标准库）。

功能
  /                     单页应用（web/index.html）
  /api/health           健康检查
  /api/datasets         5 个数据集元信息 + 表清单
  /api/tables           可浏览表的列定义（前端用）
  /api/rows             分页浏览/搜索：?table=&q=&page=&limit=
  /api/query            只读 SQL：POST {"sql": "select ..."}
  /samples/*.json       导出的样本（Pages 站也复用同一份）

安全边界（这是"能随便查"和"能被查坏"的分界线）
  * 数据库用 `file:...?mode=ro` 打开，物理上不可写；
  * 只允许 SELECT / WITH / EXPLAIN 开头的单条语句；
  * 命中 ATTACH/PRAGMA/INSERT/... 等关键字直接拒绝；
  * 结果包一层 `SELECT * FROM (...) LIMIT 500`，防止把 17 万行城市全拖出来；
  * `set_progress_handler` 限制 VM 步数，超时/爆炸查询自动中断。
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "atlas.sqlite"
WEB = ROOT / "web"
SAMPLES = ROOT / "data" / "samples"
PORT = int(os.environ.get("ATLAS_PORT", "8790"))
HOST = os.environ.get("ATLAS_HOST", "0.0.0.0")
MAX_ROWS = 500
SQL_TIMEOUT_OPS = 40_000_000  # 约等于 2~5 秒
STARTED_AT = time.time()

# 表和它们可搜索的列（前端下拉框、后端 LIKE 都靠这张表）
TABLES: dict[str, dict] = {
    "poetry_tang": {"label": "唐诗（全唐诗 12 个分片）", "dataset": "poetry", "search": ["author", "title", "paragraphs"]},
    "poetry_ci": {"label": "宋词（全量 29 个分片）", "dataset": "poetry", "search": ["author", "rhythmic", "paragraphs"]},
    "poetry_authors": {"label": "唐宋诗人作者表", "dataset": "poetry", "search": ["name", "description"]},
    "words": {"label": "词光 · 雅思词条", "dataset": "words", "search": ["word", "uk", "us", "tip", "topic", "cefr"]},
    "words_senses": {"label": "词义（中英释义）", "dataset": "words", "search": ["zh", "en"]},
    "words_examples": {"label": "例句（中英对照）", "dataset": "words", "search": ["en", "zh"]},
    "words_collocations": {"label": "固定搭配", "dataset": "words", "search": ["collocation", "zh"]},
    "ex_exercises": {"label": "健身动作 1,324 个", "dataset": "exercises", "search": ["name", "category", "equipment", "target", "instructions_en"]},
    "geo_countries": {"label": "国家 250", "dataset": "geo", "search": ["name", "capital", "region", "subregion"]},
    "geo_states": {"label": "州/省 5,327", "dataset": "geo", "search": ["name", "country_name", "type"]},
    "geo_cities": {"label": "城市 171,267", "dataset": "geo", "search": ["name", "country_name"]},
    "well_datasets": {"label": "The Well 物理模拟数据集", "dataset": "thewell", "search": ["dataset", "fields", "grid_type"]},
}

# 示例查询：放进 SQL 控制台，也是 docs 里 SQL 练习的答案蓝本
EXAMPLES = [
    ("唐诗里出现最多的 10 位诗人", "SELECT author, COUNT(*) AS poems FROM poetry_tang GROUP BY author ORDER BY poems DESC LIMIT 10"),
    ("苏轼在宋词里写了多少首", "SELECT COUNT(*) AS n FROM poetry_ci WHERE author = '苏轼'"),
    ("C1 及以上难度的单词有多少", "SELECT cefr, COUNT(*) AS n FROM words WHERE cefr IN ('C1','C2') GROUP BY cefr"),
    ("一个词的释义 + 例句（JOIN）", "SELECT w.word, s.zh AS 释义, e.en AS 例句 FROM words w JOIN words_senses s ON s.word_id = w.word_id AND s.idx = 0 JOIN words_examples e ON e.word_id = w.word_id AND e.idx = 0 WHERE w.word = 'library'"),
    ("按器材统计健身动作", "SELECT equipment, COUNT(*) AS n FROM ex_exercises GROUP BY equipment ORDER BY n DESC LIMIT 10"),
    ("人口最多的 10 个国家", "SELECT name, population FROM geo_countries ORDER BY population DESC LIMIT 10"),
    ("中国有多少个州/省级行政区入库", "SELECT COUNT(*) AS n FROM geo_states WHERE country_name = 'China'"),
]


class ReadOnly:
    """只读 SQL 网关：物理只读 + 语法白名单 + 行数/步数上限。"""

    FORBIDDEN = re.compile(
        r"\b(attach|detach|pragma|insert|update|delete|drop|create|alter|replace|vacuum|reindex|"
        r"begin|commit|rollback|savepoint|release|analyze|trigger|view)\b",
        re.IGNORECASE,
    )
    ALLOWED_START = re.compile(r"^\s*(select|with|explain)\b", re.IGNORECASE)

    def __init__(self, path: Path):
        self.uri = f"file:{path.as_posix()}?mode=ro"
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.uri, uri=True, timeout=5.0, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only = ON")
            self._local.con = con
        return con

    @staticmethod
    def _strip_comments(sql: str) -> str:
        sql = re.sub(r"--[^\n]*", " ", sql)
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        return sql.strip()

    def check(self, sql: str) -> str:
        cleaned = self._strip_comments(sql)
        if not cleaned:
            raise ValueError("SQL 不能为空")
        if not self.ALLOWED_START.match(cleaned):
            raise ValueError("只允许 SELECT / WITH / EXPLAIN 查询")
        body = cleaned.rstrip(";")
        if ";" in body:
            raise ValueError("一次只能执行一条语句")
        if self.FORBIDDEN.search(body):
            raise ValueError("检测到写操作或危险关键字，已拒绝")
        return body

    def run(self, sql: str, cap: int = MAX_ROWS) -> dict:
        body = self.check(sql)
        wrapped = f"SELECT * FROM (\n{body}\n) LIMIT {cap}"
        con = self.connection()
        started = time.perf_counter()
        steps = {"n": 0}

        def progress() -> int:
            steps["n"] += 1_000_000
            return 1 if steps["n"] > SQL_TIMEOUT_OPS else 0

        con.set_progress_handler(progress, 1_000_000)
        try:
            cur = con.execute(wrapped)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [list(r) for r in cur.fetchmany(cap)]
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise ValueError("查询太慢，已被中断（请加 WHERE 或 LIMIT）") from exc
            raise ValueError(f"SQL 错误：{exc}") from exc
        finally:
            con.set_progress_handler(None, 0)
        return {
            "columns": columns,
            "rows": rows,
            "rowCount": len(rows),
            "truncated": len(rows) >= cap,
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }


GATE = ReadOnly(DB_PATH)


def table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]


def datasets_payload() -> dict:
    con = GATE.connection()
    meta = [dict(r) for r in con.execute("SELECT * FROM meta_datasets ORDER BY sort_order")]
    tables = []
    for name, cfg in TABLES.items():
        try:
            count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            columns = table_columns(con, name)
        except sqlite3.Error:
            continue
        tables.append({"name": name, "label": cfg["label"], "dataset": cfg["dataset"], "rows": count, "columns": columns, "search": cfg["search"]})
    for item in meta:
        item["tables"] = [t["name"] for t in tables if t["dataset"] == item["dataset_id"]]
    return {"datasets": meta, "tables": tables, "examples": [{"title": t, "sql": s} for t, s in EXAMPLES]}


def health_payload() -> dict:
    con = GATE.connection()
    total = 0
    for name in TABLES:
        try:
            total += con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error:
            pass
    return {
        "ok": DB_PATH.exists(),
        "service": "open-data-atlas",
        "db": DB_PATH.name,
        "dbBytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "rows": total,
        "datasets": 5,
        "tables": len(TABLES),
        "uptimeSec": round(time.time() - STARTED_AT, 1),
        "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def rows_payload(params: dict) -> dict:
    table = (params.get("table") or ["poetry_tang"])[0]
    query = (params.get("q") or [""])[0].strip()
    page = max(1, int((params.get("page") or ["1"])[0]))
    limit = min(200, max(1, int((params.get("limit") or ["20"])[0])))
    if table not in TABLES:
        raise ValueError(f"未知表：{table}")
    con = GATE.connection()
    where, args = "", []
    if query:
        cols = TABLES[table]["search"]
        where = " WHERE " + " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in cols)
        args = [f"%{query}%"] * len(cols)
    total = con.execute(f'SELECT COUNT(*) FROM "{table}"{where}', args).fetchone()[0]
    cols = table_columns(con, table)
    order = "rowid" if "rowid" in [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')] else cols[0]
    sql = f'SELECT * FROM "{table}"{where} ORDER BY "{order}" LIMIT {limit} OFFSET {(page - 1) * limit}'
    rows = [list(r) for r in con.execute(sql, args)]
    return {
        "table": table,
        "columns": cols,
        "rows": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "query": query,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenDataAtlas/1.0"

    def log_message(self, fmt: str, *args) -> None:  # 安静一点，日志交给上层重定向
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------------- 基础响应 ----------------
    def _send(self, code: int, body: bytes, content_type: str = "application/json; charset=utf-8", extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _error(self, code: int, message: str) -> None:
        self._json({"ok": False, "error": message}, code)

    def _static(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._error(404, "not found")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # ---------------- 路由 ----------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                self._static(WEB / "index.html")
            elif route.startswith("/static/"):
                self._static(WEB / route[len("/static/"):])
            elif route.startswith("/samples/"):
                self._static(SAMPLES / Path(route[len("/samples/"):]).name)
            elif route == "/api/health":
                self._json(health_payload())
            elif route == "/api/datasets":
                self._json(datasets_payload())
            elif route == "/api/rows":
                self._json(rows_payload(params))
            elif route == "/api/query":
                sql = (params.get("sql") or [""])[0]
                self._json({"ok": True, **GATE.run(sql)})
            elif route == "/favicon.ico":
                self._send(204, b"")
            else:
                self._error(404, f"未知路径 {route}")
        except ValueError as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"内部错误：{exc}")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/query":
            self._error(404, "未知路径")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            sql = (payload.get("sql") or "").strip()
            self._json({"ok": True, **GATE.run(sql)})
        except ValueError as exc:
            self._error(400, str(exc))
        except json.JSONDecodeError:
            self._error(400, "请求体不是合法 JSON")
        except Exception as exc:  # noqa: BLE001
            self._error(500, f"内部错误：{exc}")


def main() -> int:
    if not DB_PATH.exists():
        print(f"找不到 {DB_PATH}，请先运行 python scripts/build_db.py")
        return 2
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"开源数据集图鉴已启动：http://127.0.0.1:{PORT}  (绑定 {HOST}:{PORT})")
    print(f"数据库 {DB_PATH.name} · {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
