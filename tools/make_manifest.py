#!/usr/bin/env python3
"""Builds manifest.json (and launcher/version.txt) for the Army Men: Zero Hour launcher.

Runs automatically in GitHub Actions on every push (see .github/workflows/manifest.yml).
You can also run it by hand:  python tools/make_manifest.py

Inputs : release.json  +  everything under game/  +  the launcher exe
Outputs: manifest.json (what the launcher reads), launcher/version.txt (for very old launchers)
"""
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")


def fail(msg):
    print("ERROR: " + msg)
    sys.exit(1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_url(url):
    h = hashlib.sha256()
    size = 0
    req = urllib.request.Request(url, headers={"User-Agent": "amzh-manifest"})
    with urllib.request.urlopen(req, timeout=120) as r:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def ignored(name):
    return name.startswith(".") or name.startswith("_")


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(x) for x in value if str(x).strip()]


def main():
    rel_path = os.path.join(ROOT, "release.json")
    if not os.path.exists(rel_path):
        fail("release.json not found")
    with open(rel_path, encoding="utf-8-sig") as f:
        rel = json.load(f)

    game_version = str(rel.get("game_version", "")).strip()
    if not VERSION_RE.match(game_version):
        fail("release.json: game_version must look like 1.0.1 (got '%s')" % game_version)

    if_missing = {p.replace("\\", "/").lower() for p in as_list(rel.get("if_missing"))}
    files = {}

    # 1) everything inside game/
    game_dir = os.path.join(ROOT, "game")
    if os.path.isdir(game_dir):
        for dirpath, dirnames, filenames in os.walk(game_dir):
            dirnames[:] = sorted(d for d in dirnames if not ignored(d))
            for name in sorted(filenames):
                if ignored(name):
                    continue
                full = os.path.join(dirpath, name)
                rel_file = os.path.relpath(full, game_dir).replace(os.sep, "/")
                if rel_file.lower().startswith("user/"):
                    print("WARNING: skipping %s (player data folder 'user' is never updated)" % rel_file)
                    continue
                entry = {"path": rel_file, "size": os.path.getsize(full), "sha256": sha256_file(full)}
                if rel_file.lower() in if_missing:
                    entry["if_missing"] = True
                files[rel_file.lower()] = entry

    # 2) big files hosted as GitHub Release assets (over 100 MB cannot live in the repo)
    for ext in rel.get("external_files", []) or []:
        p = str(ext.get("path", "")).replace("\\", "/").strip("/")
        url = str(ext.get("url", "")).strip()
        if not p or not url.startswith("https://"):
            fail("release.json: every external_files entry needs 'path' and an https 'url'")
        print("Hashing external file %s ..." % p)
        digest, size = hash_url(url)
        entry = {"path": p, "size": size, "sha256": digest, "url": url}
        if p.lower() in if_missing:
            entry["if_missing"] = True
        files[p.lower()] = entry

    file_list = sorted(files.values(), key=lambda e: e["path"].lower())
    delete = [p.replace("\\", "/") for p in as_list(rel.get("delete"))]

    # Revision = fingerprint of the whole file set. Players get an update even if you forget to bump the version.
    fp = hashlib.sha256()
    for e in file_list:
        fp.update(("%s:%s:%d\n" % (e["path"].lower(), e["sha256"], e["size"])).encode("utf-8"))
    for d in sorted(delete):
        fp.update(("del:%s\n" % d.lower()).encode("utf-8"))
    revision = fp.hexdigest()[:16]

    manifest = {
        "schema": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "game": {
            "version": game_version,
            "revision": revision,
            "mandatory": bool(rel.get("mandatory", False)),
            "notes": as_list(rel.get("game_notes")),
            "delete": delete,
            "files": file_list,
        },
    }

    # 3) launcher
    launcher_version = str(rel.get("launcher_version", "")).strip()
    launcher_file = str(rel.get("launcher_file", "launcher/Army_Men_Online.exe")).replace("\\", "/")
    launcher_full = os.path.join(ROOT, *launcher_file.split("/"))
    if launcher_version:
        if not VERSION_RE.match(launcher_version):
            fail("release.json: launcher_version must look like 1.1.0 (got '%s')" % launcher_version)
        if not os.path.exists(launcher_full):
            fail("launcher_version is set but %s does not exist" % launcher_file)
        manifest["launcher"] = {
            "version": launcher_version,
            "path": launcher_file,
            "size": os.path.getsize(launcher_full),
            "sha256": sha256_file(launcher_full),
            "notes": as_list(rel.get("launcher_notes")),
        }
        # very old launchers (before the manifest existed) read this file
        with open(os.path.join(ROOT, "launcher", "version.txt"), "w", encoding="ascii", newline="\n") as f:
            f.write(launcher_version + "\n")

    with open(os.path.join(ROOT, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    total = sum(e["size"] for e in file_list)
    print("manifest.json written: game %s, %d file(s), %.1f MB, revision %s" % (game_version, len(file_list), total / 1048576.0, revision))
    if launcher_version:
        print("launcher %s -> %s" % (launcher_version, launcher_file))


if __name__ == "__main__":
    main()
