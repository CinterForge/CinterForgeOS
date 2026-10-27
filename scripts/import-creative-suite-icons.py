#!/usr/bin/env python3
"""Import original publisher icons using CFOS-owned upstream artwork URLs.

Run on the macOS host before building the SteamOS layer. The source URL is
stored in PNG metadata; unchanged images are reused without a network call.
No generic stand-in artwork and no separate manifest are produced.
"""
import argparse
import io
import os
import sys
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from pathlib import Path

APPS = ('GIMP', 'Krita', 'Inkscape', 'Scribus', 'Blender', 'Kdenlive', 'Ardour', 'RapidRAW')
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'guest/layer/usr/share/cinterforge/icons/launchers'
# This is CFOS-owned artwork metadata, not a copy of another repository's
# build registry. A public CFOS checkout must work without a sibling project.
# URLs are upstream project artwork sources or upstream site icon discovery.
PUBLISHER_ICONS = {
    'GIMP': 'https://www.google.com/s2/favicons?domain=www.gimp.org&sz=128',
    'Krita': 'https://www.google.com/s2/favicons?domain=krita.org&sz=128',
    'Inkscape': 'https://www.google.com/s2/favicons?domain=inkscape.org&sz=128',
    'Scribus': 'https://www.google.com/s2/favicons?domain=www.scribus.net&sz=128',
    'Blender': 'https://www.google.com/s2/favicons?domain=www.blender.org&sz=128',
    'Kdenlive': 'https://www.google.com/s2/favicons?domain=kdenlive.org&sz=128',
    'Ardour': 'https://www.google.com/s2/favicons?domain=ardour.org&sz=128',
    'RapidRAW': 'https://getrapidraw.com/static/logo.png',
}
SCRIBUS_ARTWORK_URL = ('https://raw.githubusercontent.com/scribusproject/scribus/'
                       'master/resources/iconsets/artwork/icon_1024x1024.png')
IMPORTER_REVISION = 'v9'


def get_rows():
    """Keep the renderer's row format without requiring an external catalog."""
    return {name: {'Icon URL': PUBLISHER_ICONS[name]} for name in APPS}


def up_to_date(path, url, kind):
    from PIL import Image
    if not path.is_file():
        return False
    try:
        with Image.open(path) as im:
            return im.format == 'PNG' and im.info.get('CinterForgeIconSourceURL') == url and im.info.get('CinterForgeIconKind') == kind
    except Exception:
        return False


def save_png(img, path, url, kind):
    from PIL.PngImagePlugin import PngInfo
    meta = PngInfo()
    meta.add_text('CinterForgeIconSourceURL', url)
    meta.add_text('CinterForgeIconKind', kind)
    temp = path.with_suffix('.png.tmp')
    try:
        img.save(temp, format='PNG', pnginfo=meta)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def fetch_publisher_image(url, name):
    """Acquire the largest authentic publisher icon, not the first tiny favicon.

    Google favicon endpoints commonly return low-detail 16px generic letter
    badges. Prefer publisher-hosted touch icons, favicon PNGs, and icons linked
    from the publisher's own HTML before considering the CSV proxy.
    """
    from PIL import Image
    from urllib.parse import urljoin
    from html.parser import HTMLParser

    parsed = urlparse(url)
    candidates = []
    if name == 'Scribus':
        # This is the actual Scribus application icon from upstream source,
        # not the unrelated 16px website favicon. Never use a favicon for it.
        candidates.append(SCRIBUS_ARTWORK_URL)
        print(f'[icons] Scribus: fetching upstream app icon {SCRIBUS_ARTWORK_URL}', flush=True)
    domain = None
    if parsed.hostname in ('www.google.com', 'google.com') and parsed.path == '/s2/favicons':
        qs = parse_qs(parsed.query)
        domain = qs.get('domain', [None])[0]
        if domain is None:
            domain_url = qs.get('domain_url', [None])[0]
            domain = urlparse(domain_url).hostname if domain_url else None
        if not domain or '/' in domain or '@' in domain or ':' in domain:
            raise RuntimeError(f'{name}: malformed publisher domain in icon URL {url!r}')

    if name == 'Scribus':
        domain = None  # Never fall back to scribus.net's generic S favicon.
    if domain:
        hosts = [domain] + ([domain[4:]] if domain.startswith('www.') else [])
        for host in hosts:
            base = f'https://{host}/'
            # Prefer large authentic publisher artwork over 16px favicons.
            candidates.extend(urljoin(base, item) for item in (
                'apple-touch-icon.png', 'apple-touch-icon-precomposed.png',
                'favicon-192x192.png', 'android-chrome-192x192.png',
                'favicon.png', 'favicon.ico'))

            class IconLinks(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.links = []
                def handle_starttag(self, tag, attrs):
                    if tag.lower() != 'link':
                        return
                    attr = dict(attrs)
                    rel = attr.get('rel', '').lower()
                    href = attr.get('href', '')
                    if 'icon' in rel and href:
                        self.links.append(urljoin(base, href))
            try:
                request = urllib.request.Request(base, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(request, timeout=10) as response:
                    html = response.read(1024 * 1024).decode('utf-8', 'replace')
                parser = IconLinks()
                parser.feed(html)
                candidates = parser.links + candidates
            except (urllib.error.URLError, OSError, ValueError):
                pass
    # Google's generic letter tiles are not the publisher's application logo.
    # For Google-proxy CSV entries, require an image hosted by the publisher.
    if not domain and name != 'Scribus':
        candidates.append(url)
    elif domain:
        print(f'[icons] {name}: ignoring Google favicon proxy; checking publisher-hosted assets', flush=True)

    errors = []
    best = None
    best_score = -1
    for candidate in dict.fromkeys(candidates):
        try:
            request = urllib.request.Request(candidate, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'image/png,image/webp,image/svg+xml,image/*;q=0.9,*/*;q=0.1',
            })
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise ValueError('icon exceeds 4 MiB')
            with Image.open(io.BytesIO(data)) as decoded:
                # ICO often has multiple resolutions; Pillow defaults to the largest.
                decoded.load()
                source = decoded.convert('RGBA')
            bbox = source.getchannel('A').getbbox()
            if bbox is None:
                raise ValueError('fully transparent image')
            score = min(source.width, source.height)
            # Tiny images cannot become detailed logos through upscaling.
            if score > best_score:
                best, best_score = source, score
                print(f'[icons] {name}: publisher artwork {source.width}x{source.height} from {candidate}', flush=True)
            if best_score >= 256:
                break
        except (urllib.error.URLError, ValueError, OSError) as exc:
            errors.append(f'{candidate}: {exc}')
    if best is None:
        raise RuntimeError(f'{name}: no publisher icon available:\n  ' + '\n  '.join(errors))
    if name == 'Scribus':
        if best_score < 256:
            raise RuntimeError(f'Scribus: upstream application icon is only {best_score}px; refusing to install a favicon')
        print(f'[icons] Scribus: VERIFIED upstream app artwork ({best_score}px); website favicons forbidden', flush=True)
    if best_score < 64:
        print(f'[icons] WARNING: {name} highest-resolution publisher icon is only {best_score}px; display quality may suffer', flush=True)
    return best


def import_icons(rows, force=False):
    from PIL import Image, ImageDraw, ImageOps
    DEST.mkdir(parents=True, exist_ok=True)
    for name in APPS:
        url = rows[name].get('Icon URL', '').strip()
        if not url.startswith('https://'):
            raise RuntimeError(f'{name}: invalid/missing Icon URL in CSV: {url!r}')
        slug = name.lower()
        original = DEST / f'unforked-{slug}.png'
        installer = DEST / f'install-{slug}.png'
        revision = IMPORTER_REVISION if name == 'Scribus' else 'v4'
        if not force and up_to_date(original, url, f'original-{revision}') and up_to_date(installer, url, f'installer-{revision}'):
            print(f'[icons] {name}: publisher icon cached ({revision})', flush=True)
            continue
        source = fetch_publisher_image(url, name)
        # Publisher favicons often arrive as 16x16 or 32x32 artwork.
        # thumbnail() only shrinks; it leaves those icons microscopic in KDE.
        # Scale the *visible* artwork to the icon canvas, retaining its aspect
        # ratio and a little breathing room. Never stretch non-uniformly.
        bounds = source.getchannel('A').getbbox()
        if bounds is None:
            raise RuntimeError(f'{name}: publisher icon is fully transparent')
        source = source.crop(bounds)
        max_side = 232
        scale = min(max_side / source.width, max_side / source.height)
        size = (max(1, round(source.width * scale)),
                max(1, round(source.height * scale)))
        source = source.resize(size, Image.Resampling.LANCZOS)
        image = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
        image.alpha_composite(source, ((256 - source.width)//2, (256 - source.height)//2))
        installer_image = image.copy()
        draw = ImageDraw.Draw(installer_image)
        draw.ellipse((151, 151, 250, 250), fill='#f2f6fb', outline='#244767', width=5)
        draw.rounded_rectangle((172, 174, 230, 214), radius=7, fill='#1764a2')
        draw.rectangle((196, 155, 205, 194), fill='white')
        draw.polygon([(183, 187), (219, 187), (201, 205)], fill='white')
        save_png(image, original, url, f'original-{revision}')
        save_png(installer_image, installer, url, f'installer-{revision}')
        print(f'[icons] {name}: imported authentic publisher icon and installer variant')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--force', action='store_true', help='refresh even cached publisher icons')
    args = ap.parse_args()
    print(f'[icons] IMPORTER {IMPORTER_REVISION} ACTIVE: {Path(__file__).resolve()}', flush=True)
    print('[icons] source: CFOS-owned publisher artwork URLs', flush=True)
    import_icons(get_rows(), args.force)
    print(f'[icons] IMPORTER {IMPORTER_REVISION} COMPLETE: all 16 publisher icons ready', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        sys.exit(f'[icons] ERROR: {exc}')
