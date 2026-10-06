"""Serve one pinned manifest snapshot on IPv4 loopback only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
from core import NEW, MANIFEST_NAMES, ROOT, local_path, read_state, validate_manifests

def build_server(manifest_dir, port=33875):
    manifest_dir = Path(manifest_dir).resolve()
    manifests = {name: json.loads((manifest_dir / name).read_text(encoding='utf-8'))
                 for name in MANIFEST_NAMES}
    current = validate_manifests(manifests)
    prefix = urlsplit(NEW.decode()).path
    routes = dict(zip((prefix + 'windows-amd64.json', prefix + 'exe/windows-amd64.json',
                       prefix + 'daemon/windows-amd64.json'),
                      (json.dumps(manifests[name]).encode() for name in MANIFEST_NAMES)))
    identity = dict(project=str(ROOT), manifests=str(manifest_dir), version=current)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            data = json.dumps(identity).encode() if path == '/health' else routes.get(path)
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifests', type=Path)
    args = parser.parse_args()
    manifest_dir = args.manifests or local_path(read_state()['manifests'])
    with build_server(manifest_dir) as server:
        print(f'Update server listening on 127.0.0.1:33875; manifests={manifest_dir}', flush=True)
        server.serve_forever()

if __name__ == '__main__':
    main()
