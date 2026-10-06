"""Fetch official updates; activate only verified, compatible complete app copies."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

from core import (ROOT, STATE, MANIFEST_NAMES, atomic_json, bootstrap, expected_hash,
                  local_path, patch_app, read_state, save_manifests, sha256,
                  validate_manifests, verify_app, version)

OFFICIAL = 'https://sideloadly.io/'
URLS = dict(zip(MANIFEST_NAMES, ('release/windows-amd64.json',
            'release/exe/windows-amd64.json', 'release/daemon/windows-amd64.json')))

def settings():
    defaults = dict(proxy=None, extractor=None, timeout_seconds=20,
                    check_interval_hours=24, automatic_updates=True)
    path = ROOT / 'config.json'
    if path.exists():
        defaults.update(json.loads(path.read_text(encoding='utf-8')))
    if not 1 <= float(defaults['timeout_seconds']) <= 120:
        raise ValueError('timeout_seconds must be between 1 and 120')
    if float(defaults['check_interval_hours']) < 0:
        raise ValueError('check_interval_hours must not be negative')
    proxy = defaults['proxy']
    if proxy and not proxy.startswith(('http://', 'https://')):
        raise ValueError('Use an HTTP proxy; Python standard library does not support SOCKS')
    return defaults

@contextmanager
def update_lock():
    # OS releases this file lock on crashes; the file's existence is not a lock.
    path = ROOT / 'data' / 'update.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

def opener(config):
    handlers = []
    if config['proxy'] is not None:
        handlers.append(urllib.request.ProxyHandler(
            {'http': config['proxy'], 'https': config['proxy']} if config['proxy'] else {}))
    return urllib.request.build_opener(*handlers)

def fetch_manifests(client, timeout):
    result = {}
    for name, endpoint in URLS.items():
        request = urllib.request.Request(OFFICIAL + endpoint, headers={'User-Agent': 'SideloadlyFix/1'})
        with client.open(request, timeout=timeout) as response:
            if not response.url.startswith(OFFICIAL):
                raise ValueError('Manifest redirected away from official origin')
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('Manifest is unexpectedly large')
            result[name] = json.loads(raw)
    validate_manifests(result)
    return result

def download(client, url, target, expected, timeout):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256(target) == expected:
        return
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.part')
    try:
        with os.fdopen(fd, 'wb') as output:
            with client.open(url, timeout=timeout) as response:
                if not response.url.startswith(OFFICIAL):
                    raise ValueError('Installer redirected away from official origin')
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > 1024 * 1024 * 1024:
                        raise ValueError('Installer exceeds 1 GiB limit')
                    output.write(block)
        if sha256(temporary) != expected:
            raise ValueError('Installer SHA256 mismatch; active version was not changed')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def extractor_path(config):
    choices = [config['extractor'], shutil.which('7z'),
               r'C:\Program Files\7-Zip\7z.exe', r'C:\Program Files (x86)\7-Zip\7z.exe']
    return next((str(Path(path).resolve()) for path in choices if path and Path(path).is_file()), None)

def stage_update(source, manifests):
    """Verify originals and patch a fresh release without changing the active state."""
    current = validate_manifests(manifests)
    source = Path(source).resolve()
    for manifest, filename in [('exe-windows-amd64.json', 'sideloadly.exe'),
                               ('daemon-windows-amd64.json', 'sideloadlydaemon.exe')]:
        if sha256(source / filename) != expected_hash(manifests[manifest]['Sha256']):
            raise ValueError(f'{filename} does not match official manifest')
    for filename in ('sideloadly.dll', 'python38.dll', 'python38.zip', '_ssl.pyd', 'an/iTunesCore.dll'):
        if not (source / filename).is_file():
            raise ValueError(f'Missing dependency: {filename}; full installer layout required')
    release = ROOT / 'data' / 'releases' / (current + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    release.mkdir(parents=True)
    try:
        app = release / 'app'
        shutil.copytree(source, app)
        report = patch_app(app)
        verify_app(app, report)
        save_manifests(release / 'manifests', manifests)
        candidate = dict(version=current, app=app.relative_to(ROOT).as_posix(),
                         manifests=(release / 'manifests').relative_to(ROOT).as_posix(), report=report)
        atomic_json(ROOT / 'data' / 'candidate.json', candidate)
        return candidate
    except Exception:
        # Keep failed staging for diagnosis, never overwrite the active installation.
        logging.exception('Staging failed in %s', release)
        raise

def promote(candidate, active):
    verify_app(local_path(candidate['app']), candidate['report'])
    manifests = {name: json.loads((local_path(candidate['manifests']) / name).read_text())
                 for name in MANIFEST_NAMES}
    if validate_manifests(manifests) != candidate['version']:
        raise ValueError('Candidate version mismatch')
    atomic_json(ROOT / 'data' / 'previous.json', active)
    atomic_json(STATE, candidate)

def ensure_not_running():
    if os.name != 'nt':
        return
    script = "$rootPath = $env:SIDELOADLYFIX_ROOT.TrimEnd('\\') + '\\'; $found = Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.Name -in @('sideloadly.exe','sideloadlydaemon.exe') -and $_.ExecutablePath -and $_.ExecutablePath.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase) }; if ($found) { exit 2 }"
    environment = os.environ.copy()
    environment['SIDELOADLYFIX_ROOT'] = str(ROOT)
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script],
                               env=environment, capture_output=True, timeout=15)
    if completed.returncode:
        raise ValueError('Close project Sideloadly and its tray daemon before switching versions; process check must succeed')

def run(config, force=False, source=None, check_only=False):
    active = bootstrap()
    status_path = ROOT / 'data' / 'update-status.json'
    previous_status = json.loads(status_path.read_text()) if status_path.exists() else {}
    now = datetime.now(timezone.utc)
    if not force and not source:
        if not config['automatic_updates']:
            return 'Automatic updates disabled; using cached version.'
        last = previous_status.get('checked_at')
        if last and (now - datetime.fromisoformat(last)).total_seconds() < float(config['check_interval_hours']) * 3600:
            return 'Using cached version; next check is not due yet.'
    client = opener(config)
    manifests = fetch_manifests(client, float(config['timeout_seconds']))
    latest = validate_manifests(manifests)
    status = dict(checked_at=now.isoformat(), installed=active['version'], latest=latest)
    if version(latest) < version(active['version']):
        raise ValueError('Official version is older than installed version; refusing downgrade')
    if version(latest) == version(active['version']) and not source:
        # Never replace the active cached set with a different build under the same version.
        cached = {name: json.loads((local_path(active['manifests']) / name).read_text()) for name in MANIFEST_NAMES}
        if any(expected_hash(cached[name]['Sha256']) != expected_hash(manifests[name]['Sha256']) for name in MANIFEST_NAMES):
            raise ValueError('Official build changed under the same version; manual review required')
        status['result'] = 'up-to-date'
        atomic_json(status_path, status)
        return f'Already current: {latest}.'
    if check_only:
        # Do not advance automatic check throttle: the next normal launch must still download it.
        return f'Installed {active["version"]}; official latest {latest}.'
    if source is None:
        manifest = manifests['windows-amd64.json']
        installer = ROOT / 'data' / 'downloads' / latest / manifest['Installer']
        print(f'Downloading and verifying official installer {latest}...', flush=True)
        download(client, OFFICIAL + manifest['Installer'], installer,
                 expected_hash(manifest['Sha256']), float(config['timeout_seconds']))
        save_manifests(installer.parent / 'manifests', manifests)
        extractor = extractor_path(config)
        if not extractor:
            status.update(result='downloaded', installer=str(installer))
            atomic_json(status_path, status)
            return f'Update {latest} downloaded and verified: {installer}. Install 7-Zip or use --source with a complete official installation. Keeping {active["version"]} active.'
        unpacked = Path(tempfile.mkdtemp(prefix='extract-', dir=ROOT / 'data'))
        try:
            # Extraction only; do not execute the installer or alter registry/system installation.
            completed = subprocess.run([extractor, 'x', str(installer), '-o' + str(unpacked), '-y'],
                                       capture_output=True, text=True, timeout=180)
            if completed.returncode:
                raise ValueError('7-Zip extraction failed: ' + completed.stderr[-1000:])
            candidates = list(unpacked.rglob('sideloadly.exe'))
            if len(candidates) != 1:
                raise ValueError('Installer layout is ambiguous or changed')
            candidate = stage_update(candidates[0].parent, manifests)
        finally:
            shutil.rmtree(unpacked)
    else:
        candidate = stage_update(source, manifests)
    # A new version is staged first; explicit smoke validation is required before activation.
    status.update(result='staged', candidate=candidate)
    atomic_json(status_path, status)
    return f'Update {latest} verified and patched. Close Sideloadly and its tray daemon, stop the local server, then run start.cmd -Candidate to test it. After testing, close it and run update.cmd --activate. Active version remains {active["version"]}.'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--source', type=Path, help='Full unmodified official installation to stage')
    parser.add_argument('--activate', action='store_true', help='Activate staged version after testing')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if sum(bool(value) for value in (args.source, args.activate, args.rollback, args.check_only)) > 1:
        parser.error('Choose one of --source, --activate, --rollback, --check-only')
    (ROOT / 'data').mkdir(exist_ok=True)
    logging.basicConfig(filename=ROOT / 'data' / 'update.log', encoding='utf-8', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    try:
        with update_lock():
            if args.activate:
                ensure_not_running()
                candidate = json.loads((ROOT / 'data' / 'candidate.json').read_text())
                active = read_state()
                promote(candidate, active)
                message = f'Activated {candidate["version"]}. Close Sideloadly, then restart it.'
            elif args.rollback:
                ensure_not_running()
                previous = json.loads((ROOT / 'data' / 'previous.json').read_text())
                promote(previous, read_state())
                message = f'Rolled back to {previous["version"]}. Close Sideloadly, then restart it.'
            else:
                message = run(settings(), args.force, args.source, args.check_only)
            logging.info(message)
            print(message)
        return 0
    except Exception as error:
        logging.exception('Update failed; active state left unchanged')
        print(f'Update failed: {error}. Using previous cached installation.', flush=True)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
