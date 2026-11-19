#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收脚本：一次性检查「数据 + 数据库 + 服务 + 只读边界 + 样本完整性」。

用法：
  python scripts/verify.py                       # 检查本机 127.0.0.1:8790
  python scripts/verify.py --base https://x.x    # 检查已发布的站点（只跑 HTTP 部分）
  python scripts/verify.py --port 8791

退出码 0 = 全部通过；1 = 有失败项。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}{(' — ' + detail) if detail else ''}")
    return bool(ok)


def http_json(url: str, payload: dict | None = None, timeout: int = 20):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def local_checks() -> None:
    db = ROOT / "data" / "atlas.sqlite"
    manifest = ROOT / "data" / "manifest.json"
    samples = ROOT / "data" / "samples"

    check(db.exists(), "数据库存在", f"{db.stat().st_size / 1048576:.1f} MB" if db.exists() else "缺失")
    check(db.exists() and db.stat().st_size > 20 * 1024 * 1024, "数据库体积 > 20MB", "")

    # manifest SHA-256 完整性
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        bad, total = [], 0
        for dataset in m.get("datasets", {}).values():
            for f in dataset.get("files", []):
                total += 1
                path = ROOT / f["rel"]
                if not path.exists():
                    bad.append(f"{f['path']} 缺失")
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != f["sha256"]:
                    bad.append(f"{f['path']} sha256 不匹配")
        check(not bad and total > 0, "manifest 校验（sha256）", f"{total} 个文件" + (f"，{len(bad)} 个异常" if bad else ""))
        if bad:
            for b in bad[:5]:
                print("      -", b)
        kinds = set(m.get("datasets", {}).keys())
        check(kinds == {"poetry", "words", "exercises", "geo", "thewell"}, "manifest 覆盖 5 个数据集", ",".join(sorted(kinds)))
    else:
        check(False, "manifest 存在", "缺失")

    needed = ["meta.json", "poetry.json", "ci.json", "words.json", "exercises.json", "geo.json", "thewell.json"]
    missing = [n for n in needed if not (samples / n).exists()]
    check(not missing, "样本导出完整", f"{len(needed)} 个 JSON" + (f"，缺 {missing}" if missing else ""))


def http_checks(base: str) -> None:
    try:
        status, health = http_json(base + "/api/health")
    except Exception as exc:  # noqa: BLE001
        check(False, "服务可达", f"{base} -> {exc}")
        return
    check(status == 200 and health.get("ok"), "健康检查 /api/health", f"rows={health.get('rows')}")
    check(health.get("rows", 0) > 200_000, "入库行数 > 200,000", str(health.get("rows")))
    check(health.get("tables") == 12, "12 张表全部就绪", str(health.get("tables")))

    status, ds = http_json(base + "/api/datasets")
    ids = [d["dataset_id"] for d in ds.get("datasets", [])]
    check(status == 200 and ids == ["poetry", "words", "exercises", "geo", "thewell"], "5 个数据集且顺序正确", ",".join(ids))
    licenses_ok = all(d.get("license") for d in ds.get("datasets", []))
    check(licenses_ok, "每个数据集都有许可证字段")
    rows_ok = all(int(d.get("row_count") or 0) > 0 for d in ds.get("datasets", []))
    check(rows_ok, "每个数据集都有入库行数")

    # 三个跨表/聚合查询，顺带测延迟
    started = time.perf_counter()
    status, q1 = http_json(base + "/api/query", {"sql": "SELECT author, COUNT(*) c FROM poetry_ci GROUP BY author ORDER BY c DESC LIMIT 5"})
    first_ms = (time.perf_counter() - started) * 1000
    check(status == 200 and len(q1.get("rows", [])) == 5, "SQL：宋词作者排行", f"{first_ms:.0f} ms，第一名 {q1.get('rows', [[None]])[0][0]}")
    check(first_ms < 2000, "查询延迟 < 2s", f"{first_ms:.0f} ms")

    status, q2 = http_json(
        base + "/api/query",
        {"sql": "SELECT w.word, s.zh, e.en FROM words w JOIN words_senses s ON s.word_id=w.word_id AND s.idx=0 "
                "JOIN words_examples e ON e.word_id=w.word_id AND e.idx=0 WHERE w.word='library'"},
    )
    check(status == 200 and q2.get("rows") and "图书馆" in q2["rows"][0][1], "SQL：词条 JOIN 释义 + 例句")

    status, q3 = http_json(base + "/api/query", {"sql": "SELECT name, population FROM geo_countries ORDER BY population DESC LIMIT 3"})
    check(status == 200 and q3.get("rows") and q3["rows"][0][1] > 1_000_000_000, "SQL：人口最多的国家", str(q3.get("rows", [[None, None]])[0]))

    status, q4 = http_json(base + "/api/query", {"sql": "DELETE FROM words"})
    check(status == 400 and not q4.get("ok"), "写操作被拒绝（只读边界）")

    try:
        with urllib.request.urlopen(base + "/", timeout=15) as resp:
            html = resp.read().decode("utf-8")
        check(resp.status == 200 and all(k in html for k in ("开源数据集图鉴", "SQL 控制台")), "首页可打开")
    except Exception as exc:  # noqa: BLE001
        check(False, "首页可打开", str(exc))

    status, rows = http_json(base + "/api/rows?table=geo_cities&q=Beijing&limit=3")
    check(status == 200 and rows.get("total", 0) >= 1, "浏览接口：城市搜索 Beijing", str(rows.get("total")))


def site_checks(base: str) -> None:
    """检查已发布的 Pages 静态站（没有 /api，检查 HTML 与数据 JSON）。"""

    def get(path: str):
        req = urllib.request.Request(base + path, headers={"User-Agent": "open-data-atlas-verify"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()

    try:
        status, html = get("/")
        text = html.decode("utf-8", "replace")
        check(status == 200 and "开源数据集图鉴" in text, "Pages 首页可打开")
        check("SQL 练习" in text or "sql-practice" in text, "首页链到 SQL 练习")
    except Exception as exc:  # noqa: BLE001
        check(False, "Pages 首页可打开", str(exc))
        return

    try:
        status, raw = get("/data/meta.json")
        meta = json.loads(raw.decode("utf-8"))
        repos = [m["repo"] for m in meta]
        check(status == 200 and len(meta) == 5, "静态站数据：5 个数据集", ",".join(repos))
        check(
            set(repos)
            == {
                "chinese-poetry/chinese-poetry",
                "designfuchen/word-atlas",
                "hasaneyldrm/exercises-dataset",
                "dr5hn/countries-states-cities-database",
                "PolymathicAI/the_well",
            },
            "静态站数据：仓库名与视频一致",
        )
    except Exception as exc:  # noqa: BLE001
        check(False, "静态站数据 meta.json", str(exc))

    for page, keyword in (("/sql-practice.html", "SQL 练习"), ("/licenses.html", "ODbL"), ("/404.html", "404")):
        try:
            status, raw = get(page)
            check(status == 200 and keyword in raw.decode("utf-8", "replace"), f"静态页 {page}")
        except Exception as exc:  # noqa: BLE001
            check(False, f"静态页 {page}", str(exc))


def main() -> int:
    ap = argparse.ArgumentParser(description="开源数据集图鉴 · 验收")
    ap.add_argument("--base", help="服务地址，默认 http://127.0.0.1:<port>")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--http-only", action="store_true", help="只跑 HTTP 部分（检查远端站点时用）")
    ap.add_argument("--site", help="检查已发布的 GitHub Pages 站点，例如 https://xushan628.github.io/open-data-atlas")
    args = ap.parse_args()

    if args.site:
        print(f"== Pages 验收 · {time.strftime('%Y-%m-%d %H:%M:%S')} · {args.site}\n")
        site_checks(args.site.rstrip("/"))
        return _summary()

    base = (args.base or f"http://127.0.0.1:{args.port}").rstrip("/")

    print(f"== 验收开始 · {time.strftime('%Y-%m-%d %H:%M:%S')} · {base}\n")
    if not args.http_only:
        local_checks()
        print()
    http_checks(base)
    return _summary()


def _summary() -> int:
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n== 结果：{passed}/{total} 通过")
    if passed != total:
        print("失败项：")
        for ok, name, detail in RESULTS:
            if not ok:
                print(f"  - {name} {detail}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
