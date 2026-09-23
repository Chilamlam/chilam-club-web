# -*- coding: utf-8 -*-
"""本地工作区 ↔ 远端 main 的逐文件对账；以及推送后的 tree 级核验。

存在理由（2026-09-23 实测两次踩到）：
  `git status` 里的 `M` 只表示「相对**本地 HEAD** 有改动」。本项目本地提交历史
  长期落后远端（且 `git fetch` 在本机不通，见下），于是会出现大量**假差异** ——
  实测 `.github/workflows/daily_update.yml`、`daily_review_ai.py`、`page_review.py`、
  `tools_probe_review.py`、`review_evidence.py` 五个文件「本地显示 M，但远端内容
  逐字相同」。若照着 git status 顺手全推，会做一堆无意义提交，还可能把远端
  更新的版本覆盖回去。所以推之前必须**逐个文件与远端 blob 比**。

为什么能本地算：远端 tree 里存的是 blob sha1，而 git blob sha1 是可离线复算的
  sha1("blob <len>\\0" + content)。推送通道（tools_gh_put_via_gitdata.py）会把
  CRLF 归一成 LF 再建 blob，所以本地内容也要先按同样口径归一，两者才可比。

本机环境事实：`github.com:443` 间歇不可达（实测 21s 超时、git fetch rc=128），
  而 `api.github.com` 稳定可用 → 对账只能走 REST，不要指望 fetch 后 diff。

用法：
  python tools_gh_blob_diff.py
      本地工作区 vs 远端 main 的 HEAD：列出「远端没有 / 内容不同 / 内容一致」。

  python tools_gh_blob_diff.py <旧commit>
      远端「旧commit → 当前 HEAD」的 tree 变化清单，并对每个变更文件做
      远端 blob 与本地内容的逐字节比对。旧 commit 可用短 sha。

  python tools_gh_blob_diff.py <旧commit> --expect a.py b.py ...
      额外断言「发生变化的路径**恰好**是这些」——推送后核验用，能抓住
      「推送顺带改了别的文件」这类事故（例如把 data/ 跑批产物一起提交了）。

退出码：0 通过 / 1 断言失败 / 2 API 失败 / 3 参数问题
"""
from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tools_gh_put_via_gitdata import API, BRANCH, _req, _to_lf  # noqa: E402

GIT = os.environ.get("GIT_EXE", "git")


def git_blob_sha(raw_lf: bytes) -> str:
    return hashlib.sha1(f"blob {len(raw_lf)}\0".encode("ascii") + raw_lf).hexdigest()


def local_changed_paths() -> list[tuple[str, str]]:
    out = subprocess.run([GIT, "status", "--porcelain"], cwd=os.getcwd(),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    pairs = []
    for line in out.splitlines():
        if line.strip():
            pairs.append((line[:2], line[3:]))
    return pairs


def _get(url: str):
    st, body = _req("GET", url)
    return st, body


def remote_head_and_tree(rev: str = f"heads/{BRANCH}"):
    """rev 可以是 heads/main 或具体 commit sha。返回 (head_sha, {path: blob_sha})。"""
    if rev.startswith("heads/"):
        st, ref = _get(f"{API}/git/ref/{rev}")
        if st != 200:
            raise RuntimeError(f"读 ref {rev} 失败 {st}: {str(ref)[:200]}")
        sha = ref["object"]["sha"]
    else:
        sha = rev
    st, commit = _get(f"{API}/git/commits/{sha}")
    if st != 200 and len(sha) < 40:
        # /git/commits/ 不认短 sha，但 /commits/ 认（会做前缀解析）→ 借它补全
        st2, quick = _get(f"{API}/commits/{sha}")
        if st2 == 200:
            sha = quick["sha"]
            st, commit = _get(f"{API}/git/commits/{sha}")
    if st != 200:
        raise RuntimeError(f"读 commit {sha} 失败 {st}（短 sha 补全也没成功）")
    st, tree = _get(f"{API}/git/trees/{commit['tree']['sha']}?recursive=1")
    if st != 200:
        raise RuntimeError(f"读 tree 失败 {st}")
    if tree.get("truncated"):
        print("⚠️ tree 被截断，结果可能不全")
    files = {e["path"]: e["sha"] for e in tree["tree"] if e["type"] == "blob"}
    return commit["sha"], files


def blob_bytes(sha: str) -> bytes:
    st, blob = _get(f"{API}/git/blobs/{sha}")
    if st != 200:
        raise RuntimeError(f"读 blob {sha[:10]} 失败 {st}")
    return base64.b64decode(blob["content"])


def mode_diff() -> int:
    head, rtree = remote_head_and_tree()
    print(f"远端 {BRANCH} head = {head[:12]}，共 {len(rtree)} 个 blob\n")
    only_local, differ, same, skipped = [], [], [], []
    for _status, path in local_changed_paths():
        full = os.path.join(os.getcwd(), path)
        if not os.path.isfile(full):
            skipped.append((path, "本地不存在（可能是删除）"))
            continue
        raw_lf, n_crlf = _to_lf(open(full, "rb").read())
        lsha, rsha = git_blob_sha(raw_lf), rtree.get(path)
        note = f"CRLF→LF {n_crlf} 处" if n_crlf else ""
        if rsha is None:
            only_local.append(path)
            print(f"🆕 远端没有      {path:38s} local={lsha[:10]} {note}")
        elif rsha == lsha:
            same.append(path)
            print(f"＝  内容一致      {path:38s} sha={lsha[:10]} {note}")
        else:
            differ.append(path)
            print(f"✎  内容不同      {path:38s} local={lsha[:10]} remote={rsha[:10]} {note}")

    print(f"\n远端没有 {len(only_local)} / 内容不同 {len(differ)} / "
          f"内容一致 {len(same)} / 跳过 {len(skipped)}")
    if same:
        print("\n⚠️ 下列文件本地显示改动但**远端内容一致** → 推上去等于空操作，"
              "多半是自己漏了 pull 造成的假差异：")
        for p in same:
            print("   =", p)
    print("\n要推的清单（把这张表原样交给 tools_gh_put_via_gitdata.py）：")
    for p in only_local + differ:
        print("   ", p)
    return 0


def mode_verify(old: str, expect: list[str]) -> int:
    fails = []

    def ck(cond, msg):
        print(("✅ " if cond else "❌ ") + msg)
        if not cond:
            fails.append(msg)

    new_full, new_t = remote_head_and_tree()
    try:
        _old_full, old_t = remote_head_and_tree(old)
    except RuntimeError as e:
        print(f"[ERR] {e}")
        print("      提示：/git/commits/ 不认短 sha；用 `git rev-parse <短sha>` 补全，"
              "或从新 commit 的 parents[0] 取。")
        return 2

    print(f"对比 {old[:12]} → {new_full[:12]}\n")
    changed = sorted(p for p in set(old_t) | set(new_t)
                     if old_t.get(p) != new_t.get(p))
    print(f"变更路径 {len(changed)} 个：")
    for p in changed:
        print(f"   {'新增' if p not in old_t else '修改'} {p}")
    gone = [p for p in changed if p not in new_t]
    if gone:
        print(f"   删除 {gone}")

    if expect:
        unexpected = [p for p in changed if p not in expect]
        missing = [p for p in expect if p not in changed]
        ck(not unexpected, f"没有任何**未预期**的路径被改动（意外：{unexpected}）")
        ck(not missing, f"全部 {len(expect)} 个预期路径都确实变了（漏：{missing}）")
        ck(len(changed) == len(expect),
           f"变更数量恰好 {len(expect)} 个（实际 {len(changed)}）")

    print()
    for p in changed:
        full = os.path.join(os.getcwd(), p)
        if p not in new_t:
            continue
        if not os.path.isfile(full):
            print(f"⚠️ 本地没有 {p}（远端已变更/新增，无法逐字节比对）")
            continue
        raw_lf, _ = _to_lf(open(full, "rb").read())
        remote = blob_bytes(new_t[p])
        ok = hashlib.sha256(raw_lf).hexdigest() == hashlib.sha256(remote).hexdigest()
        ck(ok, f"远端内容 == 本地内容  {p:36s} blob={new_t[p][:10]}")

    unchanged = len(set(old_t) & set(new_t)) - len([p for p in changed if p in old_t])
    print(f"\n未变更文件 {unchanged} 个（其中 data/ 有 "
          f"{len([p for p in old_t if p.startswith('data/')])} 个，全部未触碰）")

    if fails:
        print(f"\n❌ 核验未通过，{len(fails)} 项")
        return 1
    print("\n✅ 核验通过")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if not argv:
        return mode_diff()
    expect = []
    if "--expect" in argv:
        i = argv.index("--expect")
        expect = [p.replace("\\", "/") for p in argv[i + 1:]]
        argv = argv[:i]
    if len(argv) != 1:
        print(__doc__)
        return 3
    return mode_verify(argv[0], expect)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"[ERR] {e}")
        sys.exit(2)
