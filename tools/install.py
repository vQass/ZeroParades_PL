"""Installs the Polish translation into Zero Parades (replaces the German language slot).

    python tools/install.py --game "G:/SteamLibrary/steamapps/common/Zero Parades"
    python tools/install.py --game "..." --restore     # put original files back

Steps:
 1. Back up original files to <project>/backup/<game build id>/ (once per game build).
    Every install starts from these pristine copies, so re-running is always safe.
 2. Write out/pl_merged.json into the German LocalizationTable ("Polski") and
    out/pl_en_merged.json into the Spanish one ("Polski + EN"); language codes are unchanged.
 3. Switch every static "LiberationSans SDF" font asset with an embedded source font to
    dynamic atlas mode, so Polish letters missing from the main fonts get rendered.
 4. Zero the CRC of each modified bundle in the binary Addressables catalog (catalog.bin),
    otherwise Unity rejects the modified bundles.
"""
import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

import UnityPy

ROOT = Path(__file__).resolve().parent.parent
LOC_DIR = "Assets/FELDRuntime/Scriptables/Main/Localization"
# language slot -> translation file written by build_translation.py
SLOTS = {
    f"{LOC_DIR}/de/deLocalizationTable.asset": "out/pl_merged.json",           # "Polski"
    f"{LOC_DIR}/es_mx/es_mxLocalizationTable.asset": "out/pl_en_merged.json",  # "Polski + EN"
}
FONT_SDF = "LiberationSans SDF"
FONT_SRC = "LiberationSans"


def game_paths(game: Path):
    aa = game / "ZeroParades_Data/StreamingAssets/aa"
    return aa, aa / "StandaloneWindows64", aa / "catalog.bin"


MARKER = ROOT / "backup/current_build.json"


def sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def backup_dir_for(catalog: Path) -> Path:
    """Backups are keyed by the hash of the *original* catalog.bin of a game build.
    If the game's catalog is neither that original nor our last patched version,
    Steam has updated the game and a fresh backup set is started."""
    current = sha(catalog)
    if MARKER.exists():
        m = json.loads(MARKER.read_text())
        if current in (m["original_sha"], m.get("patched_sha")):
            return ROOT / "backup" / m["dir"]
    d = ROOT / "backup" / current[:12]
    d.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({"dir": d.name, "original_sha": current}))
    return d


def remember_patched(catalog: Path) -> None:
    m = json.loads(MARKER.read_text())
    m["patched_sha"] = sha(catalog)
    MARKER.write_text(json.dumps(m))


def backup(path: Path, bdir: Path) -> Path:
    dst = bdir / path.name
    if not dst.exists():
        shutil.copy2(path, dst)
        print(f"  kopia zapasowa: {path.name}")
    return dst


def find_loc_bundle(bundles: Path) -> Path:
    found = [p for p in bundles.glob("g5ibkj7vdwf2g67g_assets_all_*.bundle")]
    if len(found) != 1:
        sys.exit(f"Oczekiwano 1 bundla lokalizacji, znaleziono {len(found)}: {found}")
    return found[0]


def patch_localization(bundle: Path, src: Path, translations: dict[str, dict]) -> None:
    """Writes every slot's table into the bundle, then saves it once."""
    env = UnityPy.load(str(src))
    for asset, merged in translations.items():
        table = env.container[asset].read()
        keys = list(table.m_data.m_keys)
        missing = [k for k in keys if k not in merged]
        table.m_data.m_values = [merged.get(k, "") for k in keys]
        table.save()
        print(f"  {asset.rsplit('/', 1)[-1]}: {len(keys) - len(missing)}/{len(keys)} tekstów"
              + (f" ({len(missing)} kluczy bez tłumaczenia!)" if missing else ""))
    bundle.write_bytes(env.file.save(packer="lz4"))


def patch_fonts(bundle: Path, src: Path) -> bool:
    env = UnityPy.load(str(src))
    font_id, targets = None, []
    for obj in env.objects:
        try:
            name = obj.peek_name()
        except Exception:
            continue
        if obj.type.name == "Font" and name == FONT_SRC:
            font_id = obj.path_id
        elif obj.type.name == "MonoBehaviour" and name == FONT_SDF:
            targets.append(obj)
    if font_id is None or not targets:
        return False
    changed = False
    for obj in targets:
        tree = obj.read_typetree()
        if tree.get("m_AtlasPopulationMode") == 0:
            tree["m_AtlasPopulationMode"] = 1  # Dynamic
            tree["m_SourceFontFile"] = {"m_FileID": 0, "m_PathID": font_id}
            tree["m_IsMultiAtlasTexturesEnabled"] = 1
            obj.save_typetree(tree)
            changed = True
    if changed:
        bundle.write_bytes(env.file.save(packer="lz4"))
    return changed


def zero_crc(catalog: bytearray, original_size: int, name: str) -> None:
    """Bundle record in catalog.bin: [hash id][name id][CRC][size][common info id] (uint32 each)."""
    needle = struct.pack("<I", original_size)
    hits, start = [], 0
    while (i := catalog.find(needle, start)) != -1:
        hits.append(i)
        start = i + 1
    if len(hits) != 1:
        sys.exit(f"Nie udało się jednoznacznie znaleźć wpisu {name} w catalog.bin ({len(hits)} trafień)")
    pos = hits[0] - 4
    catalog[pos:pos + 4] = b"\0\0\0\0"
    print(f"  catalog.bin: wyzerowano CRC dla {name[:40]}…")


def restore(game: Path) -> None:
    aa, bundles, catalog = game_paths(game)
    if not MARKER.exists():
        sys.exit("Brak kopii zapasowych – nic do przywrócenia.")
    bdir = ROOT / "backup" / json.loads(MARKER.read_text())["dir"]
    for f in bdir.iterdir():
        dst = catalog if f.name == "catalog.bin" else bundles / f.name
        shutil.copy2(f, dst)
        print(f"  przywrócono {f.name}")


def install(game: Path) -> None:
    aa, bundles, catalog = game_paths(game)
    translations = {}
    for asset, rel in SLOTS.items():
        path = ROOT / rel
        if not path.exists():
            sys.exit(f"Brak {rel} – uruchom najpierw tools/build_translation.py")
        translations[asset] = json.loads(path.read_text(encoding="utf-8"))

    bdir = backup_dir_for(catalog)
    catalog_orig = backup(catalog, bdir)
    new_catalog = bytearray(catalog_orig.read_bytes())

    loc = find_loc_bundle(bundles)
    loc_orig = backup(loc, bdir)
    patch_localization(loc, loc_orig, translations)
    zero_crc(new_catalog, loc_orig.stat().st_size, loc.name)

    for b in sorted(bundles.glob("*.bundle")):
        if b == loc:
            continue
        orig = bdir / b.name
        src = orig if orig.exists() else b
        try:
            env = UnityPy.load(str(src))
            has_font = any(o.type.name == "Font" and o.peek_name() == FONT_SRC for o in env.objects)
        except Exception:
            continue
        if not has_font:
            continue
        orig = backup(b, bdir)
        if patch_fonts(b, orig):
            print(f"  czcionka: {FONT_SDF} → tryb dynamiczny w {b.name}")
            zero_crc(new_catalog, orig.stat().st_size, b.name)

    catalog.write_bytes(bytes(new_catalog))
    remember_patched(catalog)
    print("Gotowe. W grze: Ustawienia → Język → „Polski” (dawny Deutsch) albo „Polski + EN” (dawny Español).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, help="Folder gry (zawiera ZeroParades_Data)")
    ap.add_argument("--restore", action="store_true", help="Przywróć oryginalne pliki gry")
    args = ap.parse_args()
    game = Path(args.game)
    restore(game) if args.restore else install(game)


if __name__ == "__main__":
    main()
