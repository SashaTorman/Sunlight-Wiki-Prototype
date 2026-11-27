#!/usr/bin/env python3
"""Check article/category navigation, search routes and local article images."""
from html.parser import HTMLParser
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links, self.images, self.ids = [], [], set()
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs)
        if tag == 'img' and attrs.get('src'):
            self.images.append(attrs['src'])


def main():
    paths = [ROOT / 'index.html', *(ROOT / 'category').glob('*.html'), *(ROOT / 'article').glob('*.html')]
    pages = {p: Page(p.read_text()) for p in paths}
    errors, external_wiki = [], []
    count = 0
    detail_slugs = {}
    for name in ['brand.html', 'term.html', 'book.html', 'mir.html']:
        source = (ROOT / 'article' / name).read_text()
        match = re.search(r'var IDX=(\[.*?\]),CH=', source, re.S)
        detail_slugs[name] = {entry[0] for entry in json.loads(match[1])}

    def resolve(url, source):
        value = urllib.parse.urlsplit(html.unescape(url))
        if value.scheme or value.netloc:
            return None, value
        target = (source.parent / urllib.parse.unquote(value.path)).resolve() if value.path else source
        if target.is_dir():
            target = target / 'index.html'
        return target, value

    for path, page in pages.items():
        for link in page.links:
            url = link['href']
            target, parsed = resolve(url, path)
            if parsed.hostname == 'tikhomirovdaonyx-crypto.github.io' or (
                parsed.hostname in {'sunlight.net', 'www.sunlight.net'} and parsed.path.startswith('/wiki')
            ):
                external_wiki.append((str(path.relative_to(ROOT)), url))
            if target is None:
                continue
            count += 1
            if not target.exists():
                errors.append((str(path.relative_to(ROOT)), url))
            elif target.suffix == '.html' and link.get('target') == '_blank':
                errors.append((str(path.relative_to(ROOT)), 'Internal link opens another tab: ' + url))
            elif parsed.fragment and target.name in detail_slugs and urllib.parse.unquote(parsed.fragment) not in (detail_slugs[target.name] | pages[target].ids):
                errors.append((str(path.relative_to(ROOT)), 'Unknown detail: ' + url))
        for url in page.images:
            target, parsed = resolve(url, path)
            if target is not None and not target.is_file():
                errors.append((str(path.relative_to(ROOT)), 'Missing image: ' + url))

    index_source = (ROOT / 'assets/wiki-index.js').read_text()
    version = hashlib.sha256(index_source.encode()).hexdigest()[:12]
    for path in paths:
        if 'assets/wiki-index.js?v=' + version not in path.read_text():
            errors.append((str(path.relative_to(ROOT)), 'Stale search index version'))
    index = json.loads(index_source[len('window.__SEARCH='):].rstrip(';\n'))
    for section, items in index:
        for title, url, *rest in items:
            target, parsed = resolve(url, ROOT / 'index.html')
            if target is None or not target.is_file():
                errors.append(('Search / ' + section, title + ': ' + url))
            elif parsed.fragment and target.name in {'brand.html', 'term.html', 'book.html', 'mir.html'}:
                slugs = detail_slugs[target.name]
                if urllib.parse.unquote(parsed.fragment) not in slugs:
                    errors.append(('Search / ' + section, 'Unknown detail: ' + url))

    print(f'Checked {len(paths)} pages, {count} internal links and {sum(len(g[1]) for g in index)} search entries')
    print(f'External links to another Wiki: {len(external_wiki)}')
    for path, url in external_wiki[:30]:
        print('EXTERNAL', path, url)
    print(f'Broken links or images: {len(errors)}')
    for path, url in errors[:60]:
        print('BROKEN', path, url)
    return 1 if errors or external_wiki else 0


if __name__ == '__main__':
    sys.exit(main())
