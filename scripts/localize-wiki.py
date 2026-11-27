#!/usr/bin/env python3
"""Import missing Wiki articles and make editorial navigation stay on this site.

Usage: python3 scripts/localize-wiki.py /tmp/sunlight-wiki-source.zip
Existing article bodies and the current site's chrome are preserved.
"""
import concurrent.futures
import hashlib
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path(tempfile.gettempdir()) / "sunlight-wiki-import"
OLD_ROOT = "https://tikhomirovdaonyx-crypto.github.io/sunlight-wiki/"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
ALIASES = {"60604": "emmi-2026-yuvelirnye-aktsenty-glavnoj-televizionnoj-premii"}


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def all(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.all()

    def text(self):
        return html.unescape(" ".join(c.text() if isinstance(c, Node) else c for c in self.children))

    def render(self):
        contents = "".join(c.render() if isinstance(c, Node) else c for c in self.children)
        if not self.tag:
            return contents
        attrs = "".join(' ' + k + ('="' + html.escape(str(v), quote=True) + '"' if v is not None else '') for k, v in self.attrs.items())
        return "<" + self.tag + attrs + ">" + ("" if self.tag in VOID else contents + "</" + self.tag + ">")


class Tree(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_entityref(self, name):
        self.handle_data("&" + name + ";")

    def handle_charref(self, name):
        self.handle_data("&#" + name + ";")


def read_index():
    source = (ROOT / "assets/wiki-index.js").read_text()
    return json.loads(source[len("window.__SEARCH="):].rstrip(";\n"))


def refresh_search_version():
    version = hashlib.sha256((ROOT / 'assets/wiki-index.js').read_bytes()).hexdigest()[:12]
    pages = [ROOT / 'index.html', *(ROOT / 'category').glob('*.html'), *(ROOT / 'article').glob('*.html')]
    for page in pages:
        source = page.read_text()
        updated = re.sub(r'assets/wiki-index\.js(?:\?v=[a-zA-Z0-9-]+)?', 'assets/wiki-index.js?v=' + version, source)
        if source != updated:
            page.write_text(updated)


def download(url):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (host == "sunlight.net" or host.endswith(".sunlight.net")):
        raise ValueError("Unexpected source host: " + url)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def clean_fragment(node, source_url):
    """Retain article content, not the source site's scripts and widgets."""
    if node.tag in {"script", "style", "iframe", "object", "embed", "form", "input", "button", "noscript"} or node.attrs.get("id") == "toc_container":
        return None
    attrs = {k: v for k, v in node.attrs.items() if k in {"id", "class", "href", "src", "alt", "title", "colspan", "rowspan", "datetime"}}
    if node.tag == "img":
        src = node.attrs.get("data-src") or node.attrs.get("src") or ""
        if not src or src.startswith("data:"):
            return None
        attrs.update(src=urllib.parse.urljoin(source_url, src), loading="lazy", decoding="async")
    if node.tag == "a" and attrs.get("href") and not attrs["href"].startswith("#"):
        attrs["href"] = urllib.parse.urljoin(source_url, attrs["href"])
        if not attrs["href"].startswith(("https://", "http://", "mailto:", "tel:")):
            attrs.pop("href")
    if node.tag == "figure":
        attrs["class"] = "md-fig"
    result = Node(node.tag, attrs.items())
    for child in node.children:
        cleaned = clean_fragment(child, source_url) if isinstance(child, Node) else child
        if cleaned is not None:
            result.children.append(cleaned)
    return result


def local_route(url, known_slugs):
    url = html.unescape(url)
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    if host == "tikhomirovdaonyx-crypto.github.io":
        path = parsed.path
        if path.startswith("/sunlight-wiki/"):
            path = path[len("/sunlight-wiki/"):]
        path = path.lstrip("/")
        while path.startswith("../"):
            path = path[3:]
        path = path or "index.html"
        if path.startswith("article/"):
            slug = Path(path).stem
            path = "article/" + ALIASES.get(slug, slug) + ".html"
        return path + ("#" + parsed.fragment if parsed.fragment else "")
    if host not in {"sunlight.net", "www.sunlight.net"} or not parsed.path.startswith("/wiki"):
        return None
    path = parsed.path[len("/wiki"):].strip("/")
    if not path:
        return "index.html"
    if path.startswith("category/"):
        parts = path.split("/")
        category = parts[1]
        if (ROOT / "category" / (category + ".html")).exists():
            return "category/" + category + ".html" + ("#" + parts[2] if len(parts) > 2 else "")
        return None
    slug = Path(path).stem
    slug = ALIASES.get(slug, slug)
    return known_slugs.get(slug)


def relative_route(route, page):
    path, separator, fragment = route.partition("#")
    result = posixpath.relpath(path, page.parent.as_posix())
    return result + (separator + fragment if separator else "")


def rewrite_html(source, page, known_slugs):
    def anchor(match):
        tag = match.group()
        href = re.search(r'\bhref="([^"]*)"', tag)
        if not href:
            return tag
        route = local_route(href[1], known_slugs)
        if route is None:
            return tag
        value = html.escape(relative_route(route, page), quote=True)
        tag = tag[:href.start(1)] + value + tag[href.end(1):]
        tag = re.sub(r'\s+target="[^"]*"', '', tag)
        tag = re.sub(r'\s+rel="(?:noopener|noreferrer|external)(?: (?:noopener|noreferrer|external))*"', '', tag)
        return tag

    source = re.sub(r'<a\b[^>]*>', anchor, source)
    # Canonical, social and structured metadata must also describe this site.
    def old_url(match):
        route = local_route(match.group(), known_slugs)
        return relative_route(route, page) if route else match.group()

    return re.sub(r'https?://tikhomirovdaonyx-crypto\.github\.io/[^\s"\'<>\\]+', old_url, source)


def shell_with_content(template, main, crumbs, title, description, category, topic):
    source = re.sub(r'<main\b[^>]*>.*?</main>', lambda _: main, template, count=1, flags=re.S)
    source = re.sub(r'<nav class="crumbs\b[^>]*>.*?</nav>', lambda _: crumbs, source, count=1, flags=re.S)
    head_end = source.index("</head>")
    head, body = source[:head_end], source[head_end:]
    head = re.sub(r'<title>.*?</title>', lambda _: '<title>' + html.escape(title) + ' — Sunlight Wiki</title>', head, flags=re.S)
    head = re.sub(r'<meta\b[^>]*(?:name="description"|property="og:[^"]*")[^>]*>', '', head)
    head = re.sub(r'<link\b[^>]*rel="canonical"[^>]*>', '', head)
    head = re.sub(r'<script type="application/ld\+json">.*?</script>', '', head, flags=re.S)
    head = re.sub(r'<script[^>]*src="../assets/catalog-recommendations.js"[^>]*></script>', '', head)
    head += '<meta name="description" content="' + html.escape(description, quote=True) + '">\n'
    if topic:
        head += '<script src="../assets/catalog-recommendations.js" data-catalog-topic="' + topic + '" defer></script>\n'
    # Reuse today's navigation, search, footer, type styles and share behavior.
    body = re.sub(r'(<nav\b[^>]*(?:class="[^"]*wh-nav|id="mnav")[^>]*>)(.*?)(</nav>)',
        lambda m: m[1] + re.sub(r'(<a\b[^>]*?)\s+aria-current="page"', r'\1', m[2]).replace(
            'href="../category/' + category + '.html"',
            'href="../category/' + category + '.html" aria-current="page"') + m[3], body, flags=re.S)
    return head + body


def raw_article(source, item, template):
    tree = Tree(source)
    content = next((n for n in tree.root.all() if "post-content" in n.attrs.get("class", "").split()), None)
    if content is None or len(content.text().strip()) < 200:
        raise ValueError("Article body missing: " + item["source"])
    content = clean_fragment(content, item["source"])
    prose = "".join(c.render() if isinstance(c, Node) else c for c in content.children)
    # A cached page must contain the requested article, not an error document.
    heading = next((n for n in tree.root.all() if n.tag == "h1"), None)
    title = heading.text().strip() if heading else item["title"]
    minutes = max(1, math.ceil(len(content.text().split()) / 180))
    header = re.search(r'<header class="post-head">.*?</header>', template, re.S).group()
    header = re.sub(r'<h1>.*?</h1>', lambda _: '<h1>' + html.escape(title) + '</h1>', header, flags=re.S)
    header = re.sub(r'<p class="post-lead">.*?</p>', '', header, flags=re.S)
    header = re.sub(r'<span class="md-sub">.*?</span>', '<span class="md-sub">' + str(minutes) + ' мин чтения</span>', header, flags=re.S)
    header = re.sub(r'<span class="md-views".*?</span></span>', '', header, flags=re.S)
    header = header.replace('../category/novosti.html', '../category/dragotsennosti.html').replace('>Новости</a>', '>Драгоценности</a>')
    main = '<main id="main" class="wrap"><div class="layout"><article class="post">' + header + '<div class="prose">' + prose + '</div></article></div></main>'
    crumbs = '<nav class="crumbs wrap" aria-label="Хлебные крошки"><ol class="plain"><li><a href="../">Главная</a></li><li><a href="../category/dragotsennosti.html">Драгоценности</a></li><li aria-current="page">' + html.escape(title) + '</li></ol></nav>'
    description = re.sub(r'\s+', ' ', content.text()).strip()[:180]
    return title, description, main, crumbs


def main():
    archive = zipfile.ZipFile(sys.argv[1])
    prefix = archive.namelist()[0]
    index = read_index()
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / 'data/article-sources.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'imported': [], 'sources': {}, 'imageCount': 0}
    template = (ROOT / "article/hublot-i-dzheff-kuns-turbijon-na-granitse-iskusstva-i-vremeni.html").read_text()
    known = {}
    for kind in ['brand', 'term', 'book', 'mir']:
        source = (ROOT / ('article/' + kind + '.html')).read_text()
        entries = json.loads(re.search(r'var IDX=(\[.*?\]),CH=', source, re.S)[1])
        for slug, title, category, route in entries:
            known[slug] = 'article/' + route
    known.update({p.stem: 'article/' + p.name for p in (ROOT / 'article').glob('*.html')})
    for name in archive.namelist():
        path = name[len(prefix):]
        if path.startswith('article/') and path.endswith('.html'):
            known[Path(path).stem] = path
        if path.startswith('img/') and not name.endswith('/'):
            destination = ROOT / path
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
    # Original SUNLIGHT articles referenced by the categories have no archive page.
    official = {}
    metadata = {}
    for section, items in index:
        for title, url, *rest in items:
            parsed = urllib.parse.urlsplit(url)
            slug = Path(parsed.path.rstrip('/')).stem
            slug = ALIASES.get(slug, slug)
            if section == 'Драгоценности' and parsed.hostname == 'sunlight.net' and slug not in known:
                path = 'article/' + slug + '.html'
                known[slug] = path
                official[path] = {'title': title, 'source': url, 'section': section}
            route = local_route(url, known) if parsed.scheme else url
            if route:
                metadata.setdefault(route.split('#')[0], {'title': title, 'source': url, 'section': section})
    metadata.update(official)

    def get_official(pair):
        path, item = pair
        cached = CACHE / (Path(path).stem + '.html')
        if not cached.exists():
            cached.write_bytes(download(item['source']))
        return path, cached.read_text()

    raw = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for path, source in pool.map(get_official, official.items()):
            raw[path] = source
            print('Read official article:', Path(path).name, flush=True)
    print('Official articles:', len(raw), flush=True)

    # Reuse the existing subject rules for product recommendations on new pages.
    all_titles = {path: item['title'] for path, item in metadata.items()}
    command = "const fs=require('fs'),{topicForTerm}=require('./assets/catalog-recommendations.js'); const x=JSON.parse(fs.readFileSync(0,'utf8')); console.log(JSON.stringify(Object.fromEntries(Object.entries(x).map(([k,v])=>[k,topicForTerm(v)]))));"
    topics = json.loads(subprocess.check_output(['node', '-e', command], cwd=ROOT, input=json.dumps(all_titles).encode()))
    imported = []
    for name in archive.namelist():
        path = name[len(prefix):]
        if not path.startswith('article/') or not path.endswith('.html') or (ROOT / path).exists():
            continue
        source = archive.read(name).decode('utf-8')
        title = html.unescape(re.search(r'<h1[^>]*>(.*?)</h1>', source, re.S)[1])
        description_match = re.search(r'<meta name="description" content="([^"]*)"', source)
        description = html.unescape(description_match[1]) if description_match else title
        main_html = re.search(r'<main\b[^>]*>.*?</main>', source, re.S)[0]
        main_html = re.sub(r'<script\b[^>]*>.*?</script>', '', main_html, flags=re.S)
        crumbs = re.search(r'<nav class="crumbs\b[^>]*>.*?</nav>', source, re.S)[0]
        category = 'dragotsennosti' if metadata.get(path, {}).get('section') == 'Драгоценности' else 'novosti'
        page = shell_with_content(template, main_html, crumbs, title, description, category, topics.get(path))
        (ROOT / path).write_text(page)
        imported.append(path)
    for path, source in raw.items():
        if (ROOT / path).exists():
            continue
        title, description, main_html, crumbs = raw_article(source, official[path], template)
        topic = topics.get(path)
        if not topic and 'час' in title.lower():
            topic = 'watches'
        page = shell_with_content(template, main_html, crumbs, title, description, 'dragotsennosti', topic)
        (ROOT / path).write_text(page)
        imported.append(path)
    print('Imported articles:', len(imported), flush=True)

    # Rewrite direct editorial links, including home-page brand logos and search.
    for page in [Path('index.html'), *[p.relative_to(ROOT) for p in (ROOT / 'category').glob('*.html')], *[p.relative_to(ROOT) for p in (ROOT / 'article').glob('*.html')]]:
        source = (ROOT / page).read_text()
        updated = rewrite_html(source, page, known)
        if source != updated:
            (ROOT / page).write_text(updated)
    for section, items in index:
        for item in items:
            route = local_route(item[1], known)
            if route:
                item[1] = route
    (ROOT / 'assets/wiki-index.js').write_text('window.__SEARCH=' + json.dumps(index, ensure_ascii=False) + ';\n')

    # Download only images used by the imported original SUNLIGHT articles.
    image_urls = {}
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
                image_urls[url] = 'img/articles/' + hashlib.sha256(url.encode()).hexdigest()[:20] + suffix

    def get_image(pair):
        url, path = pair
        destination = ROOT / path
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(download(url))

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(get_image, image_urls.items()))
    for path in imported:
        source = (ROOT / path).read_text()
        for url, target in image_urls.items():
            source = source.replace(html.escape(url, quote=True), '../' + target).replace(url, '../' + target)
        (ROOT / path).write_text(source)
    manifest['imported'] = list(dict.fromkeys(manifest['imported'] + imported))
    manifest['sources'].update({path: metadata.get(path, {'source': OLD_ROOT + path}) for path in imported})
    manifest['imageCount'] += len(image_urls)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    refresh_search_version()
    print('Downloaded images:', len(image_urls), flush=True)
    print('Local article pages:', len(list((ROOT / 'article').glob('*.html'))), flush=True)


if __name__ == '__main__':
    main()
