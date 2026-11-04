import sys
from pathlib import Path

# リポジトリ直下のスクリプト（replace_csv_ver3.py など）と shisu パッケージを import できるようにする
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
