"""LZ4 raw block 解压，替代 lz4 包，使构建只依赖 Python 标准库。"""


def decompress(src, uncompressed_size=None):
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out.extend(src[i:i + lit])
        i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0 or offset > len(out):
            raise ValueError("invalid LZ4 offset %d at byte %d" % (offset, i))
        match = token & 0x0F
        if match == 15:
            while True:
                b = src[i]
                i += 1
                match += b
                if b != 255:
                    break
        match += 4
        start = len(out) - offset
        for k in range(match):
            out.append(out[start + k])
    if uncompressed_size is not None:
        if len(out) < uncompressed_size:
            raise ValueError("LZ4 output %d bytes, expected %d" % (len(out), uncompressed_size))
        return bytes(out[:uncompressed_size])
    return bytes(out)
