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


def radar_png(center, zones=None, zoom=8, tiles=(3, 3)):
    """center=(lat,lon). Renvoie (png_bytes, unix_ts de l'observation radar)."""
    from PIL import ImageDraw
    meta = json.loads(_get("https://api.rainviewer.com/public/weather-maps.json"))
    host, frame = meta["host"], meta["radar"]["past"][-1]
    path, ts = frame["path"], frame["time"]

    cx, cy = _deg2num(center[0], center[1], zoom)
    tw, th = tiles
    x0, y0 = int(cx) - tw // 2, int(cy) - th // 2
    W, H = tw * 256, th * 256

    # 1) Fond de carte net au zoom demandé
    canvas = Image.new("RGBA", (W, H), (235, 235, 235, 255))
    for dx in range(tw):
        for dy in range(th):
            X, Y = x0 + dx, y0 + dy
            if not (0 <= X < 2 ** zoom and 0 <= Y < 2 ** zoom):
                continue
            try:
                b = Image.open(io.BytesIO(_get(BASEMAP % (zoom, X, Y)))).convert("RGBA")
                canvas.paste(b, (dx * 256, dy * 256))
            except Exception:
                pass

    # 2) Couche radar (zoom 5) ré-échantillonnée sur la même emprise
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
                rt = Image.open(io.BytesIO(_get(
                    "%s%s/256/%d/%d/%d/4/1_1.png" % (host, path, RADAR_ZOOM, tx, ty)))).convert("RGBA")
                mosaic.paste(rt, ((tx - txm) * 256, (ty - tym) * 256))
            except Exception:
                pass
    patch = mosaic.crop((int(rxa - txm * 256), int(rya - tym * 256),
                         int(round(rxb - txm * 256)), int(round(ryb - tym * 256))))
    patch = patch.resize((W, H), Image.BILINEAR)
    canvas.alpha_composite(patch)

    # 3) Marqueurs des zones
    draw = ImageDraw.Draw(canvas)
    for name, (la, lo) in (zones or {}).items():
        px, py = _deg2num(la, lo, zoom)
        ix, iy = int((px - x0) * 256), int((py - y0) * 256)
        if 0 <= ix < W and 0 <= iy < H:
            draw.ellipse([ix - 5, iy - 5, ix + 5, iy + 5],
                         outline=(0, 0, 0, 255), width=2, fill=(255, 60, 0, 255))
            draw.text((ix + 8, iy - 5), name.split(" (")[0], fill=(0, 0, 0, 255))

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG")
    return out.getvalue(), ts


def radar_caption(ts):
    t = datetime.datetime.fromtimestamp(ts).strftime("%Hh%M")
    return ("📡 <b>Radar précipitations</b> — observation de <b>%s</b>\n"
            "<i>Sablons · Grury · Lapeyrouse-Mornay · source RainViewer</i>" % t)
