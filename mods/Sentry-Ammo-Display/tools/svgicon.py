"""把游戏资源里的矢量图标光栅化成战备图标格式。

战备图标的纹理是 256×256 RGB：红通道是本体、绿通道是符号层，蓝通道恒为 0。
新版战备在原版里没有对应纹理，图标只存在于 xaml 的 Path Data 里（WPF 路径语法，
与 SVG 的 d 基本一致），这里自己扫线填充出两层掩膜，再拼成同样的三通道图。

只支持绝对/相对的直线与贝塞尔、圆弧，以及 F0/F1 两种填充规则 —— 图标资源里
用到的就这些；没有 transform（Canvas.Left/Top 全是 -0）。
"""

import math
import re

import numpy as np
from PIL import Image

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _numbers(text):
    return [float(m.group(0)) for m in _NUM.finditer(text)]


def _arc_points(x0, y0, rx, ry, angle, large, sweep, x1, y1, steps=24):
    """端点参数化的圆弧 → 折线点。"""
    if rx == 0 or ry == 0:
        return [(x1, y1)]
    phi = math.radians(angle)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    rx, ry = abs(rx), abs(ry)
    scale = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if scale > 1:
        k = math.sqrt(scale)
        rx, ry = rx * k, ry * k
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(num / den, 0.0)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x0 + x1) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y0 + y1) / 2.0
    theta = math.atan2((y1p - cyp) / ry, (x1p - cxp) / rx)
    delta = math.atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx) - theta
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    out = []
    for i in range(1, steps + 1):
        t = theta + delta * i / steps
        px = cos_p * rx * math.cos(t) - sin_p * ry * math.sin(t) + cx
        py = sin_p * rx * math.cos(t) + cos_p * ry * math.sin(t) + cy
        out.append((px, py))
    return out


def parse_path(d):
    """路径字符串 → (子路径列表, 填充规则)。子路径是点列。"""
    rule = "nonzero"
    body = d.strip()
    if body[:1] in ("F", "f") and body[1:2].isdigit():
        rule = "evenodd" if body[1] == "0" else "nonzero"
        body = body[2:]

    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", body)
    subs, current, start = [], [], None
    x = y = 0.0
    last_ctrl = None
    i, cmd = 0, None
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                if current:
                    subs.append(current)
                current = []
                if start:
                    x, y = start
                continue
        if cmd is None:
            break
        rel = cmd.islower()
        up = cmd.upper()
        need = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}[up]
        args = [float(t) for t in tokens[i:i + need]]
        if len(args) < need:
            break
        i += need

        if up == "M":
            nx, ny = (x + args[0], y + args[1]) if rel else (args[0], args[1])
            if current:
                subs.append(current)
            current = [(nx, ny)]
            start = (nx, ny)
            x, y = nx, ny
            cmd = "l" if rel else "L"
            last_ctrl = None
            continue

        if up == "L":
            nx, ny = (x + args[0], y + args[1]) if rel else (args[0], args[1])
            current.append((nx, ny))
            x, y = nx, ny
            last_ctrl = None
        elif up == "H":
            nx = x + args[0] if rel else args[0]
            current.append((nx, y))
            x = nx
            last_ctrl = None
        elif up == "V":
            ny = y + args[0] if rel else args[0]
            current.append((x, ny))
            y = ny
            last_ctrl = None
        elif up in ("C", "S"):
            if up == "C":
                c1 = (x + args[0], y + args[1]) if rel else (args[0], args[1])
                rest = args[2:]
            else:
                if last_ctrl:
                    c1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1])
                else:
                    c1 = (x, y)
                rest = args
            c2 = (x + rest[0], y + rest[1]) if rel else (rest[0], rest[1])
            end = (x + rest[2], y + rest[3]) if rel else (rest[2], rest[3])
            for k in range(1, 13):
                t = k / 12.0
                mt = 1 - t
                px = (mt ** 3) * x + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * end[0]
                py = (mt ** 3) * y + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * end[1]
                current.append((px, py))
            last_ctrl = c2
            x, y = end
        elif up in ("Q", "T"):
            if up == "Q":
                c1 = (x + args[0], y + args[1]) if rel else (args[0], args[1])
                end = (x + args[2], y + args[3]) if rel else (args[2], args[3])
            else:
                c1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
                end = (x + args[0], y + args[1]) if rel else (args[0], args[1])
            for k in range(1, 13):
                t = k / 12.0
                mt = 1 - t
                px = mt * mt * x + 2 * mt * t * c1[0] + t * t * end[0]
                py = mt * mt * y + 2 * mt * t * c1[1] + t * t * end[1]
                current.append((px, py))
            last_ctrl = c1
            x, y = end
        elif up == "A":
            end = (x + args[5], y + args[6]) if rel else (args[5], args[6])
            pts = _arc_points(x, y, args[0], args[1], args[2],
                              bool(int(args[3])), bool(int(args[4])), end[0], end[1])
            current.extend(pts)
            x, y = end
            last_ctrl = None

    if current:
        subs.append(current)
    return subs, rule


def fill(subpaths, rule, width, height, ss=4):
    """扫线填充，返回 (height, width) 的 0..1 覆盖度。"""
    big = np.zeros((height * ss, width * ss), bool)
    edges = []
    for pts in subpaths:
        n = len(pts)
        for k in range(n):
            x0, y0 = pts[k]
            x1, y1 = pts[(k + 1) % n]
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    if not edges:
        return big.astype(np.float32)

    ys = (np.arange(height * ss) + 0.5) / ss
    for row, y in enumerate(ys):
        hits = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= y < y1) or (y1 <= y < y0):
                t = (y - y0) / (y1 - y0)
                hits.append((x0 + t * (x1 - x0), 1 if y1 > y0 else -1))
        if not hits:
            continue
        hits.sort()
        wind = 0
        for k in range(len(hits) - 1):
            wind += hits[k][1]
            inside = (wind != 0) if rule == "nonzero" else (wind % 2 == 1)
            if not inside:
                continue
            xa = int(math.ceil(hits[k][0] * ss - 0.5))
            xb = int(math.floor(hits[k + 1][0] * ss - 0.5))
            if xb >= xa:
                big[row, max(xa, 0):min(xb + 1, width * ss)] = True
    return big.reshape(height, ss, width, ss).mean(axis=(1, 3)).astype(np.float32)


def _area(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def to_icon(paths, size=256, plate=0.9):
    """路径列表 → 256 方图，红=本体、绿=符号。

    plate 是铺满整块画布的那一层底色，占画布面积超过这个比例就当背景丢掉；
    剩下面积最大的一层当本体，其余细节都进符号层。
    """
    parsed = [(parse_path(d), fill_) for d, fill_ in paths]
    kept = []
    for (subs, rule), _ in parsed:
        box_area = max((_area(s) for s in subs), default=0.0)
        if box_area >= plate * size * size:
            continue
        kept.append((subs, rule))
    if not kept:
        raise SystemExit("图标里没有可用的路径")
    areas = [max(_area(s) for s in subs) for subs, _ in kept]
    body_i = int(np.argmax(areas))

    body = np.zeros((size, size), np.float32)
    mark = np.zeros((size, size), np.float32)
    for i, (subs, rule) in enumerate(kept):
        layer = fill(subs, rule, size, size)
        if i == body_i:
            body = np.maximum(body, layer)
        else:
            mark = np.maximum(mark, layer)
    mark = np.maximum(mark - body, 0) if False else mark

    out = np.zeros((size, size, 3), np.uint8)
    out[..., 0] = np.clip(body * 255, 0, 255).astype(np.uint8)
    out[..., 1] = np.clip(mark * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def to_icon_split(paths, body, symbol, size=256):
    """按下标把路径分进两个通道：body 进红、symbol 进绿，其余丢弃。

    SVG 里的六边形底板要单独扔掉，光靠面积判断分不开（它只占画布的七成）。
    """
    parsed = [parse_path(d) for d, _ in paths]

    def layer(indices):
        out = np.zeros((size, size), np.float32)
        for i in indices:
            subs, rule = parsed[i]
            out = np.maximum(out, fill(subs, rule, size, size))
        return out

    body_mask, mark = layer(body), layer(symbol)
    out = np.zeros((size, size, 3), np.uint8)
    out[..., 0] = np.clip(body_mask * 255, 0, 255).astype(np.uint8)
    out[..., 1] = np.clip(mark * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGB")


def paths_from_xaml(text, key):
    """从战斗图标 xaml 里取某个 DataTemplate 的 (Data, Fill) 列表。"""
    head = 'x:Key="DataTemplate.Icon.%s"' % key
    i = text.find(head)
    if i < 0:
        raise SystemExit("xaml 里没有 %s" % key)
    j = text.find("</DataTemplate>", i)
    out = []
    for tag in re.findall(r"<Path\b[^>]*>", text[i:j]):
        d = re.search(r'\bData="([^"]+)"', tag)
        f = re.search(r'\bFill="([^"]+)"', tag)
        if d and f:
            out.append((d.group(1), f.group(1)))
    return out


def paths_from_svg(text):
    """简单 SVG → (d, fill) 列表。"""
    out = []
    for tag in re.findall(r"<path\b[^>]*>", text):
        d = re.search(r'\bd="([^"]+)"', tag)
        f = re.search(r'\bfill="([^"]+)"', tag)
        if d and f and f.group(1).lower() != "none":
            out.append((d.group(1), f.group(1)))
    return out
