#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 GitHub 拉取 5 个开源数据集的样本文件。

为什么不用 git clone / raw.githubusercontent：
  1. 本机 git/curl 的 TLS 走 Windows schannel，会报 SEC_E_NO_CREDENTIALS；
  2. raw.githubusercontent.com 在受限网络下经常不可达。
所以统一走 api.github.com 的 Git Data API（`git/trees` + `git/blobs`），
Python 自带 OpenSSL，凭据从环境变量或 Windows 凭据管理器取（见 gh_auth.py）。

幂等：manifest.json 里记录每个文件的 sha（Git blob）与 sha256，
再次运行时会跳过已下载且未变化的文件。支持 `--force` 强制重下。

用法：
  python scripts/fetch_data.py                 # 下载全部（约 50MB）
  python scripts/fetch_data.py --only words    # 只下某一个数据集
  python scripts/fetch_data.py --force         # 全部重下
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
MANIFEST = ROOT / "data" / "manifest.json"
API = "https://api.github.com"
UA = "open-data-atlas-fetcher/1.0"
LOCK = threading.Lock()

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_auth import resolve_token  # noqa: E402


# --------------------------------------------------------------------------
# 数据集清单：视频里的 5 个，全部用 GitHub API 核实过（★ 数为 2026-11-19 快照）
# --------------------------------------------------------------------------
SPECS = [
    {
        "id": "poetry",
        "name": "chinese-poetry 中华古典诗歌",
        "repo": "chinese-poetry/chinese-poetry",
        "video_ts": "00:08",
        "include": [
            "宋词/ci.song.*.json",
            "宋词/author.song.json",
            "全唐诗/poet.tang.*.json",
            "全唐诗/authors.tang.json",
        ],
        # 全唐诗共 320 个分片（~130MB），只取前 12 片做样本，脚本里明确标注
        "limit": {"全唐诗/poet.tang.*.json": 12},
        "license_files": [],
        "sample_note": "宋词全量（29 个分片）；全唐诗仅取 12/320 个分片；作者表取唐诗/宋词两份",
    },
    {
        "id": "words",
        "name": "word-atlas 词光·文字图集",
        "repo": "designfuchen/word-atlas",
        "video_ts": "00:25",
        "include": ["data/word-atlas.json", "CONTENT_LICENSE.md", "LICENSE"],
        "limit": {},
        "license_files": ["CONTENT_LICENSE.md", "LICENSE"],
        "sample_note": "全量 7,298 词条（word-atlas.json），代码 MIT / 数据 CC BY 4.0",
    },
    {
        "id": "exercises",
        "name": "exercises-dataset 健身动作库",
        "repo": "hasaneyldrm/exercises-dataset",
        "video_ts": "00:48",
        "include": ["data/exercises.json", "NOTICE.md", "LICENSE"],
        "limit": {},
        "license_files": ["NOTICE.md", "LICENSE"],
        "sample_note": "全量 1,324 个动作（不含 GIF/视频素材）",
    },
    {
        "id": "geo",
        "name": "countries-states-cities-database",
        "repo": "dr5hn/countries-states-cities-database",
        "video_ts": "01:08",
        "include": ["json/countries.json", "json/states.json", "json/countries+cities.json"],
        "limit": {},
        "license_files": [],
        "sample_note": "国家 250 + 州/省 5,000+ + 城市；不取 59MB 的合并大文件与 7.6GB 仓库历史",
    },
    {
        "id": "thewell",
        "name": "The Well 物理模拟数据集",
        "repo": "PolymathicAI/the_well",
        "video_ts": "01:32",
        "include": ["datasets/*/*.yaml", "README.md"],
        "limit": {},
        "license_files": [],
        "sample_note": "15TB 本体在 Hugging Face，仓库里只取 26 个数据集的元数据(yaml)与说明",
    },
]

SKIP_SUFFIX = (".gif", ".png", ".jpg", ".jpeg", ".mp4", ".webp")


# --------------------------------------------------------------------------
# GitHub API
# --------------------------------------------------------------------------
class GitHub:
    def __init__(self, token: str):
        self.token = token
        self.calls = 0

    def _request(self, url: str) -> dict:
        for attempt in range(4):
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": UA,
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    self.calls += 1
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                self.calls += 1
                body = exc.read().decode("utf-8", "ignore")[:300]
                remaining = exc.headers.get("X-RateLimit-Remaining")
                reset = exc.headers.get("X-RateLimit-Reset")
                if exc.code in (403, 429) and remaining == "0" and reset:
                    wait = max(1, int(reset) - int(time.time()) + 2)
                    print(f"  [rate-limit] 等待 {wait}s 后重试", flush=True)
                    time.sleep(min(wait, 120))
                    continue
                if exc.code >= 500 and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"HTTP {exc.code} {url} -> {body}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"网络错误 {url}: {exc}") from exc
        raise RuntimeError(f"重试耗尽: {url}")

    def default_branch(self, repo: str) -> str:
        return self._request(f"{API}/repos/{repo}")["default_branch"]

    def tree(self, repo: str, branch: str) -> dict[str, dict]:
        data = self._request(f"{API}/repos/{repo}/git/trees/{urllib.parse.quote(branch)}?recursive=1")
        out = {}
        for node in data.get("tree", []):
            if node.get("type") == "blob":
                out[node["path"]] = {"sha": node["sha"], "size": node.get("size", 0)}
        return out

    def blob(self, repo: str, sha: str) -> bytes:
        data = self._request(f"{API}/repos/{repo}/git/blobs/{sha}")
        if data.get("encoding") != "base64":
            raise RuntimeError(f"意外的 blob 编码: {data.get('encoding')}")
        return base64.b64decode(data["content"])


# --------------------------------------------------------------------------
# 清单工具
# --------------------------------------------------------------------------
def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"generated_at": None, "datasets": {}}


def save_manifest(manifest: dict) -> None:
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_files(spec: dict, tree: dict[str, dict]) -> list[str]:
    """按 include 的 glob 从仓库树里挑文件，limit 用来限制大仓库的分片数量。"""
    chosen: list[str] = []
    for pattern in spec["include"]:
        matched = sorted(p for p in tree if fnmatch.fnmatch(p, pattern))
        matched = [p for p in matched if not p.lower().endswith(SKIP_SUFFIX)]
        cap = spec.get("limit", {}).get(pattern)
        if cap:
            matched = matched[:cap]
        for p in matched:
            if p not in chosen:
                chosen.append(p)
    return chosen


def download_one(gh: GitHub, spec: dict, tree: dict[str, dict], path: str, force: bool) -> dict:
    node = tree[path]
    rel = Path("data") / "raw" / spec["id"] / path
    dest = ROOT / rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    existing = None
    if dest.exists() and not force:
        for f in load_manifest().get("datasets", {}).get(spec["id"], {}).get("files", []):
            if f["path"] == path and f["sha"] == node["sha"] and dest.stat().st_size == f["bytes"]:
                existing = f
    if existing:
        return {**existing, "skipped": True}

    payload = gh.blob(spec["repo"], node["sha"])
    dest.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "path": path,
        "rel": rel.as_posix(),
        "sha": node["sha"],
        "bytes": len(payload),
        "sha256": digest,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skipped": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="拉取 5 个开源数据集的样本文件")
    ap.add_argument("--only", action="append", help="只处理指定数据集 id（可重复）")
    ap.add_argument("--force", action="store_true", help="忽略已有文件，全部重下")
    args = ap.parse_args()

    specs = [s for s in SPECS if not args.only or s["id"] in args.only]
    if not specs:
        print("没有匹配的数据集，可选：" + ", ".join(s["id"] for s in SPECS))
        return 2

    token = resolve_token()
    gh = GitHub(token)
    manifest = load_manifest()
    manifest.setdefault("datasets", {})
    total_bytes = 0
    failures: list[str] = []

    for spec in specs:
        print(f"\n== {spec['id']}  {spec['repo']}", flush=True)
        try:
            meta = gh._request(f"{API}/repos/{spec['repo']}")
            branch = meta["default_branch"]
            tree = gh.tree(spec["repo"], branch)
            files = pick_files(spec, tree)
            if not files:
                raise RuntimeError("glob 没匹配到任何文件，检查路径是否变化")
            print(
                f"   branch={branch}  stars={meta['stargazers_count']}  "
                f"license={(meta.get('license') or {}).get('spdx_id')}  文件 {len(files)} 个",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {exc}", flush=True)
            failures.append(spec["id"])
            continue

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(download_one, gh, spec, tree, p, args.force): p for p in files}
            for fut in as_completed(futures):
                path = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    print(f"  [FAIL] {path}: {exc}", flush=True)
                    failures.append(path)
                    continue
                results.append(row)
                flag = "skip" if row["skipped"] else "ok  "
                print(f"  [{flag}] {row['bytes'] / 1024:9.1f} KB  {path}", flush=True)

        results.sort(key=lambda r: r["path"])
        subtotal = sum(r["bytes"] for r in results)
        total_bytes += subtotal
        manifest["datasets"][spec["id"]] = {
            "name": spec["name"],
            "repo": spec["repo"],
            "repo_url": f"https://github.com/{spec['repo']}",
            "branch": branch,
            "stars": meta["stargazers_count"],
            "license": (meta.get("license") or {}).get("spdx_id"),
            "description": meta.get("description"),
            "homepage": meta.get("homepage"),
            "pushed_at": meta.get("pushed_at"),
            "video_ts": spec["video_ts"],
            "sample_note": spec["sample_note"],
            "license_files": spec["license_files"],
            "bytes": subtotal,
            "files": results,
        }
        save_manifest(manifest)
        print(f"  小计 {subtotal / 1024 / 1024:.1f} MB", flush=True)

    save_manifest(manifest)
    print(f"\n总计 {total_bytes / 1024 / 1024:.1f} MB，manifest -> {MANIFEST.relative_to(ROOT)}")
    if failures:
        print("失败项：" + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
