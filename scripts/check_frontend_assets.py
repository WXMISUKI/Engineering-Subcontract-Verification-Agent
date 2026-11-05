"""检查已部署前端实际交付的 PDF.js worker 及 JavaScript 类型。"""
import argparse
from html.parser import HTMLParser
import re
import urllib.parse
import urllib.request


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'script' and values.get('type') == 'module' and values.get('src'):
            self.sources.append(values['src'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('base_url', help='例如 http://127.0.0.1:8088')
    base = parser.parse_args().base_url.rstrip('/') + '/'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(path):
        url = urllib.parse.urljoin(base, path)
        with opener.open(url, timeout=15) as response:
            return response.headers.get_content_type(), response.read()

    content_type, html = read('/')
    if content_type != 'text/html':
        raise SystemExit('前端首页不是 HTML')
    scripts = Scripts()
    scripts.feed(html.decode('utf-8'))
    workers = set()
    for source in scripts.sources:
        content_type, content = read(source)
        if content_type not in ('application/javascript', 'text/javascript'):
            raise SystemExit('前端脚本的 MIME 类型不正确')
        workers.update(re.findall(rb'/assets/pdf\.worker[^"\s]*?\.mjs', content))
    if not workers:
        raise SystemExit('未找到 PDF worker 资源引用')
    for worker in workers:
        content_type, content = read(worker.decode('ascii'))
        if content_type not in ('application/javascript', 'text/javascript') or not content:
            raise SystemExit(f'PDF worker 的 MIME 类型不正确: {content_type}')
    print(f'前端脚本及 {len(workers)} 个 PDF worker 的 MIME 类型正常')


if __name__ == '__main__':
    main()
