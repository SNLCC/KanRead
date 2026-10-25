"""从 FastAPI 的 OpenAPI schema 生成本机接口契约清单（开发用）。

为什么需要它：本项目前后端靠 `/api/*` 契约协作，接插件宿主时也要靠这份清单对齐。
手工维护必然漂移，所以清单由 schema 生成，`tests/test_api_contract.py` 会在每次
新增/删除路由时失败，提醒更新本文档（等价于"契约先行"的最小落地）。

用法（在仓库根目录）：
    .venv\\Scripts\\python.exe scripts\\dump_api_contract.py            # 打印到 stdout
    .venv\\Scripts\\python.exe scripts\\dump_api_contract.py --check    # 只校验，不输出全文

脚本不联网、不读用户数据；导入应用前会把数据目录指向临时目录，避免碰真实 data/。
"""
import argparse
import atexit
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 直接运行 scripts/ 下的文件时，sys.path[0] 是 scripts/；把仓库根加进来才能 import app。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DOC = ROOT / 'docs' / 'API-CONTRACT.md'
HEADER = """# 本机 API 契约（自动生成，请勿手改）

> 由 `scripts/dump_api_contract.py` 从 FastAPI 的 OpenAPI schema 生成。
> 新增或删除接口后运行该脚本重新生成；`--check` 只比较"方法 + 路径"集合是否一致。
> 所有 `/api/*` 都需要本机访问令牌（请求头 `X-KanRead-Token`，或心跳/关闭两类接口的 `?token=`）；
> 唯一豁免是 `/api/health`（启动器与单实例检查用，不返回用户数据）。
"""


# FastAPI 自带文档路由不属于"本应用接口契约"，不写进清单。
BUILTIN_PATHS = {'/openapi.json', '/docs', '/docs/oauth2-redirect', '/redoc'}
ROUTE_LINE = re.compile(r'^- `([A-Z]+) (\S+)`$', re.MULTILINE)


def documented(content):
    return set(ROUTE_LINE.findall(content))


def build():
    # 关键：导入应用前把数据目录指到临时目录，避免碰到真实 data/；也不写 .pyc。
    # 临时目录放在仓库的**同级**而不是系统 temp：本机受限沙箱对系统 temp 的写权限不稳定，
    # 同级目录由脚本自己创建、退出时删除，不污染仓库。
    sys.dont_write_bytecode = True
    folder = ROOT.parent / f'_contract-tmp-{os.getpid()}'
    folder.mkdir(mode=0o777, exist_ok=True)
    atexit.register(lambda: shutil.rmtree(folder, ignore_errors=True))
    os.environ.setdefault('READER_DATA_DIR', str(folder))
    import app.main as main
    from app.version import API_VERSION

    schema = main.app.openapi()
    lines = [HEADER, f'接口版本：{API_VERSION}', '', '## 接口清单（按路径排序）', '']
    rows = []
    for path, methods in schema.get('paths', {}).items():
        if path in BUILTIN_PATHS:
            continue
        for method in methods:
            rows.append((path, method.upper()))
    for path, method in sorted(rows):
        lines.append(f'- `{method} {path}`')
    lines.append('')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只校验文档里的方法+路径是否与 schema 一致')
    args = parser.parse_args()
    content = build()
    if args.check:
        current = DOC.read_text(encoding='utf-8') if DOC.is_file() else ''
        raise SystemExit(0 if documented(current) == documented(content) else 1)
    print(content)


if __name__ == '__main__':
    main()
