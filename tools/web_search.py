#!/usr/bin/env python3
import json
import sys
import subprocess
import signal
import os
from shutil import which

# Optional HTTP dependency for fallback
try:
    import requests
    from html.parser import HTMLParser
    from urllib.parse import urlparse, parse_qs, unquote_plus, quote_plus
except Exception:
    requests = None

# Ensure writing to a closed pipe doesn't raise a noisy exception
signal.signal(signal.SIGPIPE, signal.SIG_DFL)


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_a = False
        self._href = None
        self._text = []
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            attrs = dict(attrs)
            href = attrs.get('href')
            if href:
                self._in_a = True
                self._href = href
                self._text = []

    def handle_data(self, data):
        if self._in_a:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self._in_a:
            text = ''.join(self._text).strip()
            if text:
                self.anchors.append((self._href, text))
            self._in_a = False
            self._href = None
            self._text = []


def _resolve_href(href):
    # If it's already an absolute URL, return it
    if href.startswith('http://') or href.startswith('https://'):
        return href

    # DuckDuckGo sometimes encodes target URLs in a /l/?uddg=... redirect
    try:
        p = urlparse(href)
        qs = parse_qs(p.query)
        uddg = qs.get('uddg')
        if uddg:
            return unquote_plus(uddg[0])
    except Exception:
        pass

    return None


def _http_fallback(query, n):
    if requests is None:
        print("ERROR: requests library is not available for HTTP fallback", file=sys.stderr)
        return None, 2

    url = 'https://lite.duckduckgo.com/lite/?q=' + quote_plus(query)
    headers = {'User-Agent': 'ooChat/web_search-fallback/1.0'}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        print(f"ERROR: failed to fetch DuckDuckGo: {e}", file=sys.stderr)
        return None, 2

    if resp.status_code != 200:
        print(f"ERROR: unexpected status from DuckDuckGo: {resp.status_code}", file=sys.stderr)
        return None, 2

    parser = _AnchorParser()
    parser.feed(resp.text)

    results = []
    seen = set()
    for href, text in parser.anchors:
        resolved = _resolve_href(href)
        if not resolved:
            # maybe it's already absolute
            if href.startswith('http'):
                resolved = href
        if not resolved:
            continue
        if 'duckduckgo.com' in resolved:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        results.append({'title': text, 'url': resolved, 'abstract': ''})
        if len(results) >= n:
            break

    return results, 0


def main():
    try:
        args = json.load(sys.stdin)
    except Exception as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2

    query = args.get('query')
    if not query:
        print("ERROR: 'query' is required", file=sys.stderr)
        return 2

    n = args.get('n', 10)
    try:
        n = int(n)
    except Exception:
        n = 10

    # Allow forcing HTTP fallback for testing via env var OOCHAT_FORCE_HTTP=1
    force_http = os.environ.get('OOCHAT_FORCE_HTTP')

    if not force_http and which('ddgr') is not None:
        cmd = ['ddgr', '--json', '-n', str(n), query]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except Exception as e:
            print(f"ERROR: failed to run ddgr: {e}", file=sys.stderr)
            return 2

        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)

        return proc.returncode

    # Fallback to lightweight HTTP scrape of DuckDuckGo (lite)
    results, code = _http_fallback(query, n)
    if code != 0:
        return code

    sys.stdout.write(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
