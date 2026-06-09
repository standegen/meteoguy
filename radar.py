#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Image radar de précipitations (RainViewer) sur fond de carte (CartoDB).

RainViewer (gratuit) plafonne ses tuiles radar au zoom 5. On affiche donc un
fond de carte net au zoom voulu, et on ré-échantillonne (lisse) la couche radar
du zoom 5 par-dessus la même emprise géographique.
"""
import io, math, json, datetime, urllib.request
from PIL import Image

UA = {"User-Agent": "MeteoGuy/1.0 (perso)"}
BASEMAP = "https://a.basemaps.cartocdn.com/light_all/%d/%d/%d.png"
RADAR_ZOOM = 5          # zoom maximal supporté par le radar RainViewer gratuit


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _deg2num(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def _num2deg(x, y, z):
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def _meta():
    meta = json.loads(_get("https://api.rainviewer.com/public/weather-maps.json"))
    r = meta["radar"]
    return meta["host"], r["past"], r.get("nowcast", [])


def _basemap(center, zoom, tiles):
    cx, cy = _deg2num(center[0], center[1], zoom)
    tw, th = tiles
    x0, y0 = int(cx) - tw // 2, int(cy) - th // 2
    W, H = tw * 256, th * 256
    canvas = Image.new("RGBA", (W, H), (235, 235, 235, 255))
    for dx in range(tw):
        for dy in range(th):
            X, Y = x0 + dx, y0 + dy
            if not (0 <= X < 2 ** zoom and 0 <= Y < 2 ** zoom):
                continue
            try:
                canvas.paste(Image.open(io.BytesIO(_get(BASEMAP % (zoom, X, Y)))).convert("RGBA"),
                             (dx * 256, dy * 256))
            except Exception:
                pass
    return canvas, x0, y0, W, H


def _radar_overlay(host, path, zoom, tiles, x0, y0, W, H):
    tw, th = tiles
    lat_tl, lon_tl = _num2deg(x0, y0, zoom)
    lat_br, lon_br = _num2deg(x0 + tw, y0 + th, zoom)
    rxa, rya = [v * 256 for v in _deg2num(lat_tl, lon_tl, RADAR_ZOOM)]
    rxb, ryb = [v * 256 for v in _deg2num(lat_br, lon_br, RADAR_ZOOM)]
    txm, txM = int(rxa // 256), int(rxb // 256)
    tym, tyM = int(rya // 256), int(ryb // 256)
    mosaic = Image.new("RGBA", ((txM - txm + 1) * 256, (tyM - tym + 1) * 256), (0, 0, 0, 0))
    for tx in range(txm, txM + 1):
        for ty in range(tym, tyM + 1):
            try:
                mosaic.paste(Image.open(io.BytesIO(_get(
                    "%s%s/256/%d/%d/%d/4/1_1.png" % (host, path, RADAR_ZOOM, tx, ty)))).convert("RGBA"),
                    ((tx - txm) * 256, (ty - tym) * 256))
            except Exception:
                pass
    patch = mosaic.crop((int(rxa - txm * 256), int(rya - tym * 256),
                         int(round(rxb - txm * 256)), int(round(ryb - tym * 256))))
    return patch.resize((W, H), Image.BILINEAR)


def _render(center, zoom, tiles):
    """Fond de carte + dernière trame radar observée. -> (canvas,x0,y0,W,H,ts)."""
    host, past, _ = _meta()
    frame = past[-1]
    canvas, x0, y0, W, H = _basemap(center, zoom, tiles)
    canvas.alpha_composite(_radar_overlay(host, frame["path"], zoom, tiles, x0, y0, W, H))
    return canvas, x0, y0, W, H, frame["time"]


def _px(la, lo, x0, y0, zoom):
    px, py = _deg2num(la, lo, zoom)
    return int((px - x0) * 256), int((py - y0) * 256)


def radar_png(center, zones=None, zoom=8, tiles=(3, 3)):
    """center=(lat,lon). Renvoie (png_bytes, unix_ts de l'observation radar)."""
    from PIL import ImageDraw
    canvas, x0, y0, W, H, ts = _render(center, zoom, tiles)
    draw = ImageDraw.Draw(canvas)
    for name, (la, lo) in (zones or {}).items():
        ix, iy = _px(la, lo, x0, y0, zoom)
        if 0 <= ix < W and 0 <= iy < H:
            draw.ellipse([ix - 5, iy - 5, ix + 5, iy + 5],
                         outline=(0, 0, 0, 255), width=2, fill=(255, 60, 0, 255))
            draw.text((ix + 8, iy - 5), name.split(" (")[0], fill=(0, 0, 0, 255))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue(), ts


def impact_map(slat, slon, zlat, zlon, zone_name, dist_km):
    """Carte d'un impact de foudre : zone (rouge) + impact ⚡ (jaune). -> png bytes."""
    from PIL import ImageDraw
    center = ((slat + zlat) / 2, (slon + zlon) / 2)
    canvas, x0, y0, W, H, ts = _render(center, zoom=9, tiles=(3, 3))
    draw = ImageDraw.Draw(canvas)
    # zone
    zx, zy = _px(zlat, zlon, x0, y0, zoom=9)
    draw.ellipse([zx - 6, zy - 6, zx + 6, zy + 6], outline=(0, 0, 0, 255), width=2,
                 fill=(255, 60, 0, 255))
    draw.text((zx + 9, zy - 6), zone_name.split(" (")[0], fill=(0, 0, 0, 255))
    # impact foudre
    ix, iy = _px(slat, slon, x0, y0, zoom=9)
    draw.line([zx, zy, ix, iy], fill=(0, 0, 0, 160), width=2)
    draw.ellipse([ix - 8, iy - 8, ix + 8, iy + 8], outline=(120, 90, 0, 255), width=2,
                 fill=(255, 230, 0, 255))
    draw.text((ix + 10, iy - 6), "⚡ %.0f km" % dist_km, fill=(0, 0, 0, 255))
    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def radar_gif(center, zones=None, zoom=8, tiles=(3, 3), past=8):
    """Animation GIF : `past` dernières trames observées + nowcast (trajectoire des cellules)."""
    from PIL import ImageDraw
    host, past_fr, now_fr = _meta()
    frames = past_fr[-past:] + now_fr
    base, x0, y0, W, H = _basemap(center, zoom, tiles)
    imgs = []
    for fr in frames:
        c = base.copy()
        c.alpha_composite(_radar_overlay(host, fr["path"], zoom, tiles, x0, y0, W, H))
        d = ImageDraw.Draw(c)
        for name, (la, lo) in (zones or {}).items():
            ix, iy = _px(la, lo, x0, y0, zoom)
            if 0 <= ix < W and 0 <= iy < H:
                d.ellipse([ix - 5, iy - 5, ix + 5, iy + 5],
                          outline=(0, 0, 0, 255), width=2, fill=(255, 60, 0, 255))
        tag = datetime.datetime.fromtimestamp(fr["time"]).strftime("%Hh%M")
        nowcast = fr in now_fr
        d.rectangle([0, 0, 150, 22], fill=(0, 0, 0, 180))
        d.text((5, 5), ("➡️ " if nowcast else "") + tag + (" (prévu)" if nowcast else ""),
               fill=(255, 255, 255, 255))
        imgs.append(c.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=128))
    out = io.BytesIO()
    imgs[0].save(out, format="GIF", save_all=True, append_images=imgs[1:],
                 duration=450, loop=0, disposal=2, optimize=True)
    return out.getvalue(), past_fr[-1]["time"]


def radar_caption(ts):
    t = datetime.datetime.fromtimestamp(ts).strftime("%Hh%M")
    return ("📡 <b>Radar précipitations</b> — observation de <b>%s</b>\n"
            "<i>Sablons · Grury · Lapeyrouse-Mornay · source RainViewer</i>" % t)
