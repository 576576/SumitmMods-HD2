"""读 filediver 导出的 .glb，按指定动画时刻求蒙皮后的三角形。

unit 的 gpu 导出只有绑定姿态，要看模型自己摆出的形态（比如补给舱的 deploy），
必须走 skinned mesh 这条路：JOINTS_0/WEIGHTS_0 加权骨骼矩阵，再乘逆绑定矩阵。

用法：
    gltf, bin_ = glbskin.load(path)
    parts = glbskin.skinned(gltf, bin_, anim=2, t=0.999)   # [(顶点, 三角形, 材质号)]
"""

import json
import struct

import numpy as np

COMPONENT_TYPE = {5120: np.int8, 5121: np.uint8, 5122: np.int16,
                  5123: np.uint16, 5125: np.uint32, 5126: np.float32}
TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def load(path):
    """→ (gltf 字典, bin 字节)。GLB 头 12 字节，之后是长度+类型交替的 chunk。"""
    data = open(path, "rb").read()
    off, length = 12, struct.unpack_from("<III", data, 0)[2]
    gltf, bin_ = None, None
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8:off + 8 + clen]
        if ctype == 0x4E4F534A:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            bin_ = chunk
        off += 8 + clen
    return gltf, bin_


def accessor(gltf, bin_, index):
    """读一个 accessor。bufferView 带 byteStride 时是交错布局，
    要按整行跨步取前 itemsize 字节，按紧凑数组读会把相邻属性当成本条数据。
    """
    acc = gltf["accessors"][index]
    bv = gltf["bufferViews"][acc["bufferView"]]
    dt = np.dtype(COMPONENT_TYPE[acc["componentType"]])
    n = TYPE_COUNT[acc["type"]]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    stride = bv.get("byteStride")
    itemsize = dt.itemsize * n
    if stride and stride != itemsize:
        rows = np.frombuffer(bin_, np.uint8, count=count * stride,
                             offset=start).reshape(count, stride)[:, :itemsize]
        arr = np.ascontiguousarray(rows).view(dt).reshape(count, n)
    else:
        arr = np.frombuffer(bin_, dtype=dt, count=count * n, offset=start).reshape(count, n)
    return arr.astype(np.float32) if dt == np.float32 else arr


def parents(gltf):
    """→ {子节点: 父节点}。"""
    out = {}
    for i, node in enumerate(gltf["nodes"]):
        for child in node.get("children", []):
            out[child] = i
    return out


def channels(gltf, anim):
    """→ {节点: {"translation": (时间访问器, 值访问器), ...}}"""
    a = gltf["animations"][anim]
    out = {}
    for ch in a["channels"]:
        s = a["samplers"][ch["sampler"]]
        out.setdefault(ch["target"]["node"], {})[ch["target"]["path"]] = (s["input"], s["output"])
    return out


def _at(gltf, bin_, tin, tout, t):
    """在关键帧序列上取 t 时刻的值，两端夹住、段内线性插值。"""
    ts = accessor(gltf, bin_, tin).ravel().astype(np.float64)
    vs = accessor(gltf, bin_, tout).astype(np.float64)
    if t <= ts[0]:
        return vs[0]
    if t >= ts[-1]:
        return vs[-1]
    i = max(0, min(int(np.searchsorted(ts, t)) - 1, len(ts) - 2))
    f = (t - ts[i]) / (ts[i + 1] - ts[i])
    return vs[i] * (1 - f) + vs[i + 1] * f


def _matrix(t, r, s):
    """(平移, 四元数 xyzw, 缩放) → 4×4 局部矩阵。"""
    x, y, z, w = r / (np.linalg.norm(r) or 1.0)
    m = np.eye(4)
    m[:3, :3] = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                          [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                          [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]) @ np.diag(s)
    m[:3, 3] = t
    return m


class Pose:
    """某个动画时刻的全部节点世界矩阵，按需计算并缓存。"""

    def __init__(self, gltf, bin_, anim=None, t=0.0):
        self.g, self.bin, self.t = gltf, bin_, t
        self.par = parents(gltf)
        self.ch = channels(gltf, anim) if anim is not None else {}
        self._local, self._world = {}, {}

    def local(self, i):
        if i in self._local:
            return self._local[i]
        node = self.g["nodes"][i]
        if "matrix" in node:
            m = np.array(node["matrix"], float).reshape(4, 4).T
        else:
            t = np.array(node.get("translation", [0, 0, 0]), float)
            r = np.array(node.get("rotation", [0, 0, 0, 1]), float)
            s = np.array(node.get("scale", [1, 1, 1]), float)
            ch = self.ch.get(i, {})
            if "translation" in ch:
                t = _at(self.g, self.bin, *ch["translation"], self.t)
            if "rotation" in ch:
                r = _at(self.g, self.bin, *ch["rotation"], self.t)
            if "scale" in ch:
                s = _at(self.g, self.bin, *ch["scale"], self.t)
            m = _matrix(t, r, s)
        self._local[i] = m
        return m

    def world(self, i):
        if i in self._world:
            return self._world[i]
        parent = self.par.get(i)
        m = self.local(i) if parent is None else self.world(parent) @ self.local(i)
        self._world[i] = m
        return m

    def bone(self, name):
        """按名字取骨骼的世界矩阵；skin 的 joints 里没有这个名字就报错。"""
        joints = self.g["skins"][0]["joints"]
        for j in joints:
            if self.g["nodes"][j].get("name") == name:
                return self.world(j)
        raise SystemExit("骨骼不存在: %s" % name)


def duration(gltf, bin_, anim):
    return max(float(accessor(gltf, bin_, s["input"]).ravel().max())
               for s in gltf["animations"][anim]["samplers"])


def skinned(gltf, bin_, anim=None, t=0.0):
    """→ [(顶点 N×3, 三角形 M×3, 材质号)]，顶点已蒙皮到 anim 的 t 时刻。"""
    pose = Pose(gltf, bin_, anim, t)
    skin = gltf["skins"][0]
    ibm = accessor(gltf, bin_, skin["inverseBindMatrices"]).astype(np.float64)
    ibm = ibm.reshape(-1, 4, 4).transpose(0, 2, 1)
    joint_mat = np.stack([pose.world(j) @ ibm[k] for k, j in enumerate(skin["joints"])])
    out = []
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            a = prim["attributes"]
            pos = accessor(gltf, bin_, a["POSITION"]).astype(np.float64)
            joints = accessor(gltf, bin_, a["JOINTS_0"]).astype(np.int64)
            weights = accessor(gltf, bin_, a["WEIGHTS_0"]).astype(np.float64)
            idx = accessor(gltf, bin_, prim["indices"]).astype(np.int64).ravel()
            homo = np.hstack([pos, np.ones((len(pos), 1))])
            v = np.zeros((len(pos), 4))
            for k in range(joints.shape[1]):
                v += weights[:, k:k + 1] * np.einsum("nij,nj->ni", joint_mat[joints[:, k]], homo)
            out.append((v[:, :3], idx.reshape(-1, 3), prim.get("material")))
    return out
