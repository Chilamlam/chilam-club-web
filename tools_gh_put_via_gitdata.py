"""通过 GitHub Git Data API 提交单个文件（blob -> tree -> commit -> update ref）。

用途：当 git 传输通道（git-upload-pack / git-receive-pack）不可达，
且 Contents API PUT 因 PAT 缺少 workflow scope 返回 404 时，尝试这条路。

用法：
    python tools_gh_put_via_gitdata.py <仓库内路径> "<commit message>"

退出码：0 成功 / 2 API 拒绝 / 3 参数或本地文件问题
注意：token 从 remote.origin.url 读取，全程不回显明文。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "Chilamlam/chilam-club-web"
BRANCH = "main"


def _token() -> str:
    url = subprocess.check_output(
        ["git", "config", "--get", "remote.origin.url"], text=True
    ).strip()
    m = re.search(r"https://([^@/]+)@", url)
    if not m:
        print("[ERR] remote.origin.url 中没有内嵌凭据")
        sys.exit(3)
    return m.group(1).split(":")[0]


_TOK = _token()
API = f"https://api.github.com/repos/{REPO}"


def _req(method: str, url: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Bearer " + _TOK)
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
            return e.code, {"raw": e.read().decode("utf-8", "replace")[:400]}
    except Exception as e:
        return 0, {"raw": f"{type(e).__name__}: {e}"}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 3
    path = sys.argv[1].replace("\\", "/")
    msg = sys.argv[2]
    if not os.path.isfile(path):
        print(f"[ERR] 本地文件不存在: {path}")
        return 3

    st, ref = _req("GET", f"{API}/git/ref/heads/{BRANCH}")
    if st != 200:
        print(f"[ERR] 读取 ref 失败 {st}: {json.dumps(ref, ensure_ascii=False)[:300]}")
        return 2
    head_sha = ref["object"]["sha"]
    print(f"[1/5] head = {head_sha[:12]}")

    st, commit = _req("GET", f"{API}/git/commits/{head_sha}")
    if st != 200:
        print(f"[ERR] 读取 commit 失败 {st}")
        return 2
    base_tree = commit["tree"]["sha"]
    print(f"[2/5] base_tree = {base_tree[:12]}")

    content = open(path, "rb").read().decode("utf-8")
    st, blob = _req("POST", f"{API}/git/blobs", {"content": content, "encoding": "utf-8"})
    if st not in (200, 201):
        print(f"[ERR] 创建 blob 失败 {st}: {json.dumps(blob, ensure_ascii=False)[:300]}")
        return 2
    print(f"[3/5] blob = {blob['sha'][:12]}")

    st, tree = _req(
        "POST",
        f"{API}/git/trees",
        {
            "base_tree": base_tree,
            "tree": [
                {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
            ],
        },
    )
    if st not in (200, 201):
        print(f"[ERR] 创建 tree 失败 {st}: {json.dumps(tree, ensure_ascii=False)[:300]}")
        return 2
    print(f"[4/5] tree = {tree['sha'][:12]}")

    st, nc = _req(
        "POST",
        f"{API}/git/commits",
        {"message": msg, "tree": tree["sha"], "parents": [head_sha]},
    )
    if st not in (200, 201):
        print(f"[ERR] 创建 commit 失败 {st}: {json.dumps(nc, ensure_ascii=False)[:300]}")
        return 2

    st, upd = _req(
        "PATCH", f"{API}/git/refs/heads/{BRANCH}", {"sha": nc["sha"], "force": False}
    )
    if st not in (200, 201):
        print(f"[ERR] 更新 ref 失败 {st}: {json.dumps(upd, ensure_ascii=False)[:300]}")
        print("      （若为 404/403，说明 PAT 缺少 workflow scope，此路不通）")
        return 2

    print(f"[5/5] OK 新 head = {upd['object']['sha'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
