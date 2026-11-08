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
