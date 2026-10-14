"""Variant B: adds NEW languages to Zero Parades, next to English, German, Russian, Spanish and Chinese
(no existing slot is replaced):
    "Polski"       code pl = 80   texts from --texts       (default: the original German table)
    "Polski + EN"  code fo = 45   --bilingual: built here from --texts plus the English originals read
                                  from the player's own game files (tools/en_from_game.py), so the
                                  distributed translation never contains any game text;
                                  --texts-en <json>: a ready-made table instead (workshop use)
The FELD enum ZAUM.FELD.Data.Localization.LanguageIsoCode has a single "pl", so the bilingual variant
borrows "fo" (Faroese): a Latin-script, left-to-right language with a valid .NET culture, no special
fonts in the game's font styles, and no chance of ever becoming an official language of this game.

    python tools/add_language.py --game "G:/SteamLibrary/steamapps/common/Zero Parades" \
        --texts translation/pl.json --bilingual --out <dir>
        builds the patched files into <dir> only (the game is not touched)
    python tools/add_language.py --game "..." --texts ... --bilingual --apply
        writes them into the game
    --texts defaults to translation/pl.json, or out/pl_merged.json in the workshop checkout.
    python tools/install.py --game "..." --restore      # undo (or Steam -> Verify integrity of game files)

Always starts from the pristine copies in backup/, so the DE/ES slots stay German/Spanish.

What is changed:
 1. localization bundle: a table asset .../Localization/<dir>/<dir>LocalizationTable.asset per language,
    language name keys in every table and in the English base strings (DialogueDatabase "menu");
 2. settings bundle: "Text Localization Settings" gets {code -> table GUID},
    the language dropdown gets {code -> menu/<name entry>};
 3. LiberationSans SDF fonts -> dynamic atlas (as in install.py, for Polish letters);
 4. catalog.bin: CRC of modified bundles zeroed, new locations + keys (GUID and address) appended.
"""
import argparse
import copy
import hashlib
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import UnityPy
from UnityPy.helpers import TypeTreeHelper

import en_from_game
import install
from catalog_bin import Catalog

TEMPLATE_ASSET = f"{install.LOC_DIR}/de/deLocalizationTable.asset"
DATABASE_ASSET = "Assets/FELDRuntime/Scriptables/Main/DialogueDatabase.asset"
SETTINGS_BUNDLE = "7c1b62aa1547cf298fd1304c258061af.bundle"
LANGUAGE_NAME_PREFIX = "menu/settings_option_language_"


@dataclass
class Language:
    code: int            # ZAUM.FELD.Data.Localization.LanguageIsoCode
    dir: str             # folder / asset / address name
    name: str            # shown in the language dropdown
    name_entry: str      # key in the "menu" table
    texts: dict | None = None
    base: "Language | None" = None   # bilingual variant: texts = base.texts + English read from the game

    @property
    def asset(self) -> str:
        return f"{install.LOC_DIR}/{self.dir}/{self.dir}LocalizationTable.asset"

    @property
    def guid(self) -> str:
        return hashlib.md5(f"ZeroParadesPL/{self.dir}LocalizationTable".encode()).hexdigest()

    @property
    def name_key(self) -> str:
        return f"menu/{self.name_entry}"


POLISH = Language(80, "pl", "Polski", "settings_option_language_polish")
POLISH_EN = Language(45, "pl_en", "Polski + EN", "settings_option_language_polish_english")

# UnityPy 1.25 skips the data of a null managed reference (type class "") when reading,
# but still expects it when writing - DialogueDatabase has one (rid -2). Mirror the reader.
_write_value = TypeTreeHelper.write_value


def _write_value_null_refs(value, node, writer, config):
    if node.m_Type == "ReferencedObject":
        for child in node.m_Children:
            if child.m_Type == "ReferencedObjectData":
                ref_node = TypeTreeHelper.get_ref_type_node(value, config.assetsfile)
                if ref_node is not None:
                    TypeTreeHelper.write_value(value[child.m_Name], ref_node, writer, config)
            else:
                TypeTreeHelper.write_value(value[child.m_Name], child, writer, config)
        if TypeTreeHelper.metaflag_is_aligned(node.m_MetaFlag):
            writer.align_stream()
        return
    _write_value(value, node, writer, config)


TypeTreeHelper.write_value = _write_value_null_refs


def new_path_id(lang: Language, existing) -> int:
    seed = 0
    while True:
        h = hashlib.md5(f"ZeroParadesPL/{lang.dir}/{seed}".encode()).digest()
        pid = struct.unpack("<q", h[:8])[0]
        if pid not in existing:
            return pid
        seed += 1


def add_entry(table: dict, key: str, text: str) -> None:
    keys, values = table["m_data"]["m_keys"], table["m_data"]["m_values"]
    if key in keys:
        values[keys.index(key)] = text
    else:
        keys.append(key)
        values.append(text)


def patch_loc_bundle(src: Path, langs: list[Language]) -> bytes:
    env = UnityPy.load(str(src))
    sf = next(iter(env.file.files.values()))
    asset = lambda path: sf.objects[env.container[path].m_PathID]
    template = asset(TEMPLATE_ASSET)
    ab_obj = next(o for o in sf.objects.values() if o.type.name == "AssetBundle")
    ab = ab_obj.read_typetree()
    info = next(e[1] for e in ab["m_Container"] if e[0] == TEMPLATE_ASSET)
    template_preload = ab["m_PreloadTable"][info["preloadIndex"]:info["preloadIndex"] + info["preloadSize"]]
    old_tables = [p for p in env.container if p.endswith("LocalizationTable.asset")]

    # 0. bilingual variant: Polish texts + the English originals read from this very bundle
    for lang in langs:
        if lang.base is not None and lang.texts is None:
            en = en_from_game.extract(env)
            lang.texts = {k: en_from_game.with_original(v, en.get(k)) for k, v in lang.base.texts.items()}
            print(f"  {lang.name}: angielski oryginał z plików gry – {len(en)} tekstów, "
                  f"dopisany do {sum(1 for k in lang.texts if k in en)} linii")

    for lang in langs:
        # 1a. the new table: a copy of the German one with our code and texts
        tree = template.read_typetree()
        tree["m_Name"] = f"{lang.dir}LocalizationTable"
        tree["m_languageCode"] = lang.code
        if lang.texts is not None:
            keys = tree["m_data"]["m_keys"]
            missing = [k for k in keys if k not in lang.texts]
            # language names stay native ("Deutsch", "Español (latino)"...) as in the template table,
            # even if the texts were built for the old slot setup (German slot renamed to "Polski")
            tree["m_data"]["m_values"] = [v if k.startswith(LANGUAGE_NAME_PREFIX) else lang.texts.get(k, v)
                                          for k, v in zip(keys, tree["m_data"]["m_values"])]
            if missing:
                print(f"  uwaga: {lang.name}: {len(missing)} kluczy bez tekstu – zostaje tekst niemiecki")
        for other in langs:
            add_entry(tree, other.name_key, other.name)
        pid = new_path_id(lang, sf.objects)
        table = copy.copy(template)
        table.path_id = pid
        table.save_typetree(tree)
        sf.objects[pid] = table

        # 1b. register the asset in the bundle (container + preload table), same pattern as the template
        preload = [{"m_FileID": 0, "m_PathID": pid} if p == {"m_FileID": 0, "m_PathID": template.path_id} else p
                   for p in template_preload]
        ab["m_Container"].append((lang.asset, {"preloadIndex": len(ab["m_PreloadTable"]),
                                               "preloadSize": len(preload),
                                               "asset": {"m_FileID": 0, "m_PathID": pid}}))
        ab["m_PreloadTable"] += preload
        print(f"  nowa tabela {lang.asset.rsplit('/', 1)[-1]} „{lang.name}” (kod {lang.code}, "
              f"{len(tree['m_data']['m_keys'])} kluczy)")

    ab["m_Container"].sort(key=lambda e: e[0])
    ab_obj.save_typetree(ab)

    # 1c. language names in every existing table ...
    for path in old_tables:
        obj = asset(path)
        t = obj.read_typetree()
        for lang in langs:
            add_entry(t, lang.name_key, lang.name)
        obj.save_typetree(t)

    # ... and in the English base strings (FELD entity "menu")
    db_obj = asset(DATABASE_ASSET)
    db = db_obj.read_typetree()
    menu_rid = db["m_entities"]["m_values"][db["m_entities"]["m_keys"].index("menu")]["rid"]
    menu = next(r for r in db["references"]["RefIds"] if r["rid"] == menu_rid)["data"]["m_entries"]
    for lang in langs:
        if lang.name_entry not in menu["m_keys"]:
            menu["m_keys"].append(lang.name_entry)
            menu["m_values"].append({"description": lang.name_entry.replace("_", " "), "text": lang.name})
    db_obj.save_typetree(db)

    return env.file.save(packer="lz4")


def patch_settings_bundle(src: Path, langs: list[Language]) -> bytes:
    env = UnityPy.load(str(src))
    codes = {lang.code for lang in langs}
    done = {"data": 0, "names": 0}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if tree.get("m_Name") == "Text Localization Settings" and "LocalizationData" in tree:
            data = [e for e in tree["LocalizationData"] if e["LanguageCode"] not in codes]
            data += [{"LanguageCode": lang.code, "DataReference": {
                "m_AssetGUID": lang.guid, "m_SubObjectName": "", "m_SubObjectType": ""}} for lang in langs]
            tree["LocalizationData"] = data
            obj.save_typetree(tree)
            done["data"] += 1
        elif "m_settingLocalizationKeys" in tree:
            keys = tree["m_settingLocalizationKeys"]
            names = [e for e in keys["LanguageLocalizationKeys"] if e["LanguageCode"] not in codes]
            names += [{"LanguageCode": lang.code, "LanguageLocalizationKeyReference": {
                "m_tableId": "menu", "m_entryKey": lang.name_entry}} for lang in langs]
            keys["LanguageLocalizationKeys"] = names
            obj.save_typetree(tree)
            done["names"] += 1
    if done["data"] != 1 or done["names"] < 1:
        sys.exit(f"Nie znaleziono ustawień języków w {src.name}: {done}")
    print(f"  ustawienia: lista języków + {done['names']}× lista nazw w menu")
    return env.file.save(packer="lz4")


def patch_catalog(catalog: Catalog, project: str, langs: list[Language]) -> None:
    """The German table is addressed both as "<project>/de" and by its GUID; each new table gets the same pair."""
    addr_key_off, locs = catalog.find(f"{project}/de")
    guid_key_off = next(key_off for k, key_off, locs_off in catalog.resources()
                        if isinstance(k, str) and len(k) == 32 and catalog.array(locs_off) == locs)
    new_keys = []
    for lang in langs:
        address = f"{project}/{lang.dir}"
        loc = catalog.add_location_like(locs[0], address, lang.asset)
        new_keys += [(catalog.add_string_key(guid_key_off, lang.guid), [loc]),
                     (catalog.add_string_key(addr_key_off, address), [loc])]
        print(f"  catalog.bin: nowa lokalizacja {address} (GUID {lang.guid})")
    catalog.add_keys(new_keys)


def build(game: Path, langs: list[Language]) -> dict[Path, bytes]:
    """Returns {destination in the game: new content}. Reads the game, writes only backup/."""
    aa, bundles, catalog_path = install.game_paths(game)
    bdir = install.backup_dir_for(catalog_path)
    catalog_orig = install.backup(catalog_path, bdir)
    loc = install.find_loc_bundle(bundles)
    loc_orig = install.backup(loc, bdir)
    settings = bundles / SETTINGS_BUNDLE
    if not settings.exists():
        sys.exit(f"Brak {SETTINGS_BUNDLE} – gra się zmieniła, skrypt wymaga aktualizacji")
    settings_orig = install.backup(settings, bdir)

    out: dict[Path, bytes] = {}
    catalog = Catalog(catalog_orig.read_bytes())

    out[loc] = patch_loc_bundle(loc_orig, langs)
    install.zero_crc(catalog.d, loc_orig.stat().st_size, loc.name)
    out[settings] = patch_settings_bundle(settings_orig, langs)
    install.zero_crc(catalog.d, settings_orig.stat().st_size, settings.name)

    for b in sorted(bundles.glob("*.bundle")):          # fonts, exactly as install.py
        if b in (loc, settings):
            continue
        orig = bdir / b.name
        src = orig if orig.exists() else b
        try:
            env = UnityPy.load(str(src))
            has_font = any(o.type.name == "Font" and o.peek_name() == install.FONT_SRC for o in env.objects)
        except Exception:
            continue
        if not has_font:
            continue
        orig = install.backup(b, bdir)
        tmp = Path(str(orig) + ".tmp")
        if install.patch_fonts(tmp, orig):
            out[b] = tmp.read_bytes()
            tmp.unlink()
            install.zero_crc(catalog.d, orig.stat().st_size, b.name)
            print(f"  czcionka: {install.FONT_SDF} → tryb dynamiczny w {b.name}")

    patch_catalog(catalog, loc.name.split("_")[0], langs)
    out[catalog_path] = bytes(catalog.d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, help="Folder gry (zawiera ZeroParades_Data)")
    default_texts = next((p for p in (install.ROOT / "translation/pl.json", install.ROOT / "out/pl_merged.json") if p.exists()), None)
    ap.add_argument("--texts", default=default_texts,
                    help=f"JSON klucz -> tekst dla „Polski” (domyślnie {default_texts or 'oryginał niemiecki'})")
    variant = ap.add_mutually_exclusive_group()
    variant.add_argument("--bilingual", action="store_true",
                         help="dodaj też „Polski + EN”: angielski oryginał czytany z plików gry")
    variant.add_argument("--texts-en", help="JSON klucz -> tekst dla „Polski + EN” (gotowa tabela, warsztat)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", help="Zapisz zmienione pliki do tego folderu (gra nietknięta)")
    mode.add_argument("--apply", action="store_true", help="Zapisz zmienione pliki w folderze gry")
    args = ap.parse_args()
    game = Path(args.game)
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None

    POLISH.texts = load(args.texts)
    langs = [POLISH]
    if args.texts_en:
        POLISH_EN.texts = load(args.texts_en)
        langs.append(POLISH_EN)
    elif args.bilingual:
        if POLISH.texts is None:
            sys.exit("--bilingual wymaga --texts (polskich tekstów)")
        POLISH_EN.base = POLISH
        langs.append(POLISH_EN)

    files = build(game, langs)
    if args.out:
        out = Path(args.out)
        for dst, data in files.items():
            rel = dst.relative_to(game)
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_bytes(data)
        print(f"Zapisano {len(files)} plików w {out} (gra nie została zmieniona).")
    else:
        for dst, data in files.items():
            dst.write_bytes(data)
        install.remember_patched(install.game_paths(game)[2])
        print("Gotowe. W grze: Ustawienia → Rozgrywka → Język → "
              + " / ".join(f"„{lang.name}”" for lang in langs) + ".")


if __name__ == "__main__":
    main()
