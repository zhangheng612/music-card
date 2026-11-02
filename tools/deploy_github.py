"""一次性把 MusicCard-Web 部署到 GitHub Pages。

流程：GitHub 设备授权（只需在浏览器点一次「Authorize」）→ 建公开仓库 →
上传文件 → 打开 Pages → 触发构建 → 打印站点地址。

用法：python tools/deploy_github.py [--repo music-card]
令牌只存在内存里，不写磁盘。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ID = "178c6fc778ccc68e1d6a"  # GitHub CLI 的公开 client id，用于设备授权
API = "https://api.github.com"
UA = "MusicCard-Deploy"
SKIP_DIRS = {"dist", "__pycache__", ".git"}
TOKEN_FILE = Path(os.environ.get("TEMP", ".")) / "musiccard_gh_token.txt"


def post_form(url: str, fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Accept": "application/json", "User-Agent": UA}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def api(token: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    url = path if path.startswith("https://") else API + path
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", UA)
    if body:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "ignore")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"message": raw[:300]}
        return error.code, data


def device_login() -> str:
    if TOKEN_FILE.exists():
        cached = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if cached:
            status, _ = api(cached, "GET", "/user")
            if status == 200:
                print("复用上次的授权。", flush=True)
                return cached

    start = post_form(
        "https://github.com/login/device/code",
        {"client_id": CLIENT_ID, "scope": "repo workflow"},
    )
    code = start["user_code"]
    device_code = start["device_code"]
    interval = max(5, int(start.get("interval", 5)))
    expires = int(start.get("expires_in", 900))

    print(f"AUTH_URL={start.get('verification_uri_complete') or start['verification_uri']}", flush=True)
    print(f"AUTH_CODE={code}", flush=True)
    print("等待你在浏览器里输入代码并授权……", flush=True)

    deadline = time.time() + expires
    while time.time() < deadline:
        time.sleep(interval)
        try:
            result = post_form(
                "https://github.com/login/oauth/access_token",
                {
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except Exception as error:  # 网络抖动，继续等
            print(f"  (轮询失败，重试：{error})", flush=True)
            continue
        token = result.get("access_token")
        if token:
            print("授权成功。", flush=True)
            TOKEN_FILE.write_text(token, encoding="utf-8")
            return token
        error = result.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        raise SystemExit(f"授权失败：{error} {result.get('error_description', '')}")
    raise SystemExit("授权超时，请重新运行。")


def collect_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.name.endswith((".pyc", ".DS_Store")):
            continue
        files.append((path, relative.as_posix()))
    return files


def create_repo(token: str, name: str) -> tuple[str, str]:
    status, data = api(
        token,
        "POST",
        "/user/repos",
        {
            "name": name,
            "private": False,
            "description": "Apple Music 朋友圈分享页（og:image 预排版）",
            "auto_init": False,
            "has_issues": False,
            "has_wiki": False,
        },
    )
    if status == 201:
        return data["owner"]["login"], data["name"]
    if status == 422:
        login = api(token, "GET", "/user")[1]["login"]
        check, repo = api(token, "GET", f"/repos/{login}/{name}")
        if check == 200:
            print(f"仓库 {login}/{name} 已存在，直接复用。", flush=True)
            return login, name
        for suffix in ("-web", "-pages", "-2"):
            alt = name + suffix
            status, data = api(
                token,
                "POST",
                "/user/repos",
                {"name": alt, "private": False, "auto_init": False},
            )
            if status == 201:
                print(f"仓库名 {name} 被占用，改用 {alt}。", flush=True)
                return data["owner"]["login"], data["name"]
        raise SystemExit(f"建仓库失败：{data}")
    raise SystemExit(f"建仓库失败：{status} {data}")


def upload(token: str, owner: str, repo: str, files: list[tuple[Path, str]]) -> None:
    """空仓库不能直接建 blob，所以先用 Contents API 提交 README 作为第一个 commit。"""
    status, ref = api(token, "GET", f"/repos/{owner}/{repo}/git/ref/heads/main")
    if status != 200:
        seed = next((item for item in files if item[1] == "README.md"), None)
        if seed is None:
            raise SystemExit("缺少 README.md，无法初始化仓库。")
        status, data = api(
            token,
            "PUT",
            f"/repos/{owner}/{repo}/contents/README.md",
            {
                "message": "Initial commit",
                "content": base64.b64encode(seed[0].read_bytes()).decode("ascii"),
            },
        )
        if status not in (200, 201):
            raise SystemExit(f"初始化仓库失败：{status} {data}")
        print("  已提交 README.md（初始化仓库）", flush=True)
        files = [item for item in files if item[1] != "README.md"]
        status, ref = api(token, "GET", f"/repos/{owner}/{repo}/git/ref/heads/main")
        if status != 200:
            raise SystemExit("读取分支失败。")

    base_commit = ref["object"]["sha"]
    status, base = api(token, "GET", f"/repos/{owner}/{repo}/git/commits/{base_commit}")
    if status != 200:
        raise SystemExit(f"读取基础提交失败：{status} {base}")
    base_tree = base["tree"]["sha"]

    tree = []
    for path, name in files:
        content = base64.b64encode(path.read_bytes()).decode("ascii")
        status, blob = api(
            token, "POST", f"/repos/{owner}/{repo}/git/blobs",
            {"content": content, "encoding": "base64"},
        )
        if status != 201:
            raise SystemExit(f"上传 {name} 失败：{status} {blob}")
        tree.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  已上传 {name}", flush=True)

    status, tree_data = api(
        token, "POST", f"/repos/{owner}/{repo}/git/trees",
        {"base_tree": base_tree, "tree": tree},
    )
    if status != 201:
        raise SystemExit(f"建目录失败：{status} {tree_data}")
    status, commit = api(
        token, "POST", f"/repos/{owner}/{repo}/git/commits",
        {
            "message": "Add Apple Music share pages (MusicCard Web)",
            "tree": tree_data["sha"],
            "parents": [base_commit],
        },
    )
    if status != 201:
        raise SystemExit(f"提交失败：{status} {commit}")
    status, updated = api(
        token, "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/main",
        {"sha": commit["sha"]},
    )
    if status != 200:
        raise SystemExit(f"更新分支失败：{status} {updated}")


def enable_pages(token: str, owner: str, repo: str) -> str:
    status, data = api(token, "POST", f"/repos/{owner}/{repo}/pages", {"build_type": "workflow"})
    if status in (201, 409):
        print("Pages 已开启（GitHub Actions 构建）。", flush=True)
    elif status == 422:
        status2, data2 = api(
            token, "PUT", f"/repos/{owner}/{repo}/pages", {"build_type": "workflow"}
        )
        if status2 not in (200, 204):
            print(f"Pages 设置返回 {status2}：{data2}（继续尝试构建）", flush=True)
    else:
        print(f"Pages 设置返回 {status}：{data}", flush=True)

    # 提交 push 会自己触发 workflow；等不到再手动 dispatch 一次。
    dispatched = False
    waited = 0
    for _ in range(90):
        time.sleep(10)
        waited += 10
        status, runs = api(
            token, "GET", f"/repos/{owner}/{repo}/actions/runs?per_page=10&branch=main"
        )
        if status != 200:
            continue
        run_list = runs.get("workflow_runs", [])
        if not run_list and not dispatched and waited >= 60:
            api(
                token,
                "POST",
                f"/repos/{owner}/{repo}/actions/workflows/build-pages.yml/dispatches",
                {"ref": "main"},
            )
            dispatched = True
            print("已手动触发构建。", flush=True)
            continue
        for run in run_list:
            if run.get("status") == "completed":
                if run.get("conclusion") == "success":
                    print(f"构建成功：{run['html_url']}", flush=True)
                    status, pages = api(token, "GET", f"/repos/{owner}/{repo}/pages")
                    if status == 200 and pages.get("html_url"):
                        return pages["html_url"]
                    return f"https://{owner}.github.io/{repo}/"
                raise SystemExit(
                    f"构建失败（{run.get('conclusion')}）：{run['html_url']}"
                )
    raise SystemExit("构建超时，请到仓库 Actions 页面查看。")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="music-card")
    args = parser.parse_args()

    files = collect_files()
    print(f"准备上传 {len(files)} 个文件。", flush=True)

    token = device_login()
    owner, repo = create_repo(token, args.repo)
    print(f"仓库：https://github.com/{owner}/{repo}", flush=True)

    upload(token, owner, repo, files)
    site = enable_pages(token, owner, repo)
    print(f"REPO_URL=https://github.com/{owner}/{repo}", flush=True)
    print(f"SITE_URL={site}", flush=True)
    # 保留授权缓存，方便这一轮反复改版重传；全部完成后手动删除
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
