"""通过 GitHub Git Data API 提交文件（blob -> tree -> commit -> update ref）。

用途：当 git 传输通道（git-upload-pack / git-receive-pack）不可达，
且 Contents API PUT 因 PAT 缺少 workflow scope 返回 404 时，尝试这条路。

用法：
    python tools_gh_put_via_gitdata.py <仓库内路径> "<commit message>"           # 单文件（旧式，保留）
    python tools_gh_put_via_gitdata.py --msg "<message>" <路径> [路径...]        # 多文件，合成**一个** commit

多文件必须走 --msg 形式：一个 tree 里塞多个 blob，只产生一个 commit。
逐个调用单文件模式会在 main 上留下一串碎提交，之后本地 rebase 要反复处理，
且中途失败会让远端处于「改了一半」的状态。

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


def _req_curl(method: str, url: str, body=None):
    """用 curl 子进程发同一请求。

    存在理由：这个网络下 **python 的 urllib 会在 TLS 握手阶段被打断**
    （`SSL: UNEXPECTED_EOF_WHILE_READING`），而 curl 对同一 URL 稳定返 200。
    差别在 TLS 栈与协议协商，不是凭据或 URL 的问题——所以看到 UNEXPECTED_EOF
    不要去查 token，直接换传输层。

    命令以参数数组传给 subprocess（不经 shell），token 只出现在参数里、不打印。
    """
    cmd = ["curl", "-sS", "-X", method,
           "-H", "Authorization: Bearer " + _TOK,
           "-H", "Accept: application/vnd.github+json",
           "-w", "\n%{http_code}", url]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    try:
        p = subprocess.run(
            cmd, input=json.dumps(body).encode() if body is not None else None,
            capture_output=True, timeout=60)
    except Exception as e:                                  # noqa: BLE001
        return 0, {"raw": f"curl 兜底也失败: {type(e).__name__}: {e}"}
    out = p.stdout.decode("utf-8", "replace")
    text, _, code = out.rpartition("\n")
    try:
        status = int(code.strip())
    except ValueError:
        return 0, {"raw": f"curl 输出无状态码: {out[:300]}"}
    try:
        return status, json.loads(text) if text.strip() else {}
    except Exception:                                       # noqa: BLE001
        return status, {"raw": text[:400]}


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
        # HTTP 错误码是**服务端给的答案**（404/422/401），换传输层重发得到的是
        # 同一个答案，还可能把非幂等 POST 重复执行。只有传输层失败才兜底。
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"raw": e.read().decode("utf-8", "replace")[:400]}
    except Exception as e:                                  # noqa: BLE001
        print(f"[warn] urllib 传输失败（{type(e).__name__}），改用 curl 兜底")
        return _req_curl(method, url, body)


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 3

    # 两种调用形态：--msg 多文件 / 旧式 <path> <msg> 单文件
    if argv[0] == "--msg":
        if len(argv) < 3:
            print(__doc__)
            return 3
        msg = argv[1]
        paths = [p.replace("\\", "/") for p in argv[2:]]
    else:
        if len(argv) < 2:
            print(__doc__)
            return 3
        paths = [argv[0].replace("\\", "/")]
        msg = argv[1]

    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        print(f"[ERR] 本地文件不存在: {', '.join(missing)}")
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

    # 所有 blob 先全部建好，再一次性提交 tree。任一 blob 失败就整体放弃，
    # 不做「已成功的先落地」——半套改动落到 main 上比不落地更难排查。
    entries = []
    for p in paths:
        content = open(p, "rb").read().decode("utf-8")
        st, blob = _req("POST", f"{API}/git/blobs",
                        {"content": content, "encoding": "utf-8"})
        if st not in (200, 201):
            print(f"[ERR] 创建 blob 失败 {p} {st}: "
                  f"{json.dumps(blob, ensure_ascii=False)[:300]}")
            return 2
        entries.append({"path": p, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
        print(f"[3/5] blob {p} = {blob['sha'][:12]}")

    st, tree = _req("POST", f"{API}/git/trees",
                    {"base_tree": base_tree, "tree": entries})
    if st not in (200, 201):
        print(f"[ERR] 创建 tree 失败 {st}: {json.dumps(tree, ensure_ascii=False)[:300]}")
        print("      （若为 404，说明其中含 .github/workflows/ 路径，PAT 缺 workflow scope）")
        return 2
    print(f"[4/5] tree = {tree['sha'][:12]}（{len(entries)} 个文件）")

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
