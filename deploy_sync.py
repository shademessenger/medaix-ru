#!/usr/bin/env python3
import json
from pathlib import Path

import paramiko


HOST = "207.90.238.174"
USER = "root"
PASSWORD = "0Y4i9zY1D7EiDpT"
REMOTE_DIR = "/opt/trainable-ai"
LOCAL_DIR = Path(__file__).resolve().parent

FILES = [
    "app.py",
    "index.html",
    "dataset_pipeline.py",
    "incoming_data.sqlite3",
]

DIRS = [
    "api",
    "kb_data",
]


def upload_tree(sftp: paramiko.SFTPClient, local_root: Path, remote_root: str) -> None:
    for name in FILES:
        local_path = local_root / name
        if not local_path.exists():
            continue
        sftp.put(str(local_path), f"{remote_root}/{name}")
    for dirname in DIRS:
        local_dir = local_root / dirname
        if not local_dir.exists():
            continue
        try:
            sftp.mkdir(f"{remote_root}/{dirname}")
        except OSError:
            pass
        for local_path in local_dir.rglob("*"):
            if local_path.is_dir():
                remote_path = f"{remote_root}/{dirname}/{local_path.relative_to(local_dir).as_posix()}"
                try:
                    sftp.mkdir(remote_path)
                except OSError:
                    pass
                continue
            remote_path = f"{remote_root}/{dirname}/{local_path.relative_to(local_dir).as_posix()}"
            parent = remote_path.rsplit("/", 1)[0]
            parts = parent.replace(remote_root + "/", "").split("/")
            current = remote_root
            for part in parts:
                current += "/" + part
                try:
                    sftp.mkdir(current)
                except OSError:
                    pass
            sftp.put(str(local_path), remote_path)


def run(ssh: paramiko.SSHClient, command: str) -> str:
    stdin, stdout, stderr = ssh.exec_command(command, timeout=120)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if err.strip():
        out = out + ("\n" if out else "") + err
    return out.strip()


def main() -> None:
    source_db = LOCAL_DIR / "data.sqlite3"
    incoming_db = LOCAL_DIR / "incoming_data.sqlite3"
    if source_db.exists():
        incoming_db.write_bytes(source_db.read_bytes())

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    sftp = ssh.open_sftp()
    upload_tree(sftp, LOCAL_DIR, REMOTE_DIR)
    sftp.close()

    restart_output = run(ssh, "systemctl restart trainable-ai.service && systemctl is-active trainable-ai.service")
    log_output = run(
        ssh,
        "journalctl -u trainable-ai.service -n 20 --no-pager",
    )
    count_output = run(
        ssh,
        "python3 - <<'PY'\nimport sqlite3\nconn=sqlite3.connect('/opt/trainable-ai/data.sqlite3')\ncur=conn.cursor()\nprint(cur.execute('select count(*) from knowledge').fetchone()[0])\nconn.close()\nPY",
    )
    print(json.dumps({"service": restart_output, "knowledge_count": count_output, "logs": log_output}, ensure_ascii=False))
    ssh.close()


if __name__ == "__main__":
    main()
