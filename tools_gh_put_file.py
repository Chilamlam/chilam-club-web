"""通过 GitHub Contents API 提交单个文件（绕过 git 传输被重置 / PAT 无 workflow scope 的场景）。

用法：
    python tools_gh_put_file.py <repo_relative_path> "<commit message>"

背景：
1) 本地 git over HTTPS 到 github.com:443 经常整段不可达（连接重置/超时），但 api.github.com 通。
   API 走的是另一条链路，可作为 push 的备用通道。
2) classic PAT 若缺 `workflow` scope，PUT `.github/workflows/*.yml` 会返回 403，
   届时仍需网页端编辑——脚本会把 403 的原文打出来以便区分「网络问题」和「权限问题」。

token 从 remote.origin.url 中解析，**不回显明文**。
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "Chilamlam/chilam-club-web"
API = "https://api.github.com"


def _token() -> str:
    url = subprocess.check_output(
        ["git", "config", "--get", "remote.origin.url"], text=True
    ).strip()
    m = re.search(r"https://([^@/]+)@", url)
    if not m:
        raise SystemExit("remote.origin.url 中没有内嵌凭据，无法取 token")
    return m.group(1).split(":")[0]


def _req(method: str, url: str, tok: str, body: dict | None = None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer " + tok)
    r.add_header("Accept", "application/vnd.github+json")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"raw": e.read().decode("utf-8", "replace")[:500]}


def put_file(path: str, message: str, branch: str = "main") -> int:
    tok = _token()
    st, cur = _req("GET", f"{API}/repos/{REPO}/contents/{path}?ref={branch}", tok)
    sha = cur.get("sha") if st == 200 else None
    print(f"GET {st} remote_sha={str(sha)[:12]}")

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    body = {"message": message, "content": b64, "branch": branch}
    if sha:
        body["sha"] = sha
    st2, res = _req("PUT", f"{API}/repos/{REPO}/contents/{path}", tok, body)
    print(f"PUT {st2}")
    if st2 in (200, 201):
        print("new commit:", res["commit"]["sha"][:12])
        return 0
    if st2 == 409:
        print("冲突：远端该文件已变化，重跑一次即可（会重新取 sha）")
    if st2 == 403:
        print("403 —— 大概率 PAT 缺 workflow scope，需走 GitHub 网页端编辑")
    print("ERR:", json.dumps(res, ensure_ascii=False)[:600])
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    sys.exit(put_file(sys.argv[1], sys.argv[2]))
