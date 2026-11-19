#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 data/raw 里的 5 个数据集归一化进一个 SQLite 数据仓库（data/atlas.sqlite）。

设计取舍：
  * 一个文件、一个连接、只读即可查询 —— 学习时随手 `sqlite3 data/atlas.sqlite`。
  * 表按数据集加前缀（poetry_/words_/ex_/geo_/well_），避免同名冲突，也方便出 SQL 练习题。
  * 词条做了进一步规范化（words_senses / words_examples），这样 5 个数据集能自然产生 JOIN 练习。
  * FTS5 可用则建全文索引，不可用自动跳过（LIKE 兜底）。
  * 构建是幂等的：每次先 DROP 再重建。产物同时导出 data/samples/*.json 给 GitHub Pages 用。

用法：
  python scripts/build_db.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "atlas.sqlite"
SAMPLES = ROOT / "data" / "samples"
MANIFEST = ROOT / "data" / "manifest.json"

SCHEMA = """
DROP TABLE IF EXISTS meta_datasets;

CREATE TABLE meta_datasets (
    dataset_id  TEXT PRIMARY KEY,
    sort_order  INTEGER,
    name        TEXT NOT NULL,
    repo        TEXT NOT NULL,
    repo_url    TEXT NOT NULL,
    stars       INTEGER,
    license     TEXT,
    description TEXT,
    homepage    TEXT,
    video_ts    TEXT,
    sample_note TEXT,
    bytes       INTEGER,
    row_count   INTEGER
);

CREATE TABLE poetry_tang (
    id         TEXT PRIMARY KEY,
    author     TEXT,
    title      TEXT,
    paragraphs TEXT,
    char_count INTEGER
);
CREATE INDEX idx_tang_author ON poetry_tang(author);

CREATE TABLE poetry_ci (
    ci_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    author     TEXT,
    rhythmic   TEXT,
    paragraphs TEXT,
    char_count INTEGER
);
CREATE INDEX idx_ci_author ON poetry_ci(author);

CREATE TABLE poetry_authors (
    name              TEXT,
    dynasty           TEXT,
    description       TEXT,
    short_description TEXT,
    PRIMARY KEY (name, dynasty)
);

CREATE TABLE words (
    word_id TEXT PRIMARY KEY,
    word    TEXT NOT NULL,
    uk      TEXT,
    us      TEXT,
    tip     TEXT,
    cefr    TEXT,
    topic   TEXT,
    note    TEXT
);
CREATE INDEX idx_words_word ON words(word);

CREATE TABLE words_senses (
    word_id TEXT NOT NULL,
    idx     INTEGER NOT NULL,
    pos     TEXT,
    zh      TEXT,
    en      TEXT,
    PRIMARY KEY (word_id, idx)
);

CREATE TABLE words_examples (
    word_id TEXT NOT NULL,
    idx     INTEGER NOT NULL,
    en      TEXT,
    zh      TEXT,
    PRIMARY KEY (word_id, idx)
);

CREATE TABLE words_collocations (
    word_id     TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    collocation TEXT,
    zh          TEXT,
    PRIMARY KEY (word_id, idx)
);

CREATE TABLE ex_exercises (
    exercise_id      TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    category         TEXT,
    body_part        TEXT,
    equipment        TEXT,
    muscle_group     TEXT,
    target           TEXT,
    secondary_muscles TEXT,
    instructions_en  TEXT,
    gif_url          TEXT,
    media_id         TEXT,
    attribution      TEXT
);
CREATE INDEX idx_ex_category ON ex_exercises(category);

CREATE TABLE geo_countries (
    id           INTEGER PRIMARY KEY,
    name         TEXT,
    iso2         TEXT,
    iso3         TEXT,
    capital      TEXT,
    region       TEXT,
    subregion    TEXT,
    population   INTEGER,
    currency     TEXT,
    phonecode    TEXT,
    area_sq_km   REAL,
    latitude     REAL,
    longitude    REAL
);

CREATE TABLE geo_states (
    id           INTEGER PRIMARY KEY,
    name         TEXT,
    country_id   INTEGER,
    country_code TEXT,
    country_name TEXT,
    iso2         TEXT,
    type         TEXT,
    latitude     REAL,
    longitude    REAL,
    timezone     TEXT
);
CREATE INDEX idx_states_country ON geo_states(country_id);

CREATE TABLE geo_cities (
    city_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    country_name TEXT,
    country_id   INTEGER
);
CREATE INDEX idx_cities_name ON geo_cities(name);

CREATE TABLE well_datasets (
    dataset     TEXT PRIMARY KEY,
    dims        INTEGER,
    resolution  TEXT,
    n_files     INTEGER,
    n_steps     INTEGER,
    n_traj      INTEGER,
    grid_type   TEXT,
    boundary    TEXT,
    fields      TEXT,
    scalars     TEXT
);
"""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def load_json(path: Path):
    with path.open(encoding="utf-8-sig") as fh:
        return json.load(fh)


def text_of(paragraphs) -> str:
    if isinstance(paragraphs, list):
        return "\n".join(str(p) for p in paragraphs)
    return str(paragraphs or "")


def mini_yaml(text: str) -> dict:
    """极简 YAML 解析：只支持本仓库用到的「映射 + 列表 + 三级缩进」子集。

    不引入 PyYAML，是为了保持"标准库就能跑"这一条。
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            continue  # 列表项由下面的 list 收集逻辑处理
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().strip("'\"")
        value = value.strip()
        if value:
            if value.startswith("[") and value.endswith("]"):
                parent[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            else:
                parent[key] = value.strip("'\"")
            continue
        child: dict = {}
        parent[key] = child
        stack.append((indent, child))
    return root


def to_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# 各数据集入库
# --------------------------------------------------------------------------
def load_poetry(con: sqlite3.Connection) -> int:
    base = RAW / "poetry"
    rows: list[tuple] = []
    for path in sorted(base.glob("全唐诗/poet.tang.*.json")):
        for item in load_json(path):
            body = text_of(item.get("paragraphs"))
            rows.append((item.get("id"), item.get("author"), item.get("title"), body, len(body)))
    con.executemany(
        "INSERT OR REPLACE INTO poetry_tang (id, author, title, paragraphs, char_count) VALUES (?,?,?,?,?)",
        rows,
    )

    ci_rows: list[tuple] = []
    for path in sorted(base.glob("宋词/ci.song.*.json")):
        for item in load_json(path):
            body = text_of(item.get("paragraphs"))
            ci_rows.append((item.get("author"), item.get("rhythmic"), body, len(body)))
    con.executemany(
        "INSERT INTO poetry_ci (author, rhythmic, paragraphs, char_count) VALUES (?,?,?,?)", ci_rows
    )

    author_rows: list[tuple] = []
    for name, dynasty in (("宋词/author.song.json", "宋"), ("全唐诗/authors.tang.json", "唐")):
        path = base / name
        if not path.exists():
            continue
        for item in load_json(path):
            author_rows.append(
                (
                    (item.get("name") or "").strip(),
                    dynasty,
                    item.get("description") or item.get("desc") or "",
                    item.get("short_description") or "",
                )
            )
    con.executemany(
        "INSERT OR REPLACE INTO poetry_authors (name, dynasty, description, short_description) VALUES (?,?,?,?)",
        author_rows,
    )
    return len(rows) + len(ci_rows) + len(author_rows)


def load_words(con: sqlite3.Connection) -> int:
    items = load_json(RAW / "words" / "data" / "word-atlas.json")
    words, senses, examples, colls = [], [], [], []
    for item in items:
        wid = item.get("id") or item.get("w")
        words.append(
            (
                wid,
                item.get("w"),
                item.get("uk"),
                item.get("us"),
                item.get("tip"),
                item.get("cefr"),
                item.get("topic"),
                item.get("note"),
            )
        )
        for i, s in enumerate(item.get("senses") or []):
            senses.append((wid, i, s.get("pos"), s.get("zh"), s.get("en")))
        for i, ex in enumerate(item.get("ex") or []):
            examples.append((wid, i, ex.get("en"), ex.get("zh")))
        for i, col in enumerate(item.get("col") or []):
            en, _, zh = str(col).partition("｜")
            colls.append((wid, i, en.strip(), zh.strip() or None))
    con.executemany(
        "INSERT OR REPLACE INTO words (word_id, word, uk, us, tip, cefr, topic, note) VALUES (?,?,?,?,?,?,?,?)",
        words,
    )
    con.executemany("INSERT OR REPLACE INTO words_senses VALUES (?,?,?,?,?)", senses)
    con.executemany("INSERT OR REPLACE INTO words_examples VALUES (?,?,?,?)", examples)
    con.executemany("INSERT OR REPLACE INTO words_collocations VALUES (?,?,?,?)", colls)
    return len(words)


def load_exercises(con: sqlite3.Connection) -> int:
    items = load_json(RAW / "exercises" / "data" / "exercises.json")
    rows = []
    for e in items:
        ins = e.get("instructions") or {}
        en = ins.get("en") if isinstance(ins, dict) else str(ins)
        sec = e.get("secondary_muscles")
        rows.append(
            (
                str(e.get("id")),
                e.get("name"),
                e.get("category"),
                e.get("body_part"),
                e.get("equipment"),
                e.get("muscle_group"),
                e.get("target"),
                ", ".join(sec) if isinstance(sec, list) else (sec or ""),
                en or "",
                e.get("gif_url"),
                e.get("media_id"),
                e.get("attribution"),
            )
        )
    con.executemany(
        "INSERT OR REPLACE INTO ex_exercises VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    return len(rows)


def load_geo(con: sqlite3.Connection) -> int:
    countries = load_json(RAW / "geo" / "json" / "countries.json")
    con.executemany(
        "INSERT OR REPLACE INTO geo_countries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                c.get("id"),
                c.get("name"),
                c.get("iso2"),
                c.get("iso3"),
                c.get("capital"),
                c.get("region"),
                c.get("subregion"),
                c.get("population"),
                c.get("currency"),
                c.get("phonecode"),
                c.get("area_sq_km"),
                c.get("latitude"),
                c.get("longitude"),
            )
            for c in countries
        ],
    )

    states = load_json(RAW / "geo" / "json" / "states.json")
    con.executemany(
        "INSERT OR REPLACE INTO geo_states VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                s.get("id"),
                s.get("name"),
                s.get("country_id"),
                s.get("country_code"),
                s.get("country_name"),
                s.get("iso2"),
                s.get("type"),
                s.get("latitude"),
                s.get("longitude"),
                s.get("timezone"),
            )
            for s in states
        ],
    )

    cities = load_json(RAW / "geo" / "json" / "countries+cities.json")
    id_by_name = {c.get("name"): c.get("id") for c in countries}
    city_rows = []
    for entry in cities:
        cname = entry.get("name")
        cid = id_by_name.get(cname) or entry.get("id")
        for city in entry.get("cities") or []:
            city_rows.append((city, cname, cid))
    con.executemany("INSERT INTO geo_cities (name, country_name, country_id) VALUES (?,?,?)", city_rows)
    return len(countries) + len(states) + len(city_rows)


def load_thewell(con: sqlite3.Connection) -> int:
    base = RAW / "thewell" / "datasets"
    rows = []
    for yml in sorted(base.glob("*/*.yaml")):
        if yml.name == "stats.yaml":
            continue
        data = mini_yaml(yml.read_text(encoding="utf-8-sig"))
        fields: list[str] = []
        for value in (data.get("field_names") or {}).values():
            if isinstance(value, list):
                fields.extend(v for v in value if v)
        scalars = data.get("scalar_names") or []
        resolution = data.get("spatial_resolution")
        steps = data.get("n_steps_per_trajectory")
        traj = data.get("n_trajectories_per_file")
        rows.append(
            (
                str(data.get("dataset_name") or yml.stem),
                to_int(data.get("n_spatial_dims")),
                ", ".join(str(x) for x in resolution) if isinstance(resolution, list) else str(resolution or ""),
                to_int(data.get("n_files")),
                to_int(steps[0]) if isinstance(steps, list) and steps else to_int(steps),
                to_int(traj[0]) if isinstance(traj, list) and traj else to_int(traj),
                str(data.get("grid_type") or ""),
                ", ".join(str(x) for x in (data.get("boundary_condition_types") or []))
                if isinstance(data.get("boundary_condition_types"), list)
                else str(data.get("boundary_condition_types") or ""),
                ", ".join(fields),
                ", ".join(str(x) for x in scalars) if isinstance(scalars, list) else str(scalars or ""),
            )
        )
    con.executemany("INSERT OR REPLACE INTO well_datasets VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


# --------------------------------------------------------------------------
# 全文索引 / 元数据 / 样本导出
# --------------------------------------------------------------------------
def try_fts(con: sqlite3.Connection) -> bool:
    try:
        con.executescript(
            """
            DROP TABLE IF EXISTS fts_words;
            DROP TABLE IF EXISTS fts_poetry;
            DROP TABLE IF EXISTS fts_exercises;
            CREATE VIRTUAL TABLE fts_words USING fts5(word, zh_senses, tokenize='unicode61');
            CREATE VIRTUAL TABLE fts_poetry USING fts5(author, title, body, tokenize='unicode61');
            CREATE VIRTUAL TABLE fts_exercises USING fts5(name, target, equipment, tokenize='unicode61');
            """
        )
        con.execute(
            """INSERT INTO fts_words (word, zh_senses)
               SELECT w.word, COALESCE(GROUP_CONCAT(s.zh, ' / '), '')
               FROM words w LEFT JOIN words_senses s ON s.word_id = w.word_id
               GROUP BY w.word_id"""
        )
        con.execute(
            """INSERT INTO fts_poetry (author, title, body)
               SELECT author, title, paragraphs FROM poetry_tang"""
        )
        con.execute(
            """INSERT INTO fts_poetry (author, title, body)
               SELECT author, rhythmic, paragraphs FROM poetry_ci"""
        )
        con.execute(
            """INSERT INTO fts_exercises (name, target, equipment)
               SELECT name, COALESCE(target, ''), COALESCE(equipment, '') FROM ex_exercises"""
        )
        con.commit()
        return True
    except sqlite3.OperationalError as exc:
        print(f"  [warn] FTS5 不可用，跳过全文索引：{exc}")
        return False


def fill_meta(con: sqlite3.Connection, manifest: dict) -> None:
    # 展示顺序 = 抖音视频里的出场顺序（不要依赖 manifest 的插入顺序，它是按抓取成功先后写的）
    order = {"poetry": 1, "words": 2, "exercises": 3, "geo": 4, "thewell": 5}
    counts = {
        "poetry": con.execute(
            "SELECT (SELECT COUNT(*) FROM poetry_tang) + (SELECT COUNT(*) FROM poetry_ci) + (SELECT COUNT(*) FROM poetry_authors)"
        ).fetchone()[0],
        "words": con.execute("SELECT COUNT(*) FROM words").fetchone()[0],
        "exercises": con.execute("SELECT COUNT(*) FROM ex_exercises").fetchone()[0],
        "geo": con.execute(
            "SELECT (SELECT COUNT(*) FROM geo_countries) + (SELECT COUNT(*) FROM geo_states) + (SELECT COUNT(*) FROM geo_cities)"
        ).fetchone()[0],
        "thewell": con.execute("SELECT COUNT(*) FROM well_datasets").fetchone()[0],
    }
    for dataset_id, meta in manifest.get("datasets", {}).items():
        con.execute(
            """INSERT OR REPLACE INTO meta_datasets
               (dataset_id, sort_order, name, repo, repo_url, stars, license, description, homepage, video_ts, sample_note, bytes, row_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dataset_id,
                order.get(dataset_id, 99),
                meta.get("name"),
                meta.get("repo"),
                meta.get("repo_url"),
                meta.get("stars"),
                meta.get("license"),
                meta.get("description"),
                meta.get("homepage"),
                meta.get("video_ts"),
                meta.get("sample_note"),
                meta.get("bytes"),
                counts.get(dataset_id, 0),
            ),
        )


def export_samples(con: sqlite3.Connection) -> list[str]:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    def dump(name: str, rows) -> None:
        path = SAMPLES / name
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(name)

    dump(
        "meta.json",
        [
            dict(zip([c[0] for c in con.execute("SELECT * FROM meta_datasets LIMIT 0").description], r))
            for r in con.execute("SELECT * FROM meta_datasets ORDER BY sort_order")
        ],
    )
    dump(
        "poetry.json",
        [
            {"author": r[0], "title": r[1], "paragraphs": r[2][:120]}
            for r in con.execute(
                "SELECT author, title, paragraphs FROM poetry_tang ORDER BY RANDOM() LIMIT 60"
            )
        ],
    )
    dump(
        "ci.json",
        [
            {"author": r[0], "rhythmic": r[1], "paragraphs": r[2][:120]}
            for r in con.execute(
                "SELECT author, rhythmic, paragraphs FROM poetry_ci ORDER BY RANDOM() LIMIT 60"
            )
        ],
    )
    dump(
        "words.json",
        [
            {"word": r[0], "uk": r[1], "us": r[2], "cefr": r[3], "topic": r[4], "zh": r[5]}
            for r in con.execute(
                """SELECT w.word, w.uk, w.us, w.cefr, w.topic, s.zh
                   FROM words w LEFT JOIN words_senses s ON s.word_id = w.word_id AND s.idx = 0
                   ORDER BY RANDOM() LIMIT 120"""
            )
        ],
    )
    dump(
        "exercises.json",
        [
            {"name": r[0], "category": r[1], "equipment": r[2], "target": r[3]}
            for r in con.execute(
                "SELECT name, category, equipment, target FROM ex_exercises ORDER BY RANDOM() LIMIT 60"
            )
        ],
    )
    dump(
        "geo.json",
        [
            {"city": r[0], "country": r[1]}
            for r in con.execute(
                "SELECT name, country_name FROM geo_cities ORDER BY RANDOM() LIMIT 120"
            )
        ],
    )
    dump(
        "thewell.json",
        [
            {"dataset": r[0], "dims": r[1], "resolution": r[2], "fields": r[3]}
            for r in con.execute("SELECT dataset, dims, resolution, fields FROM well_datasets ORDER BY dataset")
        ],
    )
    return written


def main() -> int:
    if not MANIFEST.exists():
        print("缺少 data/manifest.json，请先运行 python scripts/fetch_data.py")
        return 2
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=OFF")
    con.executescript(SCHEMA)

    steps = [
        ("poetry 唐宋诗词", load_poetry),
        ("words 词光词库", load_words),
        ("exercises 健身动作", load_exercises),
        ("geo 国家州城市", load_geo),
        ("thewell 物理模拟元数据", load_thewell),
    ]
    total = 0
    for label, fn in steps:
        n = fn(con)
        total += n
        print(f"  {label:24s} 写入 {n:>7,} 行")
    fts_ok = try_fts(con)
    fill_meta(con, manifest)
    con.commit()

    counts = {
        "poetry_tang": con.execute("SELECT COUNT(*) FROM poetry_tang").fetchone()[0],
        "poetry_ci": con.execute("SELECT COUNT(*) FROM poetry_ci").fetchone()[0],
        "poetry_authors": con.execute("SELECT COUNT(*) FROM poetry_authors").fetchone()[0],
        "words": con.execute("SELECT COUNT(*) FROM words").fetchone()[0],
        "words_senses": con.execute("SELECT COUNT(*) FROM words_senses").fetchone()[0],
        "words_examples": con.execute("SELECT COUNT(*) FROM words_examples").fetchone()[0],
        "words_collocations": con.execute("SELECT COUNT(*) FROM words_collocations").fetchone()[0],
        "ex_exercises": con.execute("SELECT COUNT(*) FROM ex_exercises").fetchone()[0],
        "geo_countries": con.execute("SELECT COUNT(*) FROM geo_countries").fetchone()[0],
        "geo_states": con.execute("SELECT COUNT(*) FROM geo_states").fetchone()[0],
        "geo_cities": con.execute("SELECT COUNT(*) FROM geo_cities").fetchone()[0],
        "well_datasets": con.execute("SELECT COUNT(*) FROM well_datasets").fetchone()[0],
    }
    samples = export_samples(con)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()
    con.close()

    size_mb = DB_PATH.stat().st_size / 1024 / 1024
    print(f"\n数据库：{DB_PATH.relative_to(ROOT)}  {size_mb:.1f} MB  FTS5={'yes' if fts_ok else 'no'}")
    for k, v in counts.items():
        print(f"  {k:20s} {v:>8,}")
    print(f"样本导出：{', '.join(samples)} -> {SAMPLES.relative_to(ROOT)}")
    print(f"总行数 {total:,} / 构建时间 {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
