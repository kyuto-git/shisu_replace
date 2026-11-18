import sys
from pathlib import Path

# リポジトリ直下のスクリプト（replace_csv_ver3.py など）と shisu パッケージを import できるようにする
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True, scope="module")
def _reset_family_registry():
    """family の台帳はプロセス全体で1つ。selected_families.json を読むテストのあとで初期セットに戻す。"""
    yield
    from shisu import families
    families.reset()


@pytest.fixture(autouse=True, scope="session")
def _baseline_copy(tmp_path_factory):
    """SP・ST の3着内基準値のマスタ（shisu/data/index_baseline_*.csv）は replace が書き換える。
    テストがリポジトリのファイルを書き換えないように、写しのフォルダ（SHISU_BASELINE_DIR）を使う。"""
    import os
    import shutil
    d = tmp_path_factory.mktemp("baseline")
    for src in (ROOT / "shisu" / "data").glob("index_baseline_*.csv"):
        shutil.copy(src, d / src.name)
    old = os.environ.get("SHISU_BASELINE_DIR")
    os.environ["SHISU_BASELINE_DIR"] = str(d)
    yield d
    if old is None:
        os.environ.pop("SHISU_BASELINE_DIR", None)
    else:
        os.environ["SHISU_BASELINE_DIR"] = old
