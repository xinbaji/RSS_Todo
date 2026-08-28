"""通过 GitHub API 推送本地 git 提交（github.com 直连被限时备用通道）。

用法: python push_via_api.py [--repo xinbaji/RSS_Todo] [--branch main]
依赖环境变量 GITHUB_TOKEN（或 wincred 自动取）。
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

REPO = "xinbaji/RSS_Todo"
BRANCH = "main"


def token_from_wincred() -> str:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    out = subprocess.run(
        ["git", "-c", "credential.helper=wincred", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.lower().startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def api(method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "push-via-api")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        raise RuntimeError(f"API {method} {path} -> {e.code}: {raw[:300]}")


def main() -> int:
    token = token_from_wincred()
    if not token:
        print("无法获取 GitHub token")
        return 1
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    msg = subprocess.run(["git", "log", "-1", "--pretty=%B"], capture_output=True, text=True).stdout.strip()
    print(f"本地 HEAD: {head[:12]} 消息: {msg[:40]}")

    # 0) 空仓库先初始化基线 ref（GitHub 空仓库不允许 git data API，用 contents API 解锁）
    try:
        api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}", token=token)
    except RuntimeError:
        api("PUT", f"/repos/{REPO}/contents/.gitkeep",
            {"message": "init", "content": base64.b64encode(b"").decode()}, token)
        print("已用 .gitkeep 初始化空仓库")

    # 1) 收集所有文件内容（git show :path 保证与暂存一致）
    entries = subprocess.run(["git", "ls-tree", "-r", "HEAD", "--name-only"],
                             capture_output=True, text=True).stdout.splitlines()
    blobs = {}
    for p in entries:
        content = subprocess.run(["git", "show", f"HEAD:{p}"],
                                 capture_output=True).stdout
        r = api("POST", "/repos/%s/git/blobs" % REPO,
                {"content": base64.b64encode(content).decode(), "encoding": "base64"}, token)
        blobs[p] = r["sha"]
        print(f"  blob {r['sha'][:8]}  {p}")

    # 2) 按目录构造 tree（git ls-tree 结构）
    def build_tree(paths: list[str], prefix: str = "") -> str:
        subs: dict[str, list[str]] = {}
        files: list[dict] = []
        for p in paths:
            if "/" in p:
                top, rest = p.split("/", 1)
                subs.setdefault(top, []).append(rest)
            else:
                full = f"{prefix}/{p}" if prefix else p
                files.append({"path": p, "mode": "100644", "type": "blob", "sha": blobs[full]})
        for d, childs in subs.items():
            sub_prefix = f"{prefix}/{d}" if prefix else d
            files.append({"path": d, "mode": "040000", "type": "tree",
                          "sha": build_tree(childs, sub_prefix)})
        r = api("POST", "/repos/%s/git/trees" % REPO, {"tree": files}, token)
        return r["sha"]

    tree_sha = build_tree(entries)
    print(f"tree: {tree_sha[:12]}")

    # 3) commit（远端无 HEAD 则无 parent）
    parents = []
    try:
        cur = api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}", token=token)
        parents = [cur["object"]["sha"]]
    except RuntimeError:
        pass
    commit = api("POST", "/repos/%s/git/commits" % REPO,
                 {"message": msg, "tree": tree_sha, "parents": parents}, token)
    print(f"commit: {commit['sha'][:12]}")

    # 4) 更新 ref（force 覆盖，保证幂等）
    api("PATCH", f"/repos/{REPO}/git/refs/heads/{BRANCH}",
        {"sha": commit["sha"], "force": True}, token)
    print(f"已推送 {BRANCH} -> {commit['sha'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
