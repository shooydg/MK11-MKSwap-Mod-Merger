# MK11 MKSwap Mod Merger

Merge two (or more) Mortal Kombat 11 **MKSwap** mods that replace the **same package file**.

With ASIMK11's MKSwap, every mod is a full copy of a game package (for example
`GEARASSETS_SCO_ScriptAssets.xxx` + `.psf`). Two skin mods for the same character
therefore have the same file names, and only one of them can sit in `MKSwap\Asset`.
This tool combines them into a single package that contains the changes from all of them.

## Requirements

- Windows, Mortal Kombat 11 (Steam)
- [Python 3.9+](https://www.python.org/downloads/) (tick "Add python.exe to PATH" when installing)
- [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader) + [ASIMK11](https://github.com/thethiny/ASIMK11) to load the result
- The **original, unmodified** game files in `Mortal Kombat 11\Asset` (the tool compares each mod against them)

No extra Python packages are needed. Oodle compression is done with the
`oo2core_5_win64.dll` that ships with the game, so nothing proprietary is included here.

## How to use

1. Double-click **`MK11 Mod Merger.pyw`** (or `merge.bat`).
2. **Game folder**: auto-detected from Steam. If it is empty, browse to your `Mortal Kombat 11` folder.
3. **Mod 1 / Mod 2**: the folders containing each mod's `.xxx` and `.psf`.
   Picking the folder one level above works too. Use **+ Add another mod** for more than two.
4. **Output folder**: where the merged `.xxx` and `.psf` are written.
5. Leave **Install into MKSwap\Asset** ticked to copy the result into the game for you.
   Files already there are moved to `MKSwap_backup_<date>` in the game folder first.
6. Click **MERGE** and wait a minute or two (the `.psf` files are over 1 GB).

### Command line

```
python mk11_merge.py --out OUTPUT_FOLDER MOD_FOLDER_1 MOD_FOLDER_2 [MOD_FOLDER_3 ...] [--install] [--game "D:\...\Mortal Kombat 11"]
```

## How it works

- Each mod is diffed against the original package. Changes from every mod are applied to one copy.
- In the `.psf`, mods overwrite texture chunks in place and append bigger ones at the end of the file.
  All mods append at the same offset, so the tool moves each extra mod's appended data to the new end
  of the file and updates the offsets in the `.xxx` that point to it.
- The `.xxx` is rebuilt, reusing the original compressed blocks wherever possible, and verified after writing.

## Limitations

- If two mods change the **same asset** (e.g. both replace the same skin), the merge stops with a
  conflict message. It does not guess.
- Mods must keep the original package layout (true for mods made by swapping textures/meshes inside the package).
- Only tested with Steam MK11 packages.

## Restoring

Delete the merged files from `MKSwap\Asset` and move the files from the `MKSwap_backup_<date>` folder back.
