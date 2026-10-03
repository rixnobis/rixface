#!/usr/bin/env python3
"""rix-face-v2.svg -> PS1 GPU primitives (C++ header for a psyqo app).

Handles exactly what this SVG uses: absolute M/L/C/Z paths, flat fills,
vertical linearGradient fills (objectBoundingBox), strokes, two blur
filters (approximated as additive semi-transparent halos), one opacity.

Usage: svg2ps1.py in.svg out.hh [preview.png]
"""
import math
import re
import sys
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/2000/svg}"
SCALE = 0.62
CX, CY = 256.0, 231.0  # SVG point mapped to screen centre
SX, SY = 160.0, 120.0
CURVE_STEPS = 12


def xf(p):
    return (SX + (p[0] - CX) * SCALE, SY + (p[1] - CY) * SCALE)


def hexcol(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def parse_path(d):
    """Returns (list of polylines in SVG coords, closed flags)."""
    toks = re.findall(r"[MLCZmlcz]|-?\d*\.?\d+", d)
    i, cmd = 0, None
    subs, cur, closed = [], [], []
    def num():
        nonlocal i
        v = float(toks[i]); i += 1
        return v
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t; i += 1
            if cmd in "Zz":
                closed.append(True); subs.append(cur); cur = []
                continue
            if cmd.islower():
                raise ValueError("relative path commands not supported: " + cmd)
        if cmd == "M":
            if cur:
                subs.append(cur); closed.append(False)
            cur = [(num(), num())]
            cmd = "L"
        elif cmd == "L":
            cur.append((num(), num()))
        elif cmd == "C":
            p0 = cur[-1]
            p1 = (num(), num()); p2 = (num(), num()); p3 = (num(), num())
            for k in range(1, CURVE_STEPS + 1):
                u = k / CURVE_STEPS; v = 1 - u
                cur.append((v**3 * p0[0] + 3 * v * v * u * p1[0] + 3 * v * u * u * p2[0] + u**3 * p3[0],
                            v**3 * p0[1] + 3 * v * v * u * p1[1] + 3 * v * u * u * p2[1] + u**3 * p3[1]))
        else:
            raise ValueError("unhandled path command " + str(cmd))
    if cur:
        subs.append(cur); closed.append(False)
    out = []
    for s, c in zip(subs, closed):
        # drop duplicate closing vertex
        if c and len(s) > 1 and math.dist(s[0], s[-1]) < 1e-6:
            s = s[:-1]
        out.append((s, c))
    return out


def area(poly):
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))


def clip_y(poly, y0, keep_below):
    """Sutherland-Hodgman against a horizontal line. keep_below: keep y >= y0."""
    def inside(p):
        return p[1] >= y0 if keep_below else p[1] <= y0
    out = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        ia, ib = inside(a), inside(b)
        if ia:
            out.append(a)
        if ia != ib:
            t = (y0 - a[1]) / (b[1] - a[1])
            out.append((a[0] + t * (b[0] - a[0]), y0))
    return out


def ear_clip(poly):
    pts = list(poly)
    # remove collinear / duplicate points
    def cleanup(p):
        changed = True
        while changed and len(p) > 3:
            changed = False
            for k in range(len(p)):
                a, b, c = p[k - 1], p[k], p[(k + 1) % len(p)]
                if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) < 1e-9:
                    p.pop(k); changed = True; break
        return p
    pts = cleanup(pts)
    if len(pts) < 3:
        return []
    if area(pts) < 0:
        pts.reverse()
    tris = []
    def pin(p, a, b, c):
        def s(p1, p2, p3):
            return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
        d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))
    guard = 0
    while len(pts) > 3 and guard < 10000:
        guard += 1
        n = len(pts)
        for k in range(n):
            a, b, c = pts[k - 1], pts[k], pts[(k + 1) % n]
            cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if cross <= 0:
                continue
            if any(pin(p, a, b, c) for p in pts if p not in (a, b, c)):
                continue
            tris.append((a, b, c)); pts.pop(k); break
        else:
            raise RuntimeError("ear clipping stuck with %d points" % len(pts))
    tris.append(tuple(pts))
    return tris


class Grad:
    def __init__(self, el):
        self.stops = []
        for s in el.findall(NS + "stop"):
            self.stops.append((float(s.get("offset")), hexcol(s.get("stop-color"))))
    def at(self, t):
        t = min(1.0, max(0.0, t))
        for (o0, c0), (o1, c1) in zip(self.stops, self.stops[1:]):
            if t <= o1:
                u = 0 if o1 == o0 else (t - o0) / (o1 - o0)
                return tuple(round(a + (b - a) * u) for a, b in zip(c0, c1))
        return self.stops[-1][1]


def fill_tris(poly, paint, grads):
    """Triangles with per-vertex colour, polygon split at gradient stops."""
    if paint.startswith("url("):
        g = grads[paint[5:-1]]
        ys = [p[1] for p in poly]
        y0, y1 = min(ys), max(ys)
        pieces = [poly]
        for off, _ in g.stops[1:-1]:
            yc = y0 + off * (y1 - y0)
            nxt = []
            for pc in pieces:
                a = clip_y(pc, yc, False)
                b = clip_y(pc, yc, True)
                nxt += [x for x in (a, b) if len(x) >= 3]
            pieces = nxt
        out = []
        for pc in pieces:
            for t in ear_clip(pc):
                out.append([(p, g.at((p[1] - y0) / (y1 - y0))) for p in t])
        return out
    col = hexcol(paint)
    return [[(p, col) for p in t] for t in ear_clip(poly)]


def stroke_quads(pts, closed, width):
    """One quad per segment, extended by half-width at each end to cover joins."""
    segs = list(zip(pts, pts[1:] + (pts[:1] if closed else [])))
    hw = width / 2.0
    quads = []
    for a, b in segs:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        ux, uy = dx / L, dy / L
        nx, ny = -uy * hw, ux * hw
        a2 = (a[0] - ux * hw, a[1] - uy * hw)
        b2 = (b[0] + ux * hw, b[1] + uy * hw)
        # Z order for psyqo Quad: A B top edge, C D bottom edge
        quads.append(((a2[0] + nx, a2[1] + ny), (b2[0] + nx, b2[1] + ny),
                      (a2[0] - nx, a2[1] - ny), (b2[0] - nx, b2[1] - ny)))
    return quads


def scale_about(poly, fy, fx):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    cx = (min(xs) + max(xs)) / 2; cy = (min(ys) + max(ys)) / 2
    return [(cx + (p[0] - cx) * fx, cy + (p[1] - cy) * fy) for p in poly]


GREEN_VISOR = None  # filled in main


def main():
    src, out = sys.argv[1], sys.argv[2]
    preview = sys.argv[3] if len(sys.argv) > 3 else None
    root = ET.parse(src).getroot()
    grads = {g.get("id"): Grad(g) for g in root.iter(NS + "linearGradient")}
    # Green visor: same shape of gradient, canon "green under focus".
    gv = Grad(ET.fromstring('<linearGradient xmlns="http://www.w3.org/2000/svg">'
                            '<stop offset="0" stop-color="#B4FFC4"/><stop offset="0.5" stop-color="#46E86A"/>'
                            '<stop offset="1" stop-color="#16943A"/></linearGradient>'))
    grads["visor_green"] = gv

    base, visor_fill, halo, traces = [], {}, {}, []
    bg = hexcol(root.find(NS + "rect").get("fill"))
    # The GPU fill is not dithered, and #14171A truncates to 15-bit (2,2,3)
    # which reads blue. Neutral (3,3,3) is the nearest grey.
    bg = (24, 24, 24)
    trace_idx = 0

    def emit_path(el, inherited, in_traces):
        nonlocal trace_idx
        a = dict(inherited); a.update(el.attrib)
        subs = parse_path(a["d"])
        fill = a.get("fill", "#000000")
        stroke = a.get("stroke")
        sw = float(a.get("stroke-width", "1"))
        filt = a.get("filter", "")
        for pts, closed in subs:
            if in_traces:
                quads = stroke_quads([xf(p) for p in pts], closed, max(1.0, sw * SCALE))
                glow = stroke_quads([xf(p) for p in pts], closed, sw * SCALE + 4.0)
                traces.append({"id": trace_idx, "core": quads, "glow": glow,
                               "lit": "filter" in a, "color": hexcol(stroke)})
                continue
            if fill != "none" and closed:
                if fill == "url(#visor)":
                    poly = [xf(p) for p in pts]
                    visor_fill["amber"] = fill_tris(poly, "url(#visor)", grads)
                    visor_fill["green"] = fill_tris(poly, "url(#visor_green)", grads)
                    # glow: blur stdDev 6 approximated by two widened additive copies
                    halo["polys"] = [scale_about(poly, 1.9, 1.06), scale_about(poly, 1.4, 1.03)]
                else:
                    base.extend(("tri", t) for t in fill_tris([xf(p) for p in pts], fill, grads))
            if stroke and stroke != "none":
                w = max(1.0, sw * SCALE)
                for q in stroke_quads([xf(p) for p in pts], closed, w):
                    base.append(("quad", q, hexcol(stroke)))
        if in_traces:
            trace_idx += 1

    def walk(el, inherited, in_traces):
        for ch in el:
            tag = ch.tag.replace(NS, "")
            if tag == "g":
                a = dict(inherited); a.update(ch.attrib)
                walk(ch, a, True)
            elif tag == "path":
                if "url(#visor)" in ch.get("fill", ""):
                    visor_index = len(base)
                    emit_path(ch, inherited, in_traces)
                    halo["at"] = visor_index
                else:
                    emit_path(ch, inherited, in_traces)
    walk(root, {}, False)

    def i16(v):
        return int(round(v))

    def rgb(c):
        return "0x%06x" % (c[0] | (c[1] << 8) | (c[2] << 16))

    L = []
    L.append("// Generated by ~/rix-assets/face/ps1/svg2ps1.py from rix-face-v2.svg. Do not edit.")
    L.append("#pragma once\n#include <stdint.h>\n")
    L.append("struct FaceTri { int16_t ax, ay, bx, by, cx, cy; uint32_t ca, cb, cc; };")
    L.append("struct FaceQuad { int16_t ax, ay, bx, by, cx, cy, dx, dy; uint32_t c; };")
    L.append("struct FaceTrace { uint16_t coreStart, coreCount, glowStart, glowCount; };\n")
    L.append("static constexpr uint32_t c_faceBg = %s;" % rgb(bg))

    def tri_line(t):
        (pa, ca), (pb, cb), (pc, cc) = t
        return "    {%d, %d, %d, %d, %d, %d, %s, %s, %s}," % (
            i16(pa[0]), i16(pa[1]), i16(pb[0]), i16(pb[1]), i16(pc[0]), i16(pc[1]), rgb(ca), rgb(cb), rgb(cc))

    def quad_line(q, c):
        return "    {%s, %s}," % (", ".join("%d, %d" % (i16(p[0]), i16(p[1])) for p in q), rgb(c))

    # base split: everything before the visor fill, then everything after (none expected)
    tris = [b for b in base if b[0] == "tri"]
    quads = [b for b in base if b[0] == "quad"]
    # draw order matters: emit base as an interleaved op list
    L.append("// op list: kind 0 = tri index, 1 = quad index")
    L.append("struct FaceOp { uint8_t kind; uint16_t index; };")
    ops, ti, qi = [], 0, 0
    for b in base:
        if b[0] == "tri":
            ops.append((0, ti)); ti += 1
        else:
            ops.append((1, qi)); qi += 1
    L.append("static const FaceTri c_faceTris[] = {"); L += [tri_line(b[1]) for b in tris]; L.append("};")
    L.append("static const FaceQuad c_faceQuads[] = {"); L += [quad_line(b[1], b[2]) for b in quads]; L.append("};")
    L.append("static const FaceOp c_faceOps[] = {"); L += ["    {%d, %d}," % o for o in ops]; L.append("};")
    for name in ("amber", "green"):
        L.append("static const FaceTri c_visor_%s[] = {" % name)
        L += [tri_line(t) for t in visor_fill[name]]
        L.append("};")
    # halo geometry as flat triangles (colour applied at runtime, additive)
    halo_tris = []
    for k, poly in enumerate(halo["polys"]):
        for t in ear_clip(poly):
            halo_tris.append(t)
    L.append("static const FaceTri c_visorHalo[] = {")
    L += ["    {%d, %d, %d, %d, %d, %d, 0, 0, 0}," % (i16(a[0]), i16(a[1]), i16(b[0]), i16(b[1]), i16(c[0]), i16(c[1]))
          for a, b, c in halo_tris]
    L.append("};")
    tq, entries, gq = [], [], []
    for t in traces:
        cs = len(tq); tq += t["core"]
        gs = len(gq); gq += t["glow"]
        entries.append((cs, len(t["core"]), gs, len(t["glow"])))
    L.append("static const FaceQuad c_traceCore[] = {"); L += [quad_line(q, (0, 0, 0)) for q in tq]; L.append("};")
    L.append("static const FaceQuad c_traceGlow[] = {"); L += [quad_line(q, (0, 0, 0)) for q in gq]; L.append("};")
    L.append("static const FaceTrace c_traces[] = {"); L += ["    {%d, %d, %d, %d}," % e for e in entries]; L.append("};")
    L.append("static constexpr unsigned c_traceCount = %d;" % len(entries))
    L.append("// SVG catching-stage lit set, for the reference still: " +
             ", ".join(str(t["id"]) for t in traces if t["lit"]))
    open(out, "w").write("\n".join(L) + "\n")
    print("tris %d quads %d visor %d/%d halo %d traces %d (core quads %d, glow quads %d)" % (
        len(tris), len(quads), len(visor_fill["amber"]), len(visor_fill["green"]), len(halo_tris),
        len(entries), len(tq), len(gq)))

    if preview:
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (320, 240), bg)
        dr = ImageDraw.Draw(im)
        def ptri(t):
            # flat preview using centroid colour (good enough for layout checks)
            c = tuple(sum(v[1][k] for v in t) // 3 for k in range(3))
            dr.polygon([v[0] for v in t], fill=c)
        for b in base:
            if b[0] == "tri":
                ptri(b[1])
            else:
                q = b[1]; dr.polygon([q[0], q[1], q[3], q[2]], fill=b[2])
        for t in visor_fill["amber"]:
            ptri(t)
        for t in traces:
            for q in t["core"]:
                dr.polygon([q[0], q[1], q[3], q[2]], fill=t["color"])
        im.resize((960, 720), Image.NEAREST).save(preview)


if __name__ == "__main__":
    main()
