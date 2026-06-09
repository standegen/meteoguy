#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
foudre.py — Détection temps réel d'impacts de foudre < RAYON_KM des 3 zones,
via le broker MQTT communautaire Blitzortung (celui de l'intégration Home Assistant).
Dépendance : paho-mqtt. Geohash en Python pur. Usage personnel (CGU Blitzortung).
"""
import json, math, threading
import paho.mqtt.client as mqtt

BROKER_HOST = "blitzortung.ha.sed.pl"
BROKER_PORT = 1883
RAYON_KM = 50.0

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def gh_encode(lat, lon, precision=12):
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    gh, bit, even, ch = [], 0, True, 0
    while len(gh) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon > mid: ch |= (1 << (4 - bit)); lon_lo = mid
            else: lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat > mid: ch |= (1 << (4 - bit)); lat_lo = mid
            else: lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            gh.append(_BASE32[ch]); bit, ch = 0, 0
    return "".join(gh)


def gh_bbox(gh):
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    even = True
    for c in gh:
        cd = _BASE32.index(c)
        for mask in (16, 8, 4, 2, 1):
            if even:
                mid = (lon_lo + lon_hi) / 2
                if cd & mask: lon_lo = mid
                else: lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if cd & mask: lat_lo = mid
                else: lat_hi = mid
            even = not even
    return lat_lo, lon_lo, lat_hi, lon_hi


def _adj(gh, d):
    gh = gh.lower(); last = gh[-1]; parent = gh[:-1]
    nb = {"right": "bc01fg45238967deuvhjyznpkmstqrwx",
          "left": "238967debc01fg45kmstqrwxuvhjyznp",
          "top": ["p0r21436x8zb9dcf5h7kjnmqesgutwvy", "bc01fg45238967deuvhjyznpkmstqrwx"],
          "bottom": ["14365h7k9dcfesgujnmqp0r2twvyx8zb", "238967debc01fg45kmstqrwxuvhjyznp"]}
    bd = {"right": "bcfguvyz", "left": "0145hjnp",
          "top": ["prxz", "bcfguvyz"], "bottom": ["028b", "0145hjnp"]}
    typ = len(gh) % 2
    table = nb[d][typ] if isinstance(nb[d], list) else nb[d]
    border = bd[d][typ] if isinstance(bd[d], list) else bd[d]
    if last in border and parent:
        parent = _adj(parent, d)
    return parent + _BASE32[table.index(last)]


def gh_neighbors(gh):
    t, b = _adj(gh, "top"), _adj(gh, "bottom")
    return [t, b, _adj(gh, "right"), _adj(gh, "left"),
            _adj(t, "right"), _adj(t, "left"), _adj(b, "right"), _adj(b, "left")]


def _bbox_point(lat, lon, r):
    lat_d = r * 360 / 40000
    lon_d = lat_d / math.cos(math.radians(lat))
    return lat - lat_d, lon - lon_d, lat + lat_d, lon + lon_d


def _ov(a1, a2, b1, b2):
    return a1 <= b2 and b1 <= a2


def _box_ov(b1, b2):
    return _ov(b1[0], b1[2], b2[0], b2[2]) and _ov(b1[1], b1[3], b2[1], b2[3])


def tuiles_point(lat, lon, r):
    bounds = _bbox_point(lat, lon, r)
    result = set()
    for precision in range(1, 13):
        center = gh_encode(lat, lon, precision)
        stack, checked = {center}, {center}
        while stack:
            cur = stack.pop()
            for n in gh_neighbors(cur):
                if n not in checked and _box_ov(gh_bbox(n), bounds):
                    stack.add(n); checked.add(n)
        if len(checked) <= 9:
            result = checked
        else:
            break
    return result


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def build_topics(points, r):
    topics = set()
    for p in points:
        for gh in tuiles_point(p["lat"], p["lon"], r):
            topics.add("blitzortung/1.1/" + "/".join(gh) + "/#")
    return sorted(topics)


class FoudreWatcher:
    def __init__(self, points, on_impact, rayon_km=RAYON_KM):
        self.points = points
        self.on_impact = on_impact
        self.rayon = rayon_km
        self.topics = build_topics(points, rayon_km)
        self._seen = {}
        try:                       # paho 2.x
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                                      protocol=mqtt.MQTTv311)
        except (AttributeError, TypeError):  # paho 1.x
            self.client = mqtt.Client(protocol=mqtt.MQTTv311)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)

    def _on_connect(self, c, u, f, rc, *a):
        print("foudre: connecté MQTT rc=%s, %d topics" % (rc, len(self.topics)), flush=True)
        for t in self.topics:
            c.subscribe(t, qos=0)

    def _on_message(self, c, u, msg):
        try:
            d = json.loads(msg.payload)
            lat, lon = float(d["lat"]), float(d["lon"])
        except (ValueError, KeyError, TypeError):
            return
        ts = d.get("time", 0) / 1e9
        key = (round(lat, 4), round(lon, 4), d.get("time"))
        if key in self._seen:
            return
        self._seen[key] = True
        if len(self._seen) > 5000:
            self._seen.clear()
        for p in self.points:
            dist = haversine_km(p["lat"], p["lon"], lat, lon)
            if dist <= self.rayon:
                try:
                    self.on_impact(p, lat, lon, ts, round(dist, 1))
                except Exception as e:
                    print("foudre: erreur callback:", e, flush=True)
                break

    def run_forever(self):
        while True:
            try:
                self.client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
                self.client.loop_forever(retry_first_connection=True)
            except Exception as e:
                print("foudre: boucle MQTT interrompue (%s), retry 5s" % e, flush=True)
                threading.Event().wait(5)

    def start_background(self):
        t = threading.Thread(target=self.run_forever, daemon=True)
        t.start()
        return t
