#!/usr/bin/env python3
"""
Local workspace for the Arabic research daily.

    python server.py            -> http://127.0.0.1:8420

Serves the web UI and a small JSON API over the shortlists in out/.
Stdlib only. Binds to localhost only.
"""

import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
OUT = os.path.join(ROOT, "out")
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(DATA, "state.json")
PORT = int(os.environ.get("PORT", 8420))
UA = "arabic-research-daily/0.1 (local workspace)"

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

_state_lock = threading.Lock()
_run_lock = threading.Lock()
_run = {"active": False, "log": [], "started": None}


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"papers": {}}


def save_state(state):
    os.makedirs(DATA, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)      # atomic: never leave a half-written state file


def patch_paper(uid, patch):
    with _state_lock:
        state = load_state()
        entry = state["papers"].setdefault(uid, {})
        entry.update(patch)
        save_state(state)
        return entry


# ---------------------------------------------------------------- runs

def list_runs():
    if not os.path.isdir(OUT):
        return []
    runs = []
    for name in sorted(os.listdir(OUT), reverse=True):
        if not name.endswith(".json"):
            continue
        path = os.path.join(OUT, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            runs.append({
                "date": data.get("date", name[:-5]),
                "count": len(data.get("shortlist", [])),
                "stats": data.get("stats", {}),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return runs


def read_run(date):
    path = os.path.join(OUT, f"{os.path.basename(date)}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    state = load_state()["papers"]
    for p in data.get("shortlist", []):
        uid = p.get("uid") or (p.get("doi", "").lower()
                               or re.sub(r"\W+", "", p["title"].lower())[:90])
        p["uid"] = uid
        saved = state.get(uid, {})
        p["verdict"] = saved.get("verdict", "")
        p["has_notes"] = bool(saved.get("script") or saved.get("highlights"))
        p["saved"] = saved
    return data


def start_fetch(days, top, topic):
    """Run fetch.py as a subprocess, streaming its output into _run['log']."""
    with _run_lock:
        if _run["active"]:
            return False
        _run.update({"active": True, "log": [], "started": None})

    cmd = [sys.executable, "-u", os.path.join(ROOT, "fetch.py")]
    if days:
        cmd += ["--days", str(int(days))]
    if top:
        cmd += ["--top", str(int(top))]
    if topic:
        cmd += ["--topic", str(topic)]

    def worker():
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    _run["log"].append(line)
                    print("  [fetch]", line)
            proc.wait()
            _run["log"].append(f"__done__ exit={proc.returncode}")
        except Exception as e:
            _run["log"].append(f"__done__ error: {e}")
        finally:
            _run["active"] = False

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------------------------------------------------------------- upstream

def http_get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def epmc_fulltext(pmcid):
    """Open-access full text as simplified sections.

    Only the PMC open-access subset has XML, and the endpoint wants the bare
    PMCID as one path segment -- /PMC3258128/fullTextXML, not /PMC/123/...
    """
    pmcid = (pmcid or "").strip().upper()
    if not pmcid.startswith("PMC"):
        return None
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    try:
        raw = http_get(url)
    except Exception:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    body = root.find(".//body")
    if body is None:
        return None

    def flatten(node):
        return re.sub(r"\s+", " ", "".join(node.itertext())).strip()

    sections = []
    for sec in body.findall(".//sec"):
        title = sec.find("title")
        heading = flatten(title) if title is not None else ""
        paras = [flatten(p) for p in sec.findall("./p")]
        paras = [p for p in paras if len(p) > 40]
        if heading or paras:
            sections.append({"heading": heading, "paragraphs": paras})

    if not sections:
        paras = [flatten(p) for p in body.findall(".//p")]
        paras = [p for p in paras if len(p) > 40]
        if paras:
            sections = [{"heading": "", "paragraphs": paras}]

    return sections or None


def epmc_references(src, pid):
    url = (f"https://www.ebi.ac.uk/europepmc/webservices/rest/{src}/{pid}/"
           f"references?format=json&pageSize=200")
    try:
        data = json.loads(http_get(url))
    except Exception:
        return []
    refs = data.get("referenceList", {}).get("reference", [])
    return [{
        "title": r.get("title", ""),
        "authors": r.get("authorString", ""),
        "journal": r.get("journalAbbreviation", ""),
        "year": r.get("pubYear", ""),
        "doi": r.get("doi", ""),
        "id": r.get("id", ""),
        "cited_order": r.get("citationOrder", ""),
    } for r in refs]


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass                    # the fetch log is the only output worth seeing

    # -- helpers

    def send_json(self, obj, status=200):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_bytes(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -- routing

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, q = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                return self.api_get(path, q)
            return self.static(path)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            return self.api_post(parsed.path, self.read_body())
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def api_get(self, path, q):
        if path == "/api/runs":
            return self.send_json({"runs": list_runs()})

        if path == "/api/run":
            date = (q.get("date") or [""])[0]
            if not date:
                runs = list_runs()
                if not runs:
                    return self.send_json({"error": "no runs yet"}, 404)
                date = runs[0]["date"]
            try:
                return self.send_json(read_run(date))
            except FileNotFoundError:
                return self.send_json({"error": f"no run for {date}"}, 404)

        if path == "/api/config":
            with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
                return self.send_json({"config": json.load(f)})

        if path == "/api/fetch/status":
            return self.send_json({"active": _run["active"], "log": _run["log"][-80:]})

        if path == "/api/fulltext":
            pmcid = (q.get("pmcid") or [""])[0]
            if not pmcid:
                return self.send_json({"sections": None})
            return self.send_json({"sections": epmc_fulltext(pmcid)})

        if path == "/api/references":
            src = (q.get("src") or [""])[0]
            pid = (q.get("id") or [""])[0]
            if not src or not pid:
                return self.send_json({"references": []})
            return self.send_json({"references": epmc_references(src, pid)})

        return self.send_json({"error": "not found"}, 404)

    def api_post(self, path, body):
        if path == "/api/save":
            uid = body.get("uid")
            if not uid:
                return self.send_json({"error": "uid required"}, 400)
            patch = {k: v for k, v in body.items()
                     if k in ("verdict", "script", "reader_html", "highlights",
                              "notes", "title", "rtl")}
            return self.send_json({"saved": patch_paper(uid, patch)})

        if path == "/api/config":
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                return self.send_json({"error": "config object required"}, 400)
            path_cfg = os.path.join(ROOT, "config.json")
            with open(path_cfg, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return self.send_json({"ok": True})

        if path == "/api/fetch":
            ok = start_fetch(body.get("days"), body.get("top"), body.get("topic"))
            return self.send_json({"started": ok, "active": _run["active"]})

        return self.send_json({"error": "not found"}, 404)

    def static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        target = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not target.startswith(WEB) or not os.path.isfile(target):
            return self.send_json({"error": "not found"}, 404)
        ctype, _ = mimetypes.guess_type(target)
        with open(target, "rb") as f:
            data = f.read()
        return self.send_bytes(data, f"{ctype or 'application/octet-stream'}; charset=utf-8")


class V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def main():
    os.makedirs(DATA, exist_ok=True)

    servers = [ThreadingHTTPServer(("127.0.0.1", PORT), Handler)]
    # Browsers resolve "localhost" to ::1 first; without this they get a
    # connection-refused page even though the IPv4 socket is fine.
    try:
        servers.append(V6Server(("::1", PORT), Handler))
    except OSError:
        pass

    print(f"\n  Arabic Research Daily — workspace")
    print(f"  http://localhost:{PORT}\n")
    print(f"  runs: {len(list_runs())} in out/   ·   Ctrl+C to stop\n")

    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped\n")


if __name__ == "__main__":
    main()
