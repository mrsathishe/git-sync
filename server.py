
import json
import shutil
import subprocess
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DASHBOARD = Path(__file__).resolve().parent
PROJECTS = DASHBOARD.parent
RECOVERY = PROJECTS / "_github_deleted_recovery"
MANIFEST = DASHBOARD / "manifest.json"
LOG_DIR = DASHBOARD / "logs"
INDEX = DASHBOARD / "index.html"
PORT = 8765

LOG_DIR.mkdir(exist_ok=True)
RECOVERY.mkdir(exist_ok=True)

lock = threading.Lock()


def log(message):
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line)
    with (LOG_DIR / "sync.log").open("a") as f:
        f.write(line + "\n")


def git(path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=check
    )


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def save_manifest(data):
    MANIFEST.write_text(json.dumps(data, indent=2))


def github_repositories():
    """Fetch every accessible repository. Failure aborts synchronization."""
    subprocess.run(
        ["gh", "auth", "status"],
        check=True, capture_output=True, text=True
    )

    login = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        check=True, capture_output=True, text=True
    ).stdout.strip()

    repositories = []
    page = 1

    while True:
        result = subprocess.run(
            [
                "gh", "api",
                f"/user/repos?per_page=100&page={page}"
                "&affiliation=owner,collaborator,organization"
            ],
            capture_output=True, text=True
        )

        if result.returncode != 0:
            raise RuntimeError(
                "GitHub API failed. No repositories will be deleted."
            )

        batch = json.loads(result.stdout)
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected GitHub API response.")

        if not batch:
            break

        repositories.extend(batch)
        page += 1

    return login, repositories


def folder_name(repo, login):
    owner = repo["owner"]["login"]
    name = repo["name"]
    return name if owner.lower() == login.lower() else f"{owner}__{name}"


def is_safe_project_path(path):
    try:
        resolved = path.resolve()
        return (
            resolved.parent == PROJECTS.resolve()
            and resolved != DASHBOARD.resolve()
            and resolved != RECOVERY.resolve()
        )
    except OSError:
        return False


def local_status(path):
    result = git(path, "status", "--porcelain=v1", check=False)

    if result.returncode != 0:
        return {"error": "Could not read Git status"}

    lines = result.stdout.splitlines()
    modified, untracked, deleted = [], [], []

    for line in lines:
        if len(line) < 3:
            continue

        code = line[:2].strip()
        filename = line[3:].strip()

        if code == "??":
            untracked.append(filename)
        elif "D" in code:
            deleted.append(filename)
        else:
            modified.append(filename)

    ahead = behind = 0
    upstream = git(
        path, "rev-parse", "--abbrev-ref", "@{upstream}", check=False
    )

    if upstream.returncode == 0:
        counts = git(
            path, "rev-list", "--left-right", "--count",
            "HEAD...@{upstream}", check=False
        )
        if counts.returncode == 0:
            values = counts.stdout.split()
            ahead, behind = map(int, values)

    return {
        "modified": modified,
        "untracked": untracked,
        "deleted": deleted,
        "ahead": ahead,
        "behind": behind,
        "clean": not (modified or untracked or deleted),
        "error": None
    }


def repository_dashboard(manifest):
    rows = []

    for repo_id, entry in manifest.items():
        path = PROJECTS / entry["folder"]

        if not path.is_dir():
            continue

        status = local_status(path)

        rows.append({
            "id": repo_id,
            "name": entry["name"],
            "folder": entry["folder"],
            "url": entry["url"],
            **status
        })

    return rows


def sync_all():
    with lock:
        log("Starting synchronization")

        # Do not prune anything unless the complete GitHub inventory
        # has been successfully retrieved.
        login, remote_repos = github_repositories()
        manifest = load_manifest()

        remote_ids = {str(r["id"]) for r in remote_repos}
        summary = {"cloned": 0, "fetched": 0, "deleted": 0,
                   "recovered": 0, "errors": []}

        # Clone new repositories and fetch existing ones.
        for repo in remote_repos:
            repo_id = str(repo["id"])
            folder = folder_name(repo, login)
            path = PROJECTS / folder
            clone_url = repo["clone_url"]
            full_name = repo["full_name"]

            try:
                entry = manifest.get(repo_id)

                if entry:
                    old_path = PROJECTS / entry["folder"]

                    # Handle a GitHub repository rename.
                    if old_path.exists() and old_path != path:
                        if path.exists():
                            raise RuntimeError(
                                f"Rename destination already exists: {folder}"
                            )
                        old_path.rename(path)

                    elif old_path.exists():
                        path = old_path
                        folder = entry["folder"]

                if not path.exists():
                    if path.exists():
                        raise RuntimeError("Destination already exists")

                    subprocess.run(
                        ["gh", "repo", "clone", full_name, str(path)],
                        check=True, capture_output=True, text=True
                    )
                    summary["cloned"] += 1
                    log(f"Cloned {full_name}")

                else:
                    git(path, "remote", "set-url", "origin", clone_url)

                    fetch = git(path, "fetch", "origin", check=False)
                    if fetch.returncode != 0:
                        raise RuntimeError(fetch.stderr.strip())

                    summary["fetched"] += 1
                    log(f"Fetched {full_name}")

                manifest[repo_id] = {
                    "name": full_name,
                    "folder": folder,
                    "url": clone_url
                }

            except Exception as exc:
                message = f"{full_name}: {exc}"
                summary["errors"].append(message)
                log(f"ERROR: {message}")

        save_manifest(manifest)

        # Only repositories previously tracked in the manifest
        # can be considered for deletion.
        for repo_id in list(manifest):
            if repo_id in remote_ids:
                continue

            entry = manifest[repo_id]
            path = PROJECTS / entry["folder"]

            if not is_safe_project_path(path):
                summary["errors"].append(
                    f"Unsafe deletion path: {entry['folder']}"
                )
                continue

            if path.exists():
                status = local_status(path)

                if status.get("error") or not status["clean"] or status["ahead"]:
                    destination = RECOVERY / (
                        f"{entry['folder']}_"
                        + datetime.now().strftime("%Y%m%d_%H%M%S")
                    )

                    if destination.exists():
                        summary["errors"].append(
                            f"Recovery destination exists: {destination.name}"
                        )
                        continue

                    shutil.move(str(path), str(destination))
                    summary["recovered"] += 1
                    log(f"Moved deleted remote repository to recovery: {destination}")
                else:
                    shutil.rmtree(path)
                    summary["deleted"] += 1
                    log(f"Deleted local repository: {entry['folder']}")

            del manifest[repo_id]

        save_manifest(manifest)
        log(f"Synchronization completed: {summary}")
        return summary


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlparse(self.path).path

        if route == "/api/status":
            try:
                manifest = load_manifest()
                rows = repository_dashboard(manifest)
                self.send_json({
                    "repositories": rows,
                    "total": len(rows),
                    "clean": sum(1 for r in rows if r.get("clean")),
                    "modified": sum(
                        1 for r in rows
                        if r.get("modified") or r.get("untracked")
                        or r.get("deleted")
                    ),
                    "ahead": sum(1 for r in rows if r.get("ahead", 0) > 0),
                    "behind": sum(1 for r in rows if r.get("behind", 0) > 0),
                    "recovery_count": sum(
                        1 for p in RECOVERY.iterdir() if p.is_dir()
                    ),
                })
            except Exception as exc:
                self.send_json({"error": str(exc)}, 500)
            return

        if route == "/" or route == "/index.html":
            if not INDEX.exists():
                self.send_error(404, "Create index.html first")
                return
            body = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/sync":
            self.send_error(404)
            return

        try:
            result = sync_all()
            self.send_json({"success": True, "result": result})
        except Exception as exc:
            log(f"SYNC FAILED: {exc}")
            self.send_json({"success": False, "error": str(exc)}, 500)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Dashboard running at {url}")
    threading.Timer(1, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
