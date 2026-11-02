"""
MK11 MKSwap package merger.

Merges several mods that each ship a full copy of the same package
(e.g. GEARASSETS_SCO_ScriptAssets.xxx/.psf) into one package that has
all of their changes, by 3-way merging every mod against the original
game files.

Usage:
    python mk11_merge.py --out OUT_DIR MOD_DIR_1 MOD_DIR_2 [MOD_DIR_3 ...]

Each MOD_DIR contains <Package>.xxx (and usually <Package>.psf).
The original package is read from <game>\\Asset (see --game).
"""
import argparse, ctypes, mmap, os, re, shutil, struct, time


def detect_game():
    """Find the Steam install of Mortal Kombat 11, or return ''."""
    libs = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as k:
            steam = winreg.QueryValueEx(k, 'SteamPath')[0]
        libs.append(steam)
        vdf = os.path.join(steam, 'steamapps', 'libraryfolders.vdf')
        with open(vdf, encoding='utf-8', errors='ignore') as f:
            libs += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
    except OSError:
        pass
    for lib in libs:
        game = os.path.normpath(os.path.join(lib, 'steamapps', 'common', 'Mortal Kombat 11'))
        if os.path.isdir(os.path.join(game, 'Asset')):
            return game
    return ''


DEFAULT_GAME = detect_game()
MAGIC = 0x9E2A83C1
SIG = struct.pack('<IIQ', MAGIC, 0, 0x20000)


# ---------------------------------------------------------------- oodle

class Oodle:
    def __init__(self, game):
        self.dll = ctypes.WinDLL(os.path.join(game, 'Binaries', 'Retail', 'oo2core_5_win64.dll'))
        d = self.dll.OodleLZ_Decompress
        d.restype = ctypes.c_ssize_t
        d.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
        c = self.dll.OodleLZ_Compress
        c.restype = ctypes.c_ssize_t
        c.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int,
                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]

    def decompress(self, src, size):
        out = ctypes.create_string_buffer(size)
        n = self.dll.OodleLZ_Decompress(src, len(src), out, size, 1, 0, 0, None, 0, None, None, None, 0, 3)
        if n != size:
            raise ValueError('Oodle decompress failed')
        return out.raw

    def compress(self, data):
        out = ctypes.create_string_buffer(len(data) + 0x10000)
        n = self.dll.OodleLZ_Compress(8, data, len(data), out, 4, None, None, None, None, 0)  # Kraken, normal
        if n <= 0:
            raise ValueError('Oodle compress failed')
        return out.raw[:n]

    def chunk_decompress(self, buf, off):
        tag, _, bs, _, usum = struct.unpack_from('<IIQQQ', buf, off)
        if tag != MAGIC:
            raise ValueError(f'bad chunk at {off:#x}')
        nb = (usum + bs - 1) // bs
        p = off + 32 + nb * 16
        out = []
        for i in range(nb):
            c, u = struct.unpack_from('<QQ', buf, off + 32 + i * 16)
            out.append(self.decompress(bytes(buf[p:p + c]), u))
            p += c
        return b''.join(out)

    def chunk_compress(self, data, bs=0x20000):
        parts = [self.compress(data[i:i + bs]) for i in range(0, len(data), bs)]
        hdr = struct.pack('<IIQQQ', MAGIC, 0, bs, sum(map(len, parts)), len(data))
        hdr += b''.join(struct.pack('<QQ', len(c), min(bs, len(data) - i * bs)) for i, c in enumerate(parts))
        return hdr + b''.join(parts)


# ---------------------------------------------------------------- package

class Package:
    """An .xxx file: raw header, section table, Oodle-compressed sections."""

    def __init__(self, path, oodle):
        self.path = path
        self.oo = oodle
        self.data = open(path, 'rb').read()
        d = self.data
        if struct.unpack_from('<I', d, 0)[0] != MAGIC or d[0x14:0x18] != b'MK11':
            raise ValueError(f'{path} is not an MK11 package')
        nsec = struct.unpack_from('<I', d, 0x68)[0]
        pos = 0x6C
        self.sections = []  # (name, field_pos, doff, dsz, [block_pos...], [blocks])
        for _ in range(nsec):
            ln = struct.unpack_from('<I', d, pos)[0]
            name = d[pos + 4:pos + 3 + ln].decode()
            pos += 4 + ln
            doff, dsz, coff, csz, nb = struct.unpack_from('<QQQQI', d, pos)
            fpos = pos
            pos += 36
            bpos = [pos + i * 32 for i in range(nb)]
            blocks = [struct.unpack_from('<QQQQ', d, p) for p in bpos]
            pos += nb * 32
            self.sections.append((name, fpos, doff, dsz, bpos, blocks))
        self.sec_end = pos
        self.hdr_end = self.sections[0][5][0][2]  # first compressed block
        self._cache = {}

    def layout(self):
        return [(s[0], s[2], s[3], [(b[0], b[1]) for b in s[5]]) for s in self.sections]

    def block(self, si, bi):
        key = (si, bi)
        if key not in self._cache:
            b = self.sections[si][5][bi]
            self._cache[key] = self.oo.chunk_decompress(self.data, b[2])
        return self._cache[key]

    def block_raw(self, si, bi):
        b = self.sections[si][5][bi]
        return self.data[b[2]:b[2] + b[3]]

    def section(self, si):
        return b''.join(self.block(si, bi) for bi in range(len(self.sections[si][5])))


# ---------------------------------------------------------------- diff helpers

def diff_ranges(x, y, lo, hi):
    """Exact [s, e) byte ranges in lo..hi where x and y differ."""
    out = []

    def rec(a, b, step):
        i = a
        while i < b:
            j = min(i + step, b)
            if x[i:j] != y[i:j]:
                if step == 1:
                    if out and out[-1][1] == i:
                        out[-1] = (out[-1][0], j)
                    else:
                        out.append((i, j))
                else:
                    rec(i, j, max(1, step // 16))
            i = j

    rec(lo, hi, 1 << 20)
    return out


class Conflict(Exception):
    pass


def merge3(orig, result, mod, lo, hi, what):
    """Apply mod's changes (vs orig) onto result in place."""
    n = 0
    for s, e in diff_ranges(orig, mod, lo, hi):
        cur = result[s:e]
        if cur != orig[s:e] and cur != mod[s:e]:
            raise Conflict(f'{what}: two mods change the same bytes at {s:#x}-{e:#x}')
        result[s:e] = mod[s:e]
        n += e - s
    return n


def chunk_starts(buf, lo, hi):
    """Offsets of compressed chunks found in buf[lo:hi]."""
    starts = []
    pos = buf.find(SIG, lo, hi)
    while pos != -1:
        _, _, bs, csum, usum = struct.unpack_from('<IIQQQ', buf, pos)
        nb = (usum + bs - 1) // bs
        if 0 < usum < (1 << 34) and nb < 1 << 16:
            starts.append(pos)
            pos = buf.find(SIG, pos + 32 + nb * 16 + csum, hi)
        else:
            pos = buf.find(SIG, pos + 1, hi)
    return starts


def relocate(buf, ranges, targets, delta):
    """Shift every u64 that equals a moved psf chunk offset and touches a changed range."""
    if not delta or not targets:
        return 0
    hits = set()
    for s, e in ranges:
        for p in range(max(s - 7, 0), min(e, len(buf) - 7)):
            v = struct.unpack_from('<Q', buf, p)[0]
            if v in targets and p not in hits:
                hits.add(p)
    for p in hits:
        v = struct.unpack_from('<Q', buf, p)[0]
        struct.pack_into('<Q', buf, p, v + delta)
    return len(hits)


# ---------------------------------------------------------------- merge

class MergeError(Exception):
    pass


def find_package(path):
    """Return (folder, package name) for a mod folder, also looking one folder deeper."""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    if not os.path.isdir(path):
        raise MergeError(f'Folder not found: {path}')
    xs = [f for f in os.listdir(path) if f.lower().endswith('.xxx')]
    if not xs:
        subs = [os.path.join(path, d) for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        found = [(s, f) for s in subs for f in os.listdir(s) if f.lower().endswith('.xxx')]
        if len(found) == 1:
            return found[0][0], os.path.splitext(found[0][1])[0]
    if len(xs) != 1:
        raise MergeError(f'{path}: expected exactly one .xxx file, found {xs or "none"}')
    return path, os.path.splitext(xs[0])[0]


def merge(mod_paths, out_dir, game=DEFAULT_GAME, log=print):
    """Merge the mods into out_dir. Returns the package name."""
    if len(mod_paths) < 2:
        raise MergeError('Pick at least two mod folders.')
    found = [find_package(m) for m in mod_paths]
    mod_dirs = [f[0] for f in found]
    names = {f[1] for f in found}
    if len(names) != 1:
        raise MergeError('These mods replace different packages (' + ', '.join(sorted(names)) +
                         '). They do not clash, so just put them all in MKSwap\\Asset.')
    name = names.pop()
    if not game or not os.path.exists(os.path.join(game, 'Binaries', 'Retail', 'oo2core_5_win64.dll')):
        raise MergeError('Set the game folder to your Mortal Kombat 11 install '
                         '(the folder that contains Asset, Binaries and MKSwap).')
    orig_dir = os.path.join(game, 'Asset')
    if not os.path.exists(os.path.join(orig_dir, name + '.xxx')):
        raise MergeError(f'Original {name}.xxx not found in {orig_dir}. Check the game folder.')
    out_dir = os.path.abspath(out_dir)
    for m in mod_dirs:
        if os.path.normcase(m) == os.path.normcase(out_dir):
            raise MergeError('The output folder must not be one of the mod folders.')
    if os.path.normcase(out_dir) == os.path.normcase(os.path.abspath(orig_dir)):
        raise MergeError("The output folder must not be the game's Asset folder.")
    os.makedirs(out_dir, exist_ok=True)
    oo = Oodle(game)

    log(f'Package: {name}')
    o = Package(os.path.join(orig_dir, name + '.xxx'), oo)
    mods = [Package(os.path.join(m, name + '.xxx'), oo) for m in mod_dirs]
    for m in mods:
        if m.layout() != o.layout() or m.hdr_end != o.hdr_end:
            raise MergeError(f'{m.path}: section layout differs from the original game file; '
                             'this tool only handles mods that keep the original layout.')

    # --- psf
    o_psf_path = os.path.join(orig_dir, name + '.psf')
    out_psf_path = os.path.join(out_dir, name + '.psf')
    log('Copying original .psf (this takes a moment) ...')
    shutil.copyfile(o_psf_path, out_psf_path)
    fo = open(o_psf_path, 'rb')
    opsf = mmap.mmap(fo.fileno(), 0, access=mmap.ACCESS_READ)
    osize = len(opsf)
    fout = open(out_psf_path, 'r+b')
    rpsf = mmap.mmap(fout.fileno(), 0)

    raw = bytearray(o.data[:o.hdr_end])
    secs = [bytearray(o.section(i)) for i in range(len(o.sections))]

    try:
        for m, mdir in zip(mods, mod_dirs):
            log(f'\nMod: {mdir}')
            mpsf_path = os.path.join(mdir, name + '.psf')
            delta, targets = 0, set()
            if os.path.exists(mpsf_path):
                fm = open(mpsf_path, 'rb')
                mpsf = mmap.mmap(fm.fileno(), 0, access=mmap.ACCESS_READ)
                n = merge3(opsf, rpsf, mpsf, 0, min(osize, len(mpsf)), '.psf')
                log(f'  .psf: {n:,} bytes changed in place')
                if len(mpsf) > osize:
                    tail_at = len(rpsf)
                    delta = tail_at - osize
                    targets = set(chunk_starts(mpsf, osize, len(mpsf)))
                    rpsf.close()
                    fout.seek(tail_at)
                    fout.write(mpsf[osize:])
                    fout.flush()
                    rpsf = mmap.mmap(fout.fileno(), 0)
                    log(f'  .psf: {len(mpsf) - osize:,} bytes appended, {len(targets)} chunks moved by {delta:#x}')
                mpsf.close()
                fm.close()

            # header (skip the section table, rebuilt below)
            mraw = bytearray(m.data[:m.hdr_end])
            ranges = diff_ranges(o.data, mraw, m.sec_end, m.hdr_end)
            relocs = relocate(mraw, ranges, targets, delta)
            n = merge3(o.data, raw, mraw, m.sec_end, m.hdr_end, 'header')
            log(f'  header: {n} bytes changed')

            for si, sec in enumerate(o.sections):
                od = o.section(si)
                md = bytearray(m.section(si))
                if md == od:
                    continue
                ranges = diff_ranges(od, md, 0, len(od))
                relocs += relocate(md, ranges, targets, delta)
                n = merge3(od, secs[si], md, 0, len(od), sec[0])
                log(f'  {sec[0]}: {n} bytes changed')
            if delta:
                log(f'  relocated {relocs} psf offset references')
                if relocs == 0:
                    raise MergeError('Appended .psf data is never referenced; refusing to guess.')
    except Conflict as e:
        raise MergeError(f'CONFLICT: {e}\nThese mods edit the same asset and cannot be merged automatically.')
    finally:
        rpsf.flush()
        rpsf.close()
        fout.close()
        opsf.close()
        fo.close()

    # --- rebuild .xxx: reuse an existing compressed block when the data matches
    log('\nBuilding .xxx ...')
    out = bytearray(raw)
    out[:o.sec_end] = o.data[:o.sec_end]
    payload = bytearray()
    cursor = o.hdr_end
    recompressed = 0
    for si, (sname, fpos, doff, dsz, bpos, blocks) in enumerate(o.sections):
        sec_coff = cursor
        for bi, b in enumerate(blocks):
            want = bytes(secs[si][b[0] - doff:b[0] - doff + b[1]])
            chunk = None
            for p in [o] + mods:
                if p.block(si, bi) == want:
                    chunk = p.block_raw(si, bi)
                    break
            if chunk is None:
                chunk = oo.chunk_compress(want)
                recompressed += 1
            struct.pack_into('<QQQQ', out, bpos[bi], b[0], b[1], cursor, len(chunk))
            payload += chunk
            cursor += len(chunk)
        struct.pack_into('<QQQQ', out, fpos, doff, dsz, sec_coff, cursor - sec_coff)
    out += payload
    out_xxx = os.path.join(out_dir, name + '.xxx')
    with open(out_xxx, 'wb') as f:
        f.write(out)
    log(f'  {recompressed} block(s) recompressed')

    # --- verify
    log('Verifying ...')
    chk = Package(out_xxx, oo)
    for si in range(len(o.sections)):
        if chk.section(si) != bytes(secs[si]):
            raise MergeError(f'Verify failed in section {o.sections[si][0]}')
    if chk.data[chk.sec_end:chk.hdr_end] != bytes(raw[o.sec_end:o.hdr_end]):
        raise MergeError('Verify failed in header')
    log(f'\nMerged package written to:\n  {out_xxx}\n  {out_psf_path}')
    return name


def install(out_dir, name, game=DEFAULT_GAME, log=print):
    """Back up the package currently in MKSwap\\Asset, then copy the merged one in."""
    swap = os.path.join(game, 'MKSwap', 'Asset')
    os.makedirs(swap, exist_ok=True)
    current = [os.path.join(swap, name + e) for e in ('.xxx', '.psf') if os.path.exists(os.path.join(swap, name + e))]
    if current:
        backup = os.path.join(game, 'MKSwap_backup_' + time.strftime('%Y%m%d_%H%M%S'))
        os.makedirs(backup)
        for f in current:
            shutil.move(f, backup)
        log(f'Backed up the old files to:\n  {backup}')
    for e in ('.xxx', '.psf'):
        log(f'Installing {name}{e} ...')
        shutil.copyfile(os.path.join(out_dir, name + e), os.path.join(swap, name + e))
    log(f'Installed into:\n  {swap}')


def main():
    ap = argparse.ArgumentParser(description='Merge MK11 MKSwap mods that replace the same package.')
    ap.add_argument('mods', nargs='+', help='mod folders (each with <Package>.xxx/.psf)')
    ap.add_argument('--out', required=True, help='output folder for the merged package')
    ap.add_argument('--game', default=DEFAULT_GAME, help='Mortal Kombat 11 install folder')
    ap.add_argument('--install', action='store_true', help='also install into MKSwap\\Asset (backs up current files)')
    args = ap.parse_args()
    try:
        name = merge(args.mods, args.out, args.game)
        if args.install:
            install(os.path.abspath(args.out), name, args.game)
    except MergeError as e:
        raise SystemExit(str(e))


if __name__ == '__main__':
    main()

