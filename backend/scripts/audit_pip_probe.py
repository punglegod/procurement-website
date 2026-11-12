"""去重 pip 审计结果并检查当前解释器条件，不安装软件、不访问网络。"""

import collections
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile

import pip
from pip._internal.exceptions import InstallationError
from pip._internal.models.link import Link
from pip._internal.utils.unpacking import is_within_directory, untar_file


folder = Path(__file__).resolve().parent
environment = json.loads((folder / "audit_pip_environment.json").read_text(encoding="utf-8-sig"))
requirements = json.loads((folder / "audit_pip_requirements.json").read_text(encoding="utf-8-sig"))
vulns = [vuln for item in environment["dependencies"] for vuln in item["vulns"]]
counts = collections.Counter(next(alias for alias in vuln["aliases"] if alias.startswith("GHSA-")) for vuln in vulns)
print(f"python={sys.version.split()[0]}; pip={pip.__version__}; tarfile.data_filter={hasattr(tarfile, 'data_filter')}")
print(f"environment_dependencies={len(environment['dependencies'])}; raw_findings={len(vulns)}; unique_GHSA={len(counts)}")
print("duplicate_counts=" + json.dumps(dict(counts), sort_keys=True))
print(f"requirements_dependencies={len(requirements['dependencies'])}; requirements_findings={sum(len(item['vulns']) for item in requirements['dependencies'])}")

with tempfile.TemporaryDirectory(prefix="audit-pip-", dir=folder) as temp:
    root = Path(temp)
    archive = root / "probe.tar"
    target = root / "install"
    target.mkdir()
    # 链接只指向该临时目录内的兄弟项；验证过滤器阻止越过解包目标。
    with tarfile.open(archive, "w") as out:
        info = tarfile.TarInfo("package/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside"
        out.addfile(info)
    try:
        untar_file(str(archive), str(target))
        blocked = False
    except InstallationError:
        blocked = True
    print(f"CVE-2025-8869 tar_symlink_rejected={blocked}; outside_created={(root / 'outside').exists()}")
    assert blocked and not (root / "outside").exists()
    # 仅检查路径判断函数，不执行 zip 解包或写入越界文件。
    sibling = root / "install_sibling" / "probe.txt"
    accepted = is_within_directory(str(target), str(sibling))
    actually_inside = os.path.commonpath([target, sibling]) == str(target)
    print(f"CVE-2026-1703 sibling_prefix_accepted={accepted}; actually_inside={actually_inside}")
    assert accepted and not actually_inside

# 只解析保留域名字符串，不下载文件、不解析 DNS。
filename = Link("https://index.example.invalid/%252e%252e%252fprobe.whl").filename
print(f"CVE-2026-13346 doubly_encoded_filename={filename!r}; contains_separator={'/' in filename}")
assert filename == "../probe.whl"
