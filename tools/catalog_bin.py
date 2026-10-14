"""Reader and append-only editor for the binary Addressables catalog (catalog.bin, Addressables 2.x, version 2).

Layout (little endian, all references are absolute byte offsets, 0xFFFFFFFF = null):
  header      magic 0x0DE38942, version, keysOffset, idOffset, instanceProviderOffset,
              sceneProviderOffset, initObjectsArrayOffset, buildResultHashOffset
  arrays      uint32 byte size stored just before the offset, then the items
  strings     plain: size prefix + ASCII (flag 0x80000000 = UTF-16);
              dynamic (flag 0x40000000): linked list of (partOffset, nextOffset), parts stored last-to-first
  object      (typeOffset, valueOffset) - System.String value = (stringOffset, separator char as uint32)
  type        (assemblyNameOffset, classNameOffset)
  location    primaryKey, internalId, providerId, dependencies[], dependencyHashCode, data, type  (7 x uint32)
  keys        array of (keyObjectOffset, locationArrayOffset) pairs

New data is appended at the end of the file and the header's keysOffset is pointed at an extended
copy of the keys array, so every existing offset stays valid.
"""
import struct

NONE = 0xFFFFFFFF
MAGIC = 0x0DE38942


class Catalog:
    def __init__(self, data: bytes):
        self.d = bytearray(data)
        magic, version = struct.unpack_from("<iI", self.d, 0)
        if magic != MAGIC or version != 2:
            raise ValueError(f"Nieobsługiwany catalog.bin (magic {magic:#x}, wersja {version})")

    # ---- reading ----
    def u32(self, o: int) -> int:
        return struct.unpack_from("<I", self.d, o)[0]

    @property
    def keys_offset(self) -> int:
        return self.u32(8)

    def array(self, o: int) -> list[int]:
        if o == NONE:
            return []
        n = self.u32(o - 4) // 4
        return list(struct.unpack_from(f"<{n}I", self.d, o))

    def string(self, o: int, sep: str = "/") -> str | None:
        if o == NONE:
            return None
        unicode, dynamic, off = bool(o & 0x80000000), bool(o & 0x40000000), o & 0x3FFFFFFF
        if not dynamic:
            raw = bytes(self.d[off:off + self.u32(off - 4)])
            return raw.decode("utf-16-le" if unicode else "ascii")
        parts = []
        while off != NONE:
            part, off = struct.unpack_from("<II", self.d, off)
            parts.append(self.string(part, sep))
        return sep.join(reversed(parts))

    def key(self, o: int):
        type_off, value_off = struct.unpack_from("<II", self.d, o)
        cls = self.string(self.u32(type_off + 4), ".")
        if value_off == NONE:
            return None
        if cls == "System.String":
            s, sep = struct.unpack_from("<II", self.d, value_off)
            return self.string(s, chr(sep))
        if cls == "System.Int32":
            return struct.unpack_from("<i", self.d, value_off)[0]
        return (cls, value_off)

    def location(self, o: int) -> dict:
        pk, iid, prov, deps, dep_hash, data, typ = struct.unpack_from("<IIIIiII", self.d, o)
        return {"offset": o, "primary_key": self.string(pk), "internal_id": self.string(iid),
                "provider": self.string(prov, "."), "dependencies": self.array(deps),
                "raw": (pk, iid, prov, deps, dep_hash, data, typ)}

    def resources(self):
        """Yields (key, key object offset, location array offset)."""
        pairs = self.array(self.keys_offset)
        for i in range(0, len(pairs), 2):
            yield self.key(pairs[i]), pairs[i], pairs[i + 1]

    def find(self, key) -> tuple[int, list[int]]:
        for k, key_off, locs_off in self.resources():
            if k == key:
                return key_off, self.array(locs_off)
        raise KeyError(key)

    # ---- appending ----
    def _append(self, payload: bytes, sized: bool) -> int:
        self.d += b"\0" * (-len(self.d) % 4)
        if sized:
            self.d += struct.pack("<I", len(payload))
        off = len(self.d)
        self.d += payload
        return off

    def add_string(self, s: str) -> int:
        return self._append(s.encode("ascii"), sized=True)

    def add_location_like(self, template_offset: int, primary_key: str, internal_id: str) -> int:
        """Copies a location record (provider, dependencies, data, type) with a new key and internal id."""
        _, _, prov, deps, dep_hash, data, typ = struct.unpack_from("<IIIIiII", self.d, template_offset)
        rec = struct.pack("<IIIIiII", self.add_string(primary_key), self.add_string(internal_id),
                          prov, deps, dep_hash, data, typ)
        return self._append(rec, sized=False)

    def add_string_key(self, template_key_offset: int, value: str, sep: str = "/") -> int:
        """A new System.String key object, reusing the type record of an existing string key."""
        type_off = self.u32(template_key_offset)
        value_off = self._append(struct.pack("<II", self.add_string(value), ord(sep)), sized=False)
        return self._append(struct.pack("<II", type_off, value_off), sized=False)

    def add_keys(self, new_pairs: list[tuple[int, list[int]]]) -> None:
        """new_pairs: (key object offset, [location offsets]). Rewrites the keys array at the end."""
        pairs = self.array(self.keys_offset)
        for key_off, locs in new_pairs:
            pairs += [key_off, self._append(struct.pack(f"<{len(locs)}I", *locs), sized=True)]
        new_keys = self._append(struct.pack(f"<{len(pairs)}I", *pairs), sized=True)
        struct.pack_into("<I", self.d, 8, new_keys)
