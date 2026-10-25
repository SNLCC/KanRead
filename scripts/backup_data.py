"""脚本入口：给 ``scripts/backup-data.ps1`` 调用，也可以单独运行。

输出约定（PowerShell 侧只认最后两行，便于解析）::

    KANREAD_BACKUP_PATH=<zip 路径>
    KANREAD_BACKUP_OK <字节数>

失败时向 stderr 写 ``KANREAD_BACKUP_FAILED <原因>`` 并以非 0 退出。
不联网、不发送任何数据；只读写本机数据目录。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backup import BackupError, create_snapshot  # noqa: E402  (路径先就位再导入)


def main(argv=None):
    parser = argparse.ArgumentParser(description='KanRead local data backup')
    parser.add_argument('--data-dir', required=True, help='数据目录（通常是程序目录下的 data）')
    parser.add_argument('--out-dir', default='', help='备份输出目录；默认 <data-dir>/backups')
    parser.add_argument('--include-logs', action='store_true', help='把日志一起打包（默认排除）')
    args = parser.parse_args(argv)
    try:
        result = create_snapshot(args.data_dir, include_logs=args.include_logs,
                                 output_folder=args.out_dir or None)
    except (BackupError, OSError, ValueError) as exc:
        print('KANREAD_BACKUP_FAILED ' + str(exc), file=sys.stderr)
        return 1
    print('KANREAD_BACKUP_PATH=' + result['path'])
    print('KANREAD_BACKUP_OK ' + str(result['size_bytes']))
    print('KANREAD_BACKUP_ENTRIES ' + str(result['entries_count']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
