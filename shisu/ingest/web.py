"""/ingest 画面（app.py に登録する Blueprint）。

  GET  /ingest                     入力（アップロード or ingest_in/ のファイルを選ぶ）・取得元・keibaAI のID・アクセス間隔
  POST /ingest/run                 実行（裏のスレッドで run_ingest。同時に動かすのは1件だけ）
  GET  /ingest/job/<id>            進み具合とログ（実行中は2秒ごとに再読込）。終わったら出力ファイルの一覧
  POST /ingest/job/<id>/cancel     中止（次のアクセスの前に止まる）
  GET  /ingest/job/<id>/log.txt    ログ全文
  GET  /ingest/files/<id>/<path>   出力ファイル1本
  GET  /ingest/zip/<id>            出力をまとめて ZIP
出力は ingest_out/<id>/、アップロードは ingest_in/uploads/<id>/ に置く（<id> = 実行日時 YYYYMMDD_HHMMSS）。
"""
from __future__ import annotations

import collections
import datetime as _dt
import io
import re
import threading
import time
import unicodedata
import zipfile
from pathlib import Path

from flask import Blueprint, Response, abort, redirect, request, send_file, url_for

from .. import config
from . import MIN_INTERVAL, ORDER, SOURCES, IngestRequest, run_ingest, validate

RUN_ID = re.compile(r"^\d{8}_\d{6}(?:_\d{1,3})?$")
INPUT_KINDS = {"base": ("ベースCSV", (".csv",)), "lap": ("ラップCSV", (".csv",)),
               "yasui": ("安井 Excel", (".xlsx", ".xlsm"))}
LOG_TAIL = 400

# テストで差し替える（ネットワークに出ない偽のセッション・待たない sleep）
SESSION_FACTORY = None
SLEEP = time.sleep


class Job:
    def __init__(self, run_id: str, out_dir: Path, req: IngestRequest):
        self.id = run_id
        self.out_dir = out_dir
        self.req = req
        self.status = "running"
        self.started = _dt.datetime.now()
        self.finished: _dt.datetime | None = None
        self.current = ""
        self.step = (0, len(req.sources))
        self.results: dict = {}
        self.stop = threading.Event()
        self.lines: collections.deque[str] = collections.deque(maxlen=5000)
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self._log_failed = False
        out_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = out_dir / "ingest.log"

    def log(self, msg: str) -> None:
        """画面用のメモリとログファイルに書く。ファイルに書けなくても（フォルダが消えた・容量不足）止めない。"""
        line = f"{_dt.datetime.now():%H:%M:%S} {msg}"
        with self._lock:
            self.lines.append(line)
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                if not self._log_failed:
                    self._log_failed = True
                    self.lines.append(f"[WARN] ログファイルに書けません: {e}")

    def on_source(self, name: str, k: int, n: int) -> None:
        self.current = SOURCES[name].label
        self.step = (k, n)

    def run(self) -> None:
        """どんな終わり方でも status を running から外す（外さないと以後の実行がすべて 409 になる）。"""
        status = "error"
        try:
            session = SESSION_FACTORY() if SESSION_FACTORY else None
            self.results = run_ingest(self.req, self.out_dir, log=self.log, stop=self.stop, session=session,
                                      sleep=SLEEP, on_source=self.on_source)
            bad = [v for v in self.results.values() if isinstance(v, str)]
            status = ("cancelled" if self.stop.is_set() or "CANCELLED" in bad
                      else "error" if bad else "done")
        except BaseException as e:
            try:
                self.log(f"[ERROR] {type(e).__name__}: {e}")
            except BaseException:
                pass
        finally:
            self.finished = _dt.datetime.now()
            self.current = ""
            self.status = status
            try:
                self.log(f"[INFO] 終了: {STATUS_LABELS.get(status, status)}")
            except BaseException:
                pass


STATUS_LABELS = {"running": "実行中", "done": "完了", "error": "完了（エラーあり）", "cancelled": "中止",
                 "past": "完了（以前の実行）"}
JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()


_RESERVED: set[str] = set()     # 受付中（アップロードの保存・入力の確認中）で、まだ JOBS に入っていない実行


def running_job() -> Job | None:
    with _JOBS_LOCK:
        return next((j for j in JOBS.values() if j.status == "running"), None)


def busy() -> bool:
    with _JOBS_LOCK:
        return bool(_RESERVED) or any(j.status == "running" for j in JOBS.values())


def reserve_run(root: Path) -> str | None:
    """実行を1件だけ受け付ける。実行中・受付中があれば None。

    確認と予約を同じロックの中で行い、出力フォルダもその場で作る（同じ秒に2回押されても、2件目は弾かれ、
    ID も重ならない）。"""
    with _JOBS_LOCK:
        if _RESERVED or any(j.status == "running" for j in JOBS.values()):
            return None
        base = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        rid, n = base, 0
        while (root / rid).exists() or rid in JOBS or rid in _RESERVED:
            n += 1
            rid = f"{base}_{n}"
        (root / rid).mkdir(parents=True, exist_ok=False)
        _RESERVED.add(rid)
        return rid


def release_run(run_id: str, root: Path, failed: bool) -> None:
    """予約を外す。受け付けなかった（入力エラー）ときは、空の出力フォルダと保存したアップロードを消す。"""
    with _JOBS_LOCK:
        _RESERVED.discard(run_id)
    if failed:
        import shutil
        shutil.rmtree(config.ingest_in_dir() / "uploads" / run_id, ignore_errors=True)
        d = root / run_id
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()


def unique_path(dest: Path, taken: set[Path]) -> Path:
    """同じ名前のファイルを2つアップロードしても上書きしない（a.csv, a_2.csv, …）。"""
    p, n = dest, 1
    while p in taken or p.exists():
        n += 1
        p = dest.with_name(f"{dest.stem}_{n}{dest.suffix}")
    taken.add(p)
    return p


def same_origin() -> bool:
    """別のサイトのページから送られた POST を断る（この画面はログインなしで動くため）。

    ブラウザは別サイトからのフォーム送信に Origin（と Sec-Fetch-Site）を付ける。どちらも無い POST（curl など）は通す。"""
    from urllib.parse import urlsplit
    site = request.headers.get("Sec-Fetch-Site", "")
    if site and site not in ("same-origin", "none"):
        return False
    origin = request.headers.get("Origin", "")
    if origin and origin != "null":
        return urlsplit(origin).netloc == request.host
    return origin != "null"


def safe_name(name: str) -> str:
    """アップロードのファイル名。日本語は残す（安井 Excel は名前の年・月を使う）。パスの部分・制御文字は除く。"""
    s = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/").split("/")[-1]
    s = re.sub(r"[\x00-\x1f\x7f:*?\"<>|]", "_", s).strip().lstrip(".")
    return s or "upload"


def listed_inputs() -> dict[str, list[str]]:
    out = {}
    for kind, (_label, exts) in INPUT_KINDS.items():
        d = config.ingest_in_dir() / kind
        out[kind] = sorted(p.name for p in d.glob("*") if p.is_file() and p.suffix.lower() in exts) if d.exists() else []
    return out


def _out_files(job_dir: Path) -> list[str]:
    if not job_dir.exists():
        return []
    return sorted(str(p.relative_to(job_dir)) for p in job_dir.rglob("*") if p.is_file())


def _job_dir(run_id: str) -> Path:
    if not RUN_ID.match(run_id or ""):
        abort(404)
    d = config.ingest_out_dir() / run_id
    if not d.is_dir():
        abort(404)
    return d


FORM_BODY = """
<div class="card"><h1>指数の取得（スクレイピング・ラップ計算・安井Excel）</h1>
<div class="muted">TARGET のベースCSV（レースID(新), 日付S, 枠番, 馬番, ワーク1）を1回渡せば、選んだ取得元をまとめて実行します。
出力は <code>ingest_out/実行日時/</code> に、取得元ごとの「TARGET 取込用（日付別 レースID(新), ワーク1 の2列・cp932）」と「マージ完全版」。</div>
{% if running %}<div class="warn" style="margin-top:8px">実行中のジョブがあります: <a href="{{ url_for('ingest.job', run_id=running.id) }}">{{ running.id }}</a>（終わるまで新しい実行はできません）</div>{% endif %}
{% for e in errors %}<div class="warn" style="margin-top:6px">{{ e }}</div>{% endfor %}
</div>
<form method="post" action="{{ url_for('ingest.run') }}" enctype="multipart/form-data">
<div class="card"><h1>1. 取得元</h1><div class="cbs">
{% for s in sources %}<label class="cb"><input type="checkbox" name="src" value="{{ s.name }}" {{ 'checked' if s.name in q_src else '' }}>{{ s.label }}</label>{% endfor %}
</div>
<div class="grid" style="margin-top:10px">
  <div><label>keibaAI 開始ID</label><input name="keibaai_start" value="{{ q.keibaai_start }}" inputmode="numeric" style="width:100%"></div>
  <div><label>keibaAI 終了ID</label><input name="keibaai_end" value="{{ q.keibaai_end }}" inputmode="numeric" style="width:100%"></div>
  {% for s in sources if s.scrape %}<div><label>{{ s.label }} の間隔（秒）</label>
    <input name="interval_{{ s.name }}" value="{{ q['interval_' ~ s.name] or s.default_interval }}" style="width:100%"></div>{% endfor %}
</div>
<div class="muted" style="margin-top:6px">アクセス間隔は {{ min_interval }} 秒未満にできません。keibaAI は muryou-keiba-ai.jp の連番ID（URL の最後の数字）を範囲で指定します。</div>
<label class="cb" style="margin-top:8px"><input type="checkbox" name="yasui_to_entries" value="1" {{ 'checked' if q.yasui_to_entries else '' }}>安井の出馬表用CSV（yasui_YYYYMMDD.csv）を entries_in/ にも置く</label>
</div>
<div class="card"><h1>2. 入力ファイル</h1>
{% for kind, (label, exts) in kinds.items() %}
<h2>{{ label }}{% if kind == 'base' %}（①〜⑤と安井の照合に使う）{% elif kind == 'lap' %}（⑥ ワーク1 にレースのラップ）{% else %}（ステータス新バージョンYYYY_M月.xlsx）{% endif %}</h2>
<input type="file" name="{{ kind }}_files" multiple accept="{{ exts|join(',') }}">
{% if listed[kind] %}<div class="muted" style="margin-top:4px">または ingest_in/{{ kind }}/ から選ぶ:</div><div class="cbs">
{% for f in listed[kind] %}<label class="cb"><input type="checkbox" name="{{ kind }}_pick" value="{{ f }}" {{ 'checked' if f in q_pick[kind] else '' }}>{{ f }}</label>{% endfor %}</div>
{% else %}<div class="muted">（ingest_in/{{ kind }}/ にファイルを置くと、ここから選べます）</div>{% endif %}
{% endfor %}
<div style="margin-top:12px"><button type="submit" {{ 'disabled' if busy else '' }}>実行</button></div>
</div></form>
{% if past %}<div class="card"><h1>これまでの実行</h1><table><thead><tr><th>実行</th><th>状態</th><th>ファイル数</th></tr></thead><tbody>
{% for p in past %}<tr><td><a href="{{ url_for('ingest.job', run_id=p.id) }}">{{ p.id }}</a></td><td>{{ p.status }}</td><td class="num">{{ p.n }}</td></tr>{% endfor %}
</tbody></table></div>{% endif %}
"""

JOB_BODY = """
{% if job_running %}<meta http-equiv="refresh" content="2">{% endif %}
<div class="card"><h1>実行 {{ run_id }}：{{ status }}</h1>
{% if job_running %}<div>{{ step[0] }}/{{ step[1] }} {{ current }}</div>
<form method="post" action="{{ url_for('ingest.cancel', run_id=run_id) }}" style="margin-top:8px"><button type="submit" style="background:#b91c1c">中止</button></form>{% endif %}
{% if results %}<table style="margin-top:8px"><thead><tr><th>取得元</th><th>結果</th><th>取得値あり / 行</th><th>ファイル</th></tr></thead><tbody>
{% for r in results %}<tr><td>{{ r.label }}</td><td>{{ r.state }}</td><td class="num">{{ r.ratio }}</td><td class="num">{{ r.files }}</td></tr>{% endfor %}
</tbody></table>{% endif %}
<div style="margin-top:8px"><a href="{{ url_for('ingest.index') }}">入力に戻る</a> ／ <a href="{{ url_for('ingest.log_txt', run_id=run_id) }}">ログ全文</a>
{% if files %} ／ <a href="{{ url_for('ingest.zip_all', run_id=run_id) }}">出力をまとめて ZIP</a>{% endif %}</div></div>
<div class="card"><h1>ログ（最後の {{ tail|length }} 行）</h1><pre style="font-size:12px;white-space:pre-wrap">{{ tail|join('\n') }}</pre></div>
{% if files %}<div class="card"><h1>出力ファイル（{{ files|length }}）</h1><table><tbody>
{% for f in files %}<tr><td><a href="{{ url_for('ingest.download', run_id=run_id, rel=f) }}">{{ f }}</a></td></tr>{% endfor %}
</tbody></table></div>{% endif %}
"""


def _int_or_none(v: str):
    v = unicodedata.normalize("NFKC", v or "").strip().replace(",", "")
    return int(v) if re.fullmatch(r"\d+", v) else None


def make_blueprint(render) -> Blueprint:
    bp = Blueprint("ingest", __name__, url_prefix="/ingest")

    def form(errors=(), q=None, status=200):
        q = q or {}
        root = config.ingest_out_dir()
        past = []
        if root.exists():
            for d in sorted((p for p in root.iterdir() if p.is_dir() and RUN_ID.match(p.name)), reverse=True)[:20]:
                j = JOBS.get(d.name)
                past.append({"id": d.name, "status": STATUS_LABELS[j.status if j else "past"],
                             "n": sum(1 for f in _out_files(d) if f != "ingest.log")})
        picks = {k: set(q.get(f"{k}_pick", [])) for k in INPUT_KINDS}
        return render("指数の取得", FORM_BODY, sources=[SOURCES[n] for n in ORDER], kinds=INPUT_KINDS,
                      listed=listed_inputs(), errors=list(errors), running=running_job(), busy=busy(), past=past,
                      q=q, q_src=set(q.get("src", [])), q_pick=picks, min_interval=MIN_INTERVAL), status

    @bp.route("", methods=["GET"])
    def index():
        return form()

    @bp.route("/run", methods=["POST"])
    def run():
        f = request.form
        q = {"src": f.getlist("src"), "keibaai_start": f.get("keibaai_start", ""),
             "keibaai_end": f.get("keibaai_end", ""), "yasui_to_entries": bool(f.get("yasui_to_entries"))}
        for n in ORDER:
            q[f"interval_{n}"] = f.get(f"interval_{n}", "")
        for k in INPUT_KINDS:
            q[f"{k}_pick"] = f.getlist(f"{k}_pick")
        if not same_origin():
            abort(403)
        root = config.ingest_out_dir()
        run_id = reserve_run(root)
        if run_id is None:
            return form(["実行中のジョブがあります。終わってから実行してください"], q, 409)
        ok = False
        try:
            errors, req, paths = _accept(run_id, q)
            if errors:
                return form(errors, q, 400)
            job = Job(run_id, root / run_id, req)
            job.log(f"[INFO] 取得元: {', '.join(SOURCES[s].label for s in req.sources)}")
            for kind in INPUT_KINDS:
                for p in paths[kind]:
                    job.log(f"[INFO] 入力（{INPUT_KINDS[kind][0]}）: {p.name}")
            with _JOBS_LOCK:
                JOBS[run_id] = job
            job.thread = threading.Thread(target=job.run, name=f"ingest-{run_id}", daemon=True)
            job.thread.start()
            ok = True
            return redirect(url_for("ingest.job", run_id=run_id))
        finally:
            release_run(run_id, root, failed=not ok)

    def _accept(run_id: str, q: dict):
        """アップロードを保存し、選んだファイルと合わせて IngestRequest を作る。戻り値: (エラー, 要求, 入力パス)。"""
        paths: dict[str, list[Path]] = {k: [] for k in INPUT_KINDS}
        listed = listed_inputs()
        taken: set[Path] = set()
        for kind, (_label, exts) in INPUT_KINDS.items():
            for name in dict.fromkeys(q[f"{kind}_pick"]):          # 同じファイルを2回選んでも1回
                if name in listed[kind]:
                    paths[kind].append(config.ingest_in_dir() / kind / name)
            for up in request.files.getlist(f"{kind}_files"):
                if not up or not up.filename:
                    continue
                name = safe_name(up.filename)
                if Path(name).suffix.lower() not in exts:
                    return [f"{INPUT_KINDS[kind][0]} に使えないファイル: {name}"], None, paths
                dest = unique_path(config.ingest_in_dir() / "uploads" / run_id / kind / name, taken)
                dest.parent.mkdir(parents=True, exist_ok=True)
                up.save(dest)
                paths[kind].append(dest)
        intervals = {}
        bad = []
        for n in ORDER:
            raw = unicodedata.normalize("NFKC", q[f"interval_{n}"] or "").strip()
            if SOURCES[n].scrape and raw:
                try:
                    intervals[n] = float(raw)
                except ValueError:
                    bad.append(f"{SOURCES[n].label} のアクセス間隔が数ではありません: {raw}")
        ks, ke = _int_or_none(q["keibaai_start"]), _int_or_none(q["keibaai_end"])
        if "keibaai" in q["src"] and ((q["keibaai_start"] and ks is None) or (q["keibaai_end"] and ke is None)):
            bad.append("keibaAI の開始ID・終了IDは数で入れてください")
        req = IngestRequest(sources=[s for s in q["src"] if s in SOURCES], base_paths=paths["base"],
                            lap_paths=paths["lap"], yasui_paths=paths["yasui"], keibaai_start=ks, keibaai_end=ke,
                            intervals=intervals,
                            yasui_entries_dir=config.entries_in_dir() if q["yasui_to_entries"] else None)
        return bad + validate(req), req, paths

    @bp.route("/job/<run_id>", methods=["GET"])
    def job(run_id):
        d = _job_dir(run_id)
        j = JOBS.get(run_id)
        log_path = d / "ingest.log"
        if j is not None:
            tail = list(j.lines)[-LOG_TAIL:]
        else:
            tail = log_path.read_text(encoding="utf-8").splitlines()[-LOG_TAIL:] if log_path.exists() else []
        results = []
        if j is not None:
            for name, r in j.results.items():
                if isinstance(r, str):
                    results.append({"label": SOURCES[name].label, "state": r, "ratio": "", "files": ""})
                else:
                    results.append({"label": SOURCES[name].label, "state": "OK",
                                    "ratio": f"{r.matched}/{r.total}", "files": len(r.files)})
        running = j is not None and j.status == "running"
        files = [] if running else [f for f in _out_files(d) if f != "ingest.log"]
        return render("指数の取得", JOB_BODY, run_id=run_id, status=STATUS_LABELS[j.status if j else "past"],
                      job_running=running, step=j.step if j else (0, 0), current=j.current if j else "",
                      results=results, tail=tail, files=files)

    @bp.route("/job/<run_id>/cancel", methods=["POST"])
    def cancel(run_id):
        if not same_origin():
            abort(403)
        _job_dir(run_id)
        j = JOBS.get(run_id)
        if j is not None and j.status == "running":
            j.stop.set()
            j.log("[INFO] 中止を受け付けました（次のアクセスの前に止まります）")
        return redirect(url_for("ingest.job", run_id=run_id))

    @bp.route("/job/<run_id>/log.txt", methods=["GET"])
    def log_txt(run_id):
        p = _job_dir(run_id) / "ingest.log"
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        return Response(text, mimetype="text/plain; charset=utf-8")

    @bp.route("/files/<run_id>/<path:rel>", methods=["GET"])
    def download(run_id, rel):
        d = _job_dir(run_id).resolve()
        p = (d / rel).resolve()
        if d not in p.parents or not p.is_file():
            abort(404)
        return send_file(p, as_attachment=True, download_name=p.name)

    @bp.route("/zip/<run_id>", methods=["GET"])
    def zip_all(run_id):
        d = _job_dir(run_id)
        j = JOBS.get(run_id)
        if j is not None and j.status == "running":
            abort(409)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for rel in _out_files(d):
                z.write(d / rel, f"{run_id}/{rel}")
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"ingest_{run_id}.zip")

    return bp
