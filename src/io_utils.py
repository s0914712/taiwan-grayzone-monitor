"""共用 I/O 工具：atomic JSON 寫入、容錯 JSON 載入、HTTP retry session

僅依賴 stdlib + requests（update-ais.yml 環境只安裝 requests + pysocks，
不可 import pandas/scipy）。
"""
import json
import os
import tempfile


def atomic_write_json(path, obj, *, indent=2, compact=False):
    """原子性寫入 JSON：先寫同目錄 temp 檔，fsync 後 os.replace 取代目標。

    避免寫入中斷（CI 取消、磁碟滿）留下半截壞檔被 commit。
    compact=True 時不縮排（大型累積檔需壓在 GitHub 100MiB 限制內）。
    """
    path = os.fspath(path)
    dirname = os.path.dirname(path) or '.'
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            if compact:
                json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
            else:
                json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_json(path, default, label=None, expect_type=None):
    """載入 JSON；檔案不存在回傳 default，解析/讀取失敗印警告後回傳 default。

    expect_type（如 list、dict）不符時同樣印警告並回傳 default，
    取代散落各處的靜默 except + isinstance 檢查。
    """
    path = os.fspath(path)
    name = label or path
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"⚠️ 讀取 {name} 失敗: {e}")
        return default
    if expect_type is not None and not isinstance(data, expect_type):
        print(f"⚠️ {name} 型別不符（預期 {expect_type.__name__}，"
              f"實得 {type(data).__name__}）")
        return default
    return data


def make_retry_session(total=3, backoff_factor=1.5,
                       status_forcelist=(429, 500, 502, 503, 504),
                       allowed_methods=frozenset({'GET', 'POST'})):
    """建立含自動 retry 的 requests Session（429/5xx 指數退避）。

    注意：POST 也列入 retry 是因本 repo 的 POST 皆為唯讀查詢
    （GFW 4wings report、ITU MARS 搜尋）。publish_threads.py 的發文
    POST 非冪等，禁止採用本 session。
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(total=total, backoff_factor=backoff_factor,
                  status_forcelist=status_forcelist,
                  allowed_methods=allowed_methods)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session
