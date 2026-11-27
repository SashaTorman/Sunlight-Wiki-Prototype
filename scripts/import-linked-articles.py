#!/usr/bin/env python3
"""Import original Wiki articles linked from article prose, preserving local edits."""
import concurrent.futures
from collections import deque
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.error

import importlib.util

spec = importlib.util.spec_from_file_location('wiki_import', Path(__file__).with_name('localize-wiki.py'))
wiki = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wiki)
ROOT = wiki.ROOT


def article_links(source):
    for match in re.finditer(r'<a\b[^>]*href="(https://sunlight.net/wiki/[^"?#]+\.html/?)"[^>]*>(.*?)</a>', source, re.S):
        yield html.unescape(match[1]), html.unescape(re.sub('<[^>]+>', '', match[2])).strip()


def main():
    template = (ROOT / 'article/hublot-i-dzheff-kuns-turbijon-na-granitse-iskusstva-i-vremeni.html').read_text()
    manifest_path = ROOT / 'data/article-sources.json'
    manifest = json.loads(manifest_path.read_text())
    queue = deque(pair for page in (ROOT / 'article').glob('*.html') for pair in article_links(page.read_text()))
    seen, imported, urls, unavailable = set(), [], {}, set()
    while queue:
        url, label = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        slug = Path(urllib.parse.urlsplit(url).path.rstrip('/')).stem
        path = 'article/' + slug + '.html'
        cached = wiki.CACHE / (slug + '.html')
        if (ROOT / path).exists():
            if cached.exists() and path not in manifest['imported'] and path not in imported:
                imported.append(path)
                urls[path] = {'title': label, 'source': url, 'section': 'Драгоценности'}
            continue
        if not cached.exists():
            try:
                cached.write_bytes(wiki.download(url))
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                unavailable.add(url)
                print('Removed obsolete article hyperlink (source 404):', url, flush=True)
                continue
        item = {'title': label, 'source': url, 'section': 'Драгоценности'}
        title, description, main_html, crumbs = wiki.raw_article(cached.read_text(), item, template)
        queue.extend(article_links(main_html))
        command = "const {topicForTerm}=require('./assets/catalog-recommendations.js');console.log(JSON.stringify(topicForTerm(process.argv[1])));"
        topic = json.loads(subprocess.check_output(['node', '-e', command, title], cwd=ROOT))
        page = wiki.shell_with_content(template, main_html, crumbs, title, description, 'dragotsennosti', topic)
        (ROOT / path).write_text(page)
        imported.append(path)
        urls[path] = item
        print('Imported linked article:', title, flush=True)

    known = {p.stem: 'article/' + p.name for p in (ROOT / 'article').glob('*.html')}
    for kind in ['brand', 'term', 'book', 'mir']:
        source = (ROOT / ('article/' + kind + '.html')).read_text()
        entries = json.loads(re.search(r'var IDX=(\[.*?\]),CH=', source, re.S)[1])
        for slug, title, category, route in entries:
            known.setdefault(slug, 'article/' + route)
    for path in [ROOT / 'index.html', *(ROOT / 'category').glob('*.html'), *(ROOT / 'article').glob('*.html')]:
        source = path.read_text()
        for url in unavailable:
            source = re.sub(r'<a\b[^>]*href="' + re.escape(html.escape(url, quote=True)) + r'"[^>]*>(.*?)</a>', r'\1', source, flags=re.S)
        updated = wiki.rewrite_html(source, path.relative_to(ROOT), known)
        if path.read_text() != updated:
            path.write_text(updated)

    images = {}
    for path in imported:
        source = (ROOT / path).read_text()
        for url in re.findall(r'<img\b[^>]*src="(https?://[^"]+)"', source):
            url = html.unescape(url)
            parsed = urllib.parse.urlsplit(url)
            host = parsed.hostname or ''
            if host == 'sunlight.net' or host.endswith('.sunlight.net'):
                suffix = Path(parsed.path).suffix.lower()
                if suffix not in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg'}:
                    suffix = '.jpg'
                images[url] = 'img/articles/' + hashlib.sha256(url.encode()).hexdigest()[:20] + suffix

    def get_image(pair):
        url, path = pair
        target = ROOT / path
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(wiki.download(url))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(get_image, images.items()))
    for path in imported:
        source = (ROOT / path).read_text()
        for url, target in images.items():
            source = source.replace(html.escape(url, quote=True), '../' + target).replace(url, '../' + target)
        (ROOT / path).write_text(source)
    manifest['imported'] = list(dict.fromkeys(manifest['imported'] + imported))
    manifest['sources'].update(urls)
    manifest['imageCount'] += len(images)
    manifest['unavailableReferences'] = sorted(set(manifest.get('unavailableReferences', [])) | unavailable)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print('Imported linked articles:', len(imported), flush=True)


if __name__ == '__main__':
    main()
