#!/usr/bin/env python3
"""Assembles the `volume` fix's module (development-time helper; needs keystone, optionally capstone and unicorn).

The module replaces the stock mixer walk of set_volume method 1 (`0x004525E0..0x00452858` in the Council Wars exe,
632 bytes: the walk, the mixerSetControlDetails wrapper and the mixerClose wrapper - nothing but set_volume's sound
branch reaches them).  It is position-independent: every global, import slot and table is reached through a base
register loaded with `call next ; pop`, so the 58 HIGHLOW .reloc entries of the old code become type 0 and no new
entry is needed.  `python volume_asm.py` prints the bytes (pasted into patch_volume.py as MODULE_HEX), the listing,
and - when unicorn is installed - runs the two entry points against a fake sound table and fake COM objects.

Entry points (offsets inside the module, Watcom convention: every register but eax preserved):
  +0x000 sfx_set   ecx = level 0..10 (set_volume clamped it), eax = 1.  Stores level+1 in the cache, re-applies the
                   level to every voice of every loaded sample (SetVolume = sound2.dat volume + attenuation), sets the
                   game's Windows per-application volume (the WaveOut line of every mixer that has one) back to its
                   maximum - the old code had turned that down - and returns eax = 0, so set_volume takes its
                   "no mixer" exit and touches nothing else.
  +SETVOL  setvol  ebx = IDirectSoundBuffer*, edx = volume in hundredths of a dB (<= 0).  Adds the attenuation of the
                   current level (the cache, or the saved level at 0x00488E0C when nothing was pressed yet), clamps at
                   DSBVOLUME_MIN (-10000), calls IDirectSoundBuffer::SetVolume and returns its HRESULT in eax.
                   Called from the three SetVolume sites of the sample engine (0x00431006, 0x00431257, 0x00431374).
"""
import struct
import sys

MODULE_VA = 0x004525E0          # Council Wars exe
MODULE_LEN = 0x278              # ..0x00452858 (set_volume starts there)

# the globals the module reaches (all relative to the base register at run time)
CACHE = 0x005337CC              # stock: the mixer control's minimum (dead with the walk) -> level + 1, 0 = nothing pressed yet
SAVED_LEVEL = 0x00488E0C        # the sound level the main menu keeps (.data, default 5; copied into the game state and back)
SOUND_OFF = 0x00489750          # non-zero = no sound (the engine's own check)
SAMPLES = 0x004DFF30            # 200 entries of 116 bytes: +0 voices (byte), +0x44 volume, +0x48 loaded (byte), +0x4C voice buffers[10]
SAMPLE_LEN, SAMPLE_COUNT = 116, 200
HMX, LINE, LC, DET, VAL, CTRL = 0x005337C8, 0x005336F0, 0x00533798, 0x005337B0, 0x005337D0, 0x005337D4   # the stock walk's .bss buffers
IAT = {'mixerClose': 0x00480574, 'mixerGetLineControlsA': 0x0048057C, 'mixerGetLineInfoA': 0x00480580,
       'mixerGetNumDevs': 0x00480584, 'mixerOpen': 0x00480588, 'mixerSetControlDetails': 0x0048058C}

# attenuation per level in hundredths of a dB: 36*log10(level/10) - the taper of the Windows per-application volume
# the old slider set (measured: session 0.5 = -10.8 dB, 0.2 = -25.8 dB); level 0 = DSBVOLUME_MIN (silence)
ATTEN = [-10000, -3600, -2517, -1883, -1433, -1084, -799, -558, -349, -165, 0]
assert len(ATTEN) == 11

PART1 = '''
sfx_set:
    pushad
    call base1
base1:
    pop ebp
    sub ebp, 6                        ; ebp = the module's address (base1 = sfx_set + 6)
    lea eax, [ecx+1]
    mov [ebp + {CACHE_D}], eax        ; cache = level + 1
    cmp dword ptr [ebp + {SOUND_OFF_D}], 0
    jne restore
    xor esi, esi                      ; sample id
    lea edi, [ebp + {SAMPLES_D}]      ; its entry
samp:
    cmp byte ptr [edi + 0x48], 1      ; loaded?
    jne next
    movzx ecx, byte ptr [edi]         ; voices
    jecxz next
voice:
    mov ebx, [edi + ecx*4 + 0x48]     ; buffer of voice ecx-1 (0x4C - 4)
    mov edx, [edi + 0x44]             ; sound2.dat volume
    call {SETVOL_VA}
    loop voice
next:
    add edi, {SAMPLE_LEN}
    inc esi
    cmp esi, {SAMPLE_COUNT}
    jb samp
restore:
    call dword ptr [ebp + {IAT_mixerGetNumDevs_D}]
    mov esi, eax                      ; mixers left
mix:
    dec esi
    js done
    push 0
    push 0
    push 0
    push esi
    lea eax, [ebp + {HMX_D}]
    push eax
    call dword ptr [ebp + {IAT_mixerOpen_D}]
    test eax, eax
    jnz mix
    lea edi, [ebp + {LINE_D}]         ; MIXERLINE
    mov dword ptr [edi], 0xA8
    mov dword ptr [edi + 0x18], 0x1008   ; MIXERLINE_COMPONENTTYPE_SRC_WAVEOUT
    push 3                            ; MIXER_GETLINEINFOF_COMPONENTTYPE
    push edi
    push dword ptr [ebp + {HMX_D}]
    call dword ptr [ebp + {IAT_mixerGetLineInfoA_D}]
    test eax, eax
    jnz close
    lea edi, [ebp + {LC_D}]           ; MIXERLINECONTROLS
    mov dword ptr [edi], 0x18
    mov eax, [ebp + {LINE_D} + 0xC]   ; dwLineID
    mov [edi + 4], eax
    mov dword ptr [edi + 8], 0x50030001  ; MIXERCONTROL_CONTROLTYPE_VOLUME
    mov dword ptr [edi + 0xC], 1
    mov dword ptr [edi + 0x10], 0x94
    lea eax, [ebp + {CTRL_D}]
    mov [edi + 0x14], eax
    push 2                            ; MIXER_GETLINECONTROLSF_ONEBYTYPE
    push edi
    push dword ptr [ebp + {HMX_D}]
    call dword ptr [ebp + {IAT_mixerGetLineControlsA_D}]
    test eax, eax
    jnz close
    lea edi, [ebp + {DET_D}]          ; MIXERCONTROLDETAILS
    mov dword ptr [edi], 0x18
    mov eax, [ebp + {CTRL_D} + 4]     ; dwControlID
    mov [edi + 4], eax
    mov dword ptr [edi + 8], 1
    mov dword ptr [edi + 0xC], 0
    mov dword ptr [edi + 0x10], 4
    lea eax, [ebp + {VAL_D}]
    mov [edi + 0x14], eax
    mov eax, [ebp + {CTRL_D} + 0x68]  ; lMaximum
    mov [ebp + {VAL_D}], eax
    push 0
    push edi
    push dword ptr [ebp + {HMX_D}]
    call dword ptr [ebp + {IAT_mixerSetControlDetails_D}]
close:
    push dword ptr [ebp + {HMX_D}]
    call dword ptr [ebp + {IAT_mixerClose_D}]
    jmp mix
done:
    popad
    xor eax, eax
    ret
'''

PART2 = '''
setvol:
    push ecx
    push edx
    call base2
base2:
    pop ecx                           ; ecx = base2's address
    mov eax, [ecx + {CACHE_B2}]       ; CACHE - base2
    test eax, eax
    jnz have
    mov eax, [ecx + {SAVED_B2}]       ; SAVED_LEVEL - base2
    inc eax
have:
    dec eax
    cmp eax, 10
    jbe ok
    mov eax, 10
ok:
    movsx eax, word ptr [ecx + eax*2 + {TABLE_B2}]   ; table - base2
    add edx, eax
    cmp edx, -10000
    jge clamped
    mov edx, -10000
clamped:
    push edx
    mov eax, [ebx]
    push ebx
    call dword ptr [eax + 0x3C]       ; IDirectSoundBuffer::SetVolume
    pop edx
    pop ecx
    ret
'''


def strip_comments(src):
    """keystone reads `;` as a statement separator, so the comments go before assembling."""
    return chr(10).join(l.split(';')[0].rstrip() for l in src.splitlines())


def assemble():
    from keystone import Ks, KS_ARCH_X86, KS_MODE_32
    ks = Ks(KS_ARCH_X86, KS_MODE_32)
    subs = dict(CACHE_D=CACHE - MODULE_VA, SOUND_OFF_D=SOUND_OFF - MODULE_VA, SAMPLES_D=SAMPLES - MODULE_VA,
                HMX_D=HMX - MODULE_VA, LINE_D=LINE - MODULE_VA, LC_D=LC - MODULE_VA, DET_D=DET - MODULE_VA,
                VAL_D=VAL - MODULE_VA, CTRL_D=CTRL - MODULE_VA, SAMPLE_LEN=SAMPLE_LEN, SAMPLE_COUNT=SAMPLE_COUNT)
    subs.update({'IAT_%s_D' % k: v - MODULE_VA for k, v in IAT.items()})
    part1 = bytes(ks.asm(strip_comments(PART1.format(SETVOL_VA=MODULE_VA, **subs)), MODULE_VA)[0])
    setvol_off = len(part1)                                 # a rel32 call: the length does not depend on the target
    part1 = bytes(ks.asm(strip_comments(PART1.format(SETVOL_VA=MODULE_VA + setvol_off, **subs)), MODULE_VA)[0])
    assert len(part1) == setvol_off
    base2 = MODULE_VA + setvol_off + 7                      # push ecx ; push edx ; call base2 ; base2:
    table_off = setvol_off + 60                             # guess, settled below
    for _ in range(8):
        part2 = bytes(ks.asm(strip_comments(PART2.format(CACHE_B2=CACHE - base2, SAVED_B2=SAVED_LEVEL - base2,
                                                         TABLE_B2=MODULE_VA + table_off - base2)), MODULE_VA + setvol_off)[0])
        if setvol_off + len(part2) == table_off:
            break
        table_off = setvol_off + len(part2)
    else:
        raise SystemExit('the table offset did not settle')
    code = part1 + part2 + b''.join(struct.pack('<h', a) for a in ATTEN)
    assert len(code) <= MODULE_LEN, len(code)
    return code, setvol_off


def listing(code):
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    out = []
    for ins in Cs(CS_ARCH_X86, CS_MODE_32).disasm(code, MODULE_VA):
        out.append('%08X: %-24s %s %s' % (ins.address, ins.bytes.hex(' '), ins.mnemonic, ins.op_str))
    return '\n'.join(out)


def emulate(code, setvol_off):
    """unicorn: a fake image around the module - the sound table, the globals, the IAT, fake COM objects."""
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESP, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_EIP
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x400000, 0x200000)            # the whole image
    uc.mem_map(0x7F000000, 0x10000)           # the stack
    uc.mem_map(0x10000000, 0x10000)           # fake objects, stubs
    uc.mem_write(MODULE_VA, code)
    log = []
    STUB = 0x10000000                          # each import / method: `ret N` recorded by a hook
    stubs = {}

    def stub(name, argc, result=0):
        va = STUB + 0x10 * len(stubs)
        stubs[va] = (name, argc, result)
        uc.mem_write(va, b'\xC2' + struct.pack('<H', argc * 4))     # ret argc*4 (stdcall)
        return va

    def on_code(uc, address, size, user):
        if address in stubs:
            name, argc, result = stubs[address]
            esp = uc.reg_read(UC_X86_REG_ESP)
            args = [struct.unpack('<I', uc.mem_read(esp + 4 + 4 * i, 4))[0] for i in range(argc)]
            log.append((name, args))
            uc.reg_write(UC_X86_REG_EAX, result(args) if callable(result) else result)
    uc.hook_add(UC_HOOK_CODE, on_code)
    # IAT slots -> stubs
    def fake_open(args):
        uc.mem_write(args[0], struct.pack('<I', 0xABCD))      # *phmx
        return 0
    def fake_lineinfo(args):
        uc.mem_write(args[1] + 0xC, struct.pack('<I', 0x10000))   # dwLineID
        return 0
    def fake_linectrls(args):
        pamxctrl = struct.unpack('<I', uc.mem_read(args[1] + 0x14, 4))[0]
        uc.mem_write(pamxctrl + 4, struct.pack('<I', 6))          # dwControlID
        uc.mem_write(pamxctrl + 0x68, struct.pack('<I', 65535))   # lMaximum
        return 0
    imports = {'mixerGetNumDevs': (0, 2), 'mixerOpen': (5, fake_open), 'mixerGetLineInfoA': (3, fake_lineinfo),
               'mixerGetLineControlsA': (3, fake_linectrls), 'mixerSetControlDetails': (3, 0), 'mixerClose': (1, 0)}
    for name, (argc, res) in imports.items():
        uc.mem_write(IAT[name], struct.pack('<I', stub(name, argc, res)))
    # two fake buffers with a vtable whose SetVolume (+0x3C) records its arguments
    setvol_stub = stub('SetVolume', 2, 0)
    VT = 0x10001000
    uc.mem_write(VT + 0x3C, struct.pack('<I', setvol_stub))
    BUF1, BUF2 = 0x10002000, 0x10002100
    for b in (BUF1, BUF2):
        uc.mem_write(b, struct.pack('<I', VT))
    # sound table: sample 7 loaded with 2 voices at volume -300, sample 9 loaded with 1 voice at 0, sample 3 not loaded
    def entry(i, loaded, vol, bufs):
        e = SAMPLES + SAMPLE_LEN * i
        uc.mem_write(e, bytes([len(bufs)]))
        uc.mem_write(e + 0x44, struct.pack('<i', vol))
        uc.mem_write(e + 0x48, bytes([1 if loaded else 0]))
        for k, b in enumerate(bufs):
            uc.mem_write(e + 0x4C + 4 * k, struct.pack('<I', b))
    entry(7, True, -300, [BUF1, BUF2]); entry(9, True, 0, [BUF2]); entry(3, False, -100, [BUF1])
    uc.mem_write(SAVED_LEVEL, struct.pack('<i', 5))

    def run(entry_va, regs):
        for r, v in regs.items():
            uc.reg_write(r, v)
        uc.reg_write(UC_X86_REG_ESP, 0x7F008000)
        uc.mem_write(0x7F008000, struct.pack('<I', 0xDEADBEEF))   # return address
        uc.emu_start(entry_va, 0xDEADBEEF, count=100000)
        return {r: uc.reg_read(r) for r in (UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI, UC_X86_REG_EBP, UC_X86_REG_ESP)}

    # 1. setvol with nothing pressed yet: saved level 5 -> -1084; volume -300 -> -1384
    del log[:]
    r = run(MODULE_VA + setvol_off, {UC_X86_REG_EBX: BUF1, UC_X86_REG_EDX: -300 & 0xFFFFFFFF, UC_X86_REG_ECX: 0x11111111, UC_X86_REG_ESI: 0x22222222, UC_X86_REG_EDI: 0x33333333, UC_X86_REG_EBP: 0x44444444})
    assert log == [('SetVolume', [BUF1, (-1384) & 0xFFFFFFFF])], log
    assert r[UC_X86_REG_ECX] == 0x11111111 and r[UC_X86_REG_EDX] == (-300 & 0xFFFFFFFF) and r[UC_X86_REG_EBX] == BUF1 and r[UC_X86_REG_ESI] == 0x22222222 and r[UC_X86_REG_EDI] == 0x33333333 and r[UC_X86_REG_EBP] == 0x44444444, r
    assert r[UC_X86_REG_ESP] == 0x7F008004 and r[UC_X86_REG_EAX] == 0
    # 2. clamp: level 0 -> DSBVOLUME_MIN
    uc.mem_write(CACHE, struct.pack('<I', 1))
    del log[:]
    run(MODULE_VA + setvol_off, {UC_X86_REG_EBX: BUF2, UC_X86_REG_EDX: -300 & 0xFFFFFFFF})
    assert log == [('SetVolume', [BUF2, (-10000) & 0xFFFFFFFF])], log
    # 3. sfx_set level 8: cache = 9, the three voices re-applied, the Windows walk over 2 mixers, eax = 0, every other register kept
    del log[:]
    r = run(MODULE_VA, {UC_X86_REG_ECX: 8, UC_X86_REG_EAX: 1, UC_X86_REG_EBX: 0x55, UC_X86_REG_EDX: 0x66, UC_X86_REG_ESI: 0x77, UC_X86_REG_EDI: 0x88, UC_X86_REG_EBP: 0x99})
    assert struct.unpack('<I', uc.mem_read(CACHE, 4))[0] == 9
    calls = [(n, a) for n, a in log if n == 'SetVolume']
    assert sorted(calls) == sorted([('SetVolume', [BUF2, -349 & 0xFFFFFFFF]), ('SetVolume', [BUF1, -649 & 0xFFFFFFFF]), ('SetVolume', [BUF2, -649 & 0xFFFFFFFF])]), calls
    names = [n for n, a in log if n != 'SetVolume']
    assert names == ['mixerGetNumDevs'] + ['mixerOpen', 'mixerGetLineInfoA', 'mixerGetLineControlsA', 'mixerSetControlDetails', 'mixerClose'] * 2, names
    det = [a for n, a in log if n == 'mixerSetControlDetails'][0]
    assert det[0] == 0xABCD and det[1] == DET and det[2] == 0
    assert struct.unpack('<I', uc.mem_read(DET + 4, 4))[0] == 6 and struct.unpack('<I', uc.mem_read(VAL, 4))[0] == 65535
    assert struct.unpack('<I', uc.mem_read(LC + 4, 4))[0] == 0x10000 and struct.unpack('<I', uc.mem_read(LINE + 0x18, 4))[0] == 0x1008
    assert r[UC_X86_REG_EAX] == 0 and r[UC_X86_REG_EBX] == 0x55 and r[UC_X86_REG_ECX] == 8 and r[UC_X86_REG_EDX] == 0x66 and r[UC_X86_REG_ESI] == 0x77 and r[UC_X86_REG_EDI] == 0x88 and r[UC_X86_REG_EBP] == 0x99 and r[UC_X86_REG_ESP] == 0x7F008004, r
    # 4. sound off: no SetVolume, the Windows walk still runs
    uc.mem_write(SOUND_OFF, struct.pack('<I', 1))
    del log[:]
    run(MODULE_VA, {UC_X86_REG_ECX: 3, UC_X86_REG_EAX: 1})
    assert not [n for n, a in log if n == 'SetVolume'] and log[0][0] == 'mixerGetNumDevs'
    assert struct.unpack('<I', uc.mem_read(CACHE, 4))[0] == 4
    return 'emulation: 4 scenarios passed'


def main():
    code, setvol_off = assemble()
    print('MODULE_HEX (%d bytes, setvol at +0x%X, table at +0x%X):' % (len(code), setvol_off, len(code) - 2 * len(ATTEN)))
    print(code.hex())
    if 'nolist' not in sys.argv:
        try:
            print(listing(code[:len(code) - 2 * len(ATTEN)]))
        except ImportError:
            pass
    try:
        print(emulate(code, setvol_off))
    except ImportError:
        print('(unicorn not installed: the emulation check was skipped)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
