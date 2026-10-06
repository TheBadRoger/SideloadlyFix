"""Shared patching, validation and atomic state operations; standard library only."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parent
OLD = b'https://sideloadly.io/release/'
NEW = b'http://127.0.0.1:33875/rrrrrr/'
STATE = ROOT / 'data' / 'active.json'
MANIFEST_NAMES = ('windows-amd64.json', 'exe-windows-amd64.json', 'daemon-windows-amd64.json')

def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+){1,3}', value):
        raise ValueError(f'Unsupported version: {value!r}')
    parts = tuple(int(part) for part in value.split('.'))
    return parts + (0,) * (4 - len(parts))

def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

def expected_hash(value):
    if not isinstance(value, str):
        raise ValueError('Missing SHA256')
    if re.fullmatch(r'[0-9a-fA-F]{64}', value):
        return value.lower()
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError('Invalid SHA256') from error
    if len(decoded) != 32:
        raise ValueError('Invalid SHA256 length')
    return decoded.hex()

def validate_manifests(manifests):
    main, exe, daemon = (manifests[name] for name in MANIFEST_NAMES)
    versions = [version(item.get('Version')) for item in (main, exe, daemon)]
    if len(set(versions)) != 1:
        raise ValueError('Official manifests disagree on version; retry later')
    for item in (main, exe, daemon):
        expected_hash(item.get('Sha256'))
    installer = main.get('Installer')
    if not isinstance(installer, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.exe', installer):
        raise ValueError('Unsafe installer filename')
    return main['Version']

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump(value, target, indent=2, ensure_ascii=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def local_path(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('State path escapes project directory')
    return path

def read_state():
    state = json.loads(STATE.read_text(encoding='utf-8'))
    version(state['version'])
    for key in ('app', 'manifests'):
        local_path(state[key])
    return state

def patch_app(directory):
    changes = []
    for name in ('sideloadly.exe', 'sideloadlydaemon.exe'):
        path = Path(directory) / name
        original = path.read_bytes()
        count = original.count(OLD)
        if name == 'sideloadly.exe' and count != 1:
            raise ValueError(f'{name}: expected one original update prefix; found {count}')
        patched = original.replace(OLD, NEW)
        assert len(patched) == len(original)
        if count:
            path.write_bytes(patched)
        changes.append(dict(file=name, replacements=count,
                            original_sha256=hashlib.sha256(original).hexdigest(),
                            patched_sha256=hashlib.sha256(patched).hexdigest()))
    report = dict(old=OLD.decode(), new=NEW.decode(), files=changes)
    atomic_json(Path(directory) / 'patch-report.json', report)
    return report

def verify_app(directory, report):
    for item in report['files']:
        if sha256(Path(directory) / item['file']) != item['patched_sha256']:
            raise ValueError(f"Patched file changed: {item['file']}")

def save_manifests(directory, manifests):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in MANIFEST_NAMES:
        atomic_json(directory / name, manifests[name])

def bootstrap():
    """Migrate this project's existing, verified 0.70.1 installation once."""
    if STATE.exists():
        return read_state()
    manifests = {name: json.loads((ROOT / name).read_text(encoding='utf-8')) for name in MANIFEST_NAMES}
    current = validate_manifests(manifests)
    report = json.loads((ROOT / 'patch-report.json').read_text(encoding='utf-8'))
    verify_app(ROOT / 'local-app', report)
    hashes = {item['file']: item['original_sha256'] for item in report['files']}
    for name, file in [('exe-windows-amd64.json', 'sideloadly.exe'),
                       ('daemon-windows-amd64.json', 'sideloadlydaemon.exe')]:
        if hashes[file] != expected_hash(manifests[name]['Sha256']):
            raise ValueError('Cached manifest does not match installed original files')
    manifest_dir = ROOT / 'data' / 'bootstrap-manifests'
    save_manifests(manifest_dir, manifests)
    state = dict(version=current, app='local-app', manifests=manifest_dir.relative_to(ROOT).as_posix(),
                 report=report)
    atomic_json(STATE, state)
    return state

if __name__ == '__main__':
    # File-based entry point avoids Windows PowerShell 5.1's native -c quoting rules.
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--bootstrap', action='store_true')
    parser.add_argument('--verify', type=Path)
    arguments = parser.parse_args()
    if arguments.bootstrap:
        bootstrap()
    elif arguments.verify:
        selected = json.loads(arguments.verify.read_text(encoding='utf-8'))
        verify_app(local_path(selected['app']), selected['report'])
        manifests = {name: json.loads((local_path(selected['manifests']) / name).read_text(encoding='utf-8'))
                     for name in MANIFEST_NAMES}
        if validate_manifests(manifests) != selected['version']:
            raise ValueError('Selected state and manifests disagree')
    else:
        parser.error('Choose --bootstrap or --verify STATE_FILE')
