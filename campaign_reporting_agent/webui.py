"""Local Flask UI wrapping pipeline.run_pipeline -- lets you pick/upload
report files and run the reconciliation without typing CLI flags. Single-user,
local-only tool: no auth, not meant to be exposed beyond localhost.

Runs happen in a background thread (a Goal Hits pass can take several
minutes) while the browser polls /progress/<run_id>/data for live status,
then lands on /results/<run_id> once the run finishes.

Run state is persisted to a JSON file per run (RUN_STATE_DIR) rather than
kept in an in-memory dict -- on a host like Render, a long Goal Hits pass
(headless Chromium + everything else) can get a worker process recycled or
land a later poll on a different worker/instance than the one that started
the run; an in-memory dict is invisible across that boundary and every next
poll 404s forever. A JSON file on the shared container filesystem survives it.
"""

import dataclasses
import json
import threading
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import user_config
from .pipeline import PipelineOptions, run_pipeline

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
RUN_STATE_DIR = BASE_DIR / "run_state"

# Form field names deliberately avoid strings like "google_ads"/"taboola" --
# ad-blocker cosmetic filters match those in id/name attributes and hide the
# row, even though the visible label text is unaffected.
FORM_FIELD_TO_KEY = {
    "meta": "meta",
    "linkedin": "linkedin",
    "platform_ga": "google_ads",
    "platform_tb": "taboola",
    "stackadapt": "stackadapt",
}

# Guards the read-modify-write below -- goal_hits.py's thread pool can call
# on_progress (-> _append_message) from several worker threads at once for
# the same run_id.
_state_lock = threading.Lock()


def _state_path(run_id):
    return RUN_STATE_DIR / f"{run_id}.json"


def _read_state(run_id):
    path = _state_path(run_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None  # a read raced an in-flight write; caller treats this as "not ready yet"


def _write_state(run_id, state):
    RUN_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(run_id)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    tmp.replace(path)  # atomic on the same filesystem -- no reader ever sees a half-written file


def _append_message(run_id, message):
    with _state_lock:
        state = _read_state(run_id)
        state["messages"].append(message)
        _write_state(run_id, state)


def _execute(run_id, options):
    try:
        result = run_pipeline(options, on_progress=lambda msg: _append_message(run_id, msg))
        with _state_lock:
            state = _read_state(run_id)
            state["status"] = "done"
            state["result"] = dataclasses.asdict(result)
            _write_state(run_id, state)
    except Exception as exc:  # noqa: BLE001
        with _state_lock:
            state = _read_state(run_id)
            state["status"] = "error"
            state["error"] = str(exc)
            _write_state(run_id, state)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB, these exports can be large

    @app.route("/", methods=["GET"])
    def index():
        saved = user_config.load_config()
        configured = bool(saved.get("spreadsheet_id") and saved.get("sheets_credentials"))
        return render_template("index.html", configured=configured)

    @app.route("/run", methods=["POST"])
    def run():
        saved = user_config.load_config()
        if not (saved.get("spreadsheet_id") and saved.get("sheets_credentials")):
            return render_template(
                "results.html",
                error="The spreadsheet connection hasn't been set up on this machine yet. Ask an admin to configure it first.",
                logs=[],
                result=None,
                output_dir=None,
            )

        run_id = uuid.uuid4().hex
        run_dir = UPLOAD_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        saved_paths = {}
        for form_field, key in FORM_FIELD_TO_KEY.items():
            uploaded = request.files.get(form_field)
            if uploaded and uploaded.filename:
                dest = run_dir / uploaded.filename
                uploaded.save(dest)
                saved_paths[key] = str(dest)

        output_dir = BASE_DIR / "output" / f"webui_{run_id}"

        # Connection details (spreadsheet ID, tab, credentials) are never taken
        # from the form -- they come only from the server-side saved config,
        # so end users never see or handle them.
        options = PipelineOptions(
            meta=saved_paths.get("meta"),
            linkedin=saved_paths.get("linkedin"),
            google_ads=saved_paths.get("google_ads"),
            taboola=saved_paths.get("taboola"),
            stackadapt=saved_paths.get("stackadapt"),
            pacing_tab=saved.get("pacing_tab", "Pacing sheet"),
            spreadsheet_id=saved.get("spreadsheet_id"),
            sheets_credentials=saved.get("sheets_credentials"),
            write_to_sheet="write_to_sheet" in request.form,
            update_goal_hits="update_goal_hits" in request.form,
            output_dir=str(output_dir),
        )

        _write_state(run_id, {"status": "running", "messages": [], "result": None, "error": None, "output_dir": str(output_dir)})

        threading.Thread(target=_execute, args=(run_id, options), daemon=True).start()
        return redirect(url_for("progress_page", run_id=run_id))

    @app.route("/progress/<run_id>", methods=["GET"])
    def progress_page(run_id):
        if _read_state(run_id) is None:
            abort(404)
        return render_template("progress.html", run_id=run_id)

    @app.route("/progress/<run_id>/data", methods=["GET"])
    def progress_data(run_id):
        state = _read_state(run_id)
        if state is None:
            abort(404)
        return jsonify({"status": state["status"], "messages": state["messages"]})

    @app.route("/results/<run_id>", methods=["GET"])
    def results_page(run_id):
        state = _read_state(run_id)
        if state is None:
            abort(404)
        if state["status"] == "running":
            return redirect(url_for("progress_page", run_id=run_id))
        if state["status"] == "error":
            return render_template("results.html", error=state["error"], logs=state["messages"], result=None, output_dir=None)
        return render_template(
            "results.html",
            error=None,
            logs=state["result"]["logs"],
            result=state["result"],
            output_dir=state["output_dir"],
        )

    return app


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(exist_ok=True)
    create_app().run(debug=False)
