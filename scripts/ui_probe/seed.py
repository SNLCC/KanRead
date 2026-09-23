"""给探针服务导入一份合成文献（三页），好让阅读界面真正渲染出来。"""
import io
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.test_app import pdf_bytes  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8799'

if __name__ == '__main__':
    binary = pdf_bytes(['\n'.join(f'Page {p}, line {n}: evidence sentence about sleep and attention.'
                                 for n in range(1, 6)) for p in range(1, 4)])
    response = httpx.post(URL + '/api/documents',
                          files={'file': ('probe-doc.pdf', io.BytesIO(binary), 'application/pdf')}, timeout=60)
    print('导入：', response.status_code, response.text[:200])
