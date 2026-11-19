#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本目录发布到 GitHub，并开启 Pages（docs/ 目录）。

为什么不用 git push：本机 git/curl 的 TLS 走 Windows schannel，会报
`AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS`；Python 自带 OpenSSL 能出网，
于是走 GitHub 的 Git Data API（blob → tree → commit → ref）直接推。

凭据：环境变量 GITHUB_TOKEN/GH_TOKEN → Windows 凭据管理器
target = "GitHub - https://api.github.com/<login>"（见 gh_auth.py）。

用法：
  python scripts/publish_github.py --dry-run      # 先看看会推哪些文件
  python scripts/publish_github.py                # 推送 + 开 Pages
  python scripts/publish_github.py --repo demo-x --private
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
UA = "open-data-atlas-publisher/1.0"
BRANCH = "main"

# 这些目录/文件不进公开仓库（体积或运行产物）
SKIP_PARTS = {"data/raw", "logs", "__pycache__", ".git", "node_modules", ".venv"}
SKIP_FILES = {"data/atlas.sqlite", "data/atlas.sqlite-wal", "data/atlas.sqlite-shm"}

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh_auth import resolve_token  # noqa: E402


class GitHub:
    def __init__(self, token: str):
        self.token = token
        self.calls = 0

    def call(self, method: str, path: str, payload: dict | None = None, retries: int = 3):
        url = path if path.startswith("http") else API + path
        body = json.dumps(payload).encode() if payload is not None else None
        for attempt in range(retries):
            req = urllib.request.Request(
                url,
                data=body,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": UA,
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    self.calls += 1
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                if exc.code in (500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"{method} {path} → HTTP {exc.code}: {detail}") from None
            except Exception as exc:  # noqa: BLE001
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise RuntimeError(f"{method} {path} → {type(exc).__name__}: {exc}") from None
        raise RuntimeError("unreachable")


def iter_files(base: Path):
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        rel_posix = rel.as_posix()
        if any(rel_posix == p or rel_posix.startswith(p + "/") for p in SKIP_PARTS):
            continue
        if rel_posix in SKIP_FILES:
            continue
        if path.suffix in {".pyc", ".log"}:
            continue
        yield path, rel_posix


def ensure_repo(gh: GitHub, owner: str, repo: str, *, private: bool, description: str, homepage: str) -> None:
    try:
        info = gh.call("GET", f"/repos/{owner}/{repo}")
        print(f"· 仓库已存在：{info['html_url']}（{info['visibility']}）")
        return
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    print(f"· 创建仓库 {owner}/{repo} …")
    gh.call(
        "POST",
        "/user/repos",
        {
            "name": repo,
            "description": description,
            "homepage": homepage,
            "private": private,
            "has_issues": True,
            "has_wiki": False,
            "has_projects": False,
            "auto_init": True,  # 空仓库建 blob 会 409，必须先初始化
        },
    )


def ensure_initial_commit(gh: GitHub, owner: str, repo: str, branch: str) -> None:
    try:
        gh.call("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
        return
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc) and "HTTP 409" not in str(exc):
            raise
    print("· 仓库为空，先建初始提交 …")
    gh.call(
        "PUT",
        f"/repos/{owner}/{repo}/contents/README.md",
        {
            "message": "chore: 初始化仓库",
            "content": base64.b64encode("# init\n".encode()).decode(),
            "branch": branch,
        },
    )


def enable_pages(gh: GitHub, owner: str, repo: str, branch: str) -> str:
    url = f"https://{owner}.github.io/{repo}/"
    try:
        gh.call(
            "POST",
            f"/repos/{owner}/{repo}/pages",
            {"source": {"branch": branch, "path": "/docs"}, "build_type": "legacy"},
        )
        print(f"· 已开启 Pages（{branch} /docs）")
    except RuntimeError as exc:
        if "HTTP 409" in str(exc) or "HTTP 422" in str(exc):
            try:
                gh.call(
                    "PUT",
                    f"/repos/{owner}/{repo}/pages",
                    {"source": {"branch": branch, "path": "/docs"}},
                )
                print("· Pages 已存在，已更新 source")
            except RuntimeError as exc2:
                print(f"  [warn] 更新 Pages 失败：{exc2}")
        else:
            print(f"  [warn] 开启 Pages 失败：{exc}")
    return url


def wait_pages(url: str, timeout: int = 180) -> bool:
    print(f"· 等待 Pages 构建：{url}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="发布开源数据集图鉴到 GitHub Pages")
    ap.add_argument("--login", default="xushan628")
    ap.add_argument("--owner", default=None, help="默认等于 --login")
    ap.add_argument("--repo", default="open-data-atlas")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--message", default=None)
    ap.add_argument("--description", default="开源数据集图鉴：抖音视频里 5 个高质量 GitHub 数据集，落地成可查询的 SQLite 数据仓库")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-pages", action="store_true")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    owner = args.owner or args.login
    message = args.message or f"feat: 开源数据集图鉴（5 个数据集 → SQLite + 本地 Web）（{time.strftime('%Y-%m-%d')}）"
    homepage = f"https://{owner}.github.io/{args.repo}/"

    files = list(iter_files(ROOT))
    total = sum(p.stat().st_size for p, _ in files)
    print(f"· 待推送 {len(files)} 个文件，{total / 1024:.1f} KB")
    for _, rel in files:
        print("   ", rel)

    if args.dry_run:
        print("· dry-run 结束（未做任何修改）")
        return 0

    gh = GitHub(resolve_token(args.login))
    ensure_repo(gh, owner, args.repo, private=args.private, description=args.description, homepage=homepage)
    ensure_initial_commit(gh, owner, args.repo, args.branch)

    print("· 上传 blob …")

    def upload(item):
        path, rel = item
        data = path.read_bytes()
        res = gh.call(
            "POST",
            f"/repos/{owner}/{args.repo}/git/blobs",
            {"content": base64.b64encode(data).decode(), "encoding": "base64"},
        )
        return {"path": rel, "mode": "100644", "type": "blob", "sha": res["sha"]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        tree = list(pool.map(upload, files))
    print(f"  完成 {len(tree)} 个")

    new_tree = gh.call("POST", f"/repos/{owner}/{args.repo}/git/trees", {"tree": tree})
    parents = []
    try:
        ref = gh.call("GET", f"/repos/{owner}/{args.repo}/git/ref/heads/{args.branch}")
        parents = [ref["object"]["sha"]]
        print(f"  基于现有提交 {parents[0][:7]}")
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        print("  首次提交（无父提交）")

    commit = gh.call(
        "POST",
        f"/repos/{owner}/{args.repo}/git/commits",
        {"message": message, "tree": new_tree["sha"], "parents": parents},
    )
    if parents:
        gh.call("PATCH", f"/repos/{owner}/{args.repo}/git/refs/heads/{args.branch}", {"sha": commit["sha"], "force": True})
    else:
        gh.call("POST", f"/repos/{owner}/{args.repo}/git/refs", {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})

    print(f"\n✅ 推送完成：https://github.com/{owner}/{args.repo}/commit/{commit['sha']}")
    print(f"   提交 {commit['sha'][:7]} · 文件 {len(tree)} · API 调用 {gh.calls} 次")

    if not args.no_pages:
        url = enable_pages(gh, owner, args.repo, args.branch)
        ok = wait_pages(url)
        print(f"   站点：{url}  {'（已可访问）' if ok else '（构建中，稍等 1~2 分钟）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
