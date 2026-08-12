running on this setup.

This document covers a specific crash — `javaw.exe` dying instantly on
every launch attempt — and the binary patch that fixes it.

![status](https://img.shields.io/badge/status-working-brightgreen)

---

## TL;DR

`jvm.dll` (client version of the dll) from the Java 5 HotSpot Client VM executes the x86
`CPUID` instruction during startup, before any Java bytecode runs.
`WX86CPU.DLL` (SoftWindows32's CPU emulator) only implements the base
**i486** instruction set — no `CPUID` — so it faults and crashs
`javaw.exe` immediately.

The fix: NOP out the 2 bytes that make up the `CPUID` opcode (`0F A2`)
inside `jvm.dll` (Client Version), at fixed file offsets `0x8091` / `0x8098`
(specific to this exact `1.5.0-b64` build). After patching, the VM
boots, JITs, and runs real bytecode — confirmed by reaching the
Minecraft Beta 1.7.3 main menu on real Wii hardware.

---

## Environment

| Component | Detail |
|---|---|
| Console | Nintendo Wii (`PROCESSOR_IDENTIFIER=Broadway`) |
| Host OS | Windows NT 4.0 for PowerPC |
| Emulation layer | SoftWindows32 (`WX86.DLL`, `WX86CPU.DLL`, `WX86.INF`) |
| Emulated CPU class | i486 (per `WX86CPU.DLL` internal strings) |
| JVM | Java HotSpot(TM) Client VM, `1.5.0-b64`, built Sep 15 2004, MS VC++ 6.0 |
| Target app | `net.minecraft.client.Minecraft` (Minecraft Beta 1.7.3) via MiniJGL fork for Windows NT 3.51, Java 5-recompiled |

---

## The Original Symptom

`javaw.exe` crashed immediately on every launch attempt — no window,
no error dialog, nothing. This looked, at first, like a missing-DLL /
unresolved-import problem (a reasonable first guess, since that's the
usual cause of instant native crashes on legacy Windows).

## Investigation

### 1. Ruling out missing imports

Both `jvm.dll` (Client Version) and `jvm.dll` (Server Version) were inspected with `pefile`.
Their import tables only reference standard Win32 DLLs:

```
KERNEL32.dll
USER32.dll
ADVAPI32.dll
WINMM.dll
MSVCRT.dll
```

Nothing exotic, nothing missing. This ruled out the "needs a stub DLL"
theory for this particular crash — every import resolves cleanly
against any stock Windows NT install.

### 2. Fingerprinting the emulator

Strings extracted from `WX86CPU.DLL`:

```
Win32 i486 emulator
opc0_table$S24485
opc1_table$S24486
opc4_table$S24487
opc6_table$S24488
opc7_table$S24489
opcode_table$S24515
illegal_op
illegal_op_int
```

No `"GenuineIntel"` string anywhere in the binary, and no evidence of
`CPUID`-response logic. Combined with the "i486 emulator" self-
description and 486-era opcode table naming, this strongly suggested
`CPUID` (opcode `0F A2`, introduced with the Pentium — not present on
the original Intel 80486) is simply not implemented.

### 3. Locating the CPUID emission in HotSpot

HotSpot's `VM_Version::initialize()` runs very early during VM
startup — before any class loading — and executes a small stub
generated at runtime (`VM_Version_StubGenerator`) to probe CPU
features via `CPUID`. This matched the symptom exactly: an instant
crash, before any bytecode-level activity was possible.

Disassembling `jvm_client.dll` (via `pefile` + `capstone`) for the
byte-emission pattern HotSpot's assembler uses
(`emit_int8(0x0F); emit_int8(0xA2);`) found exactly **one** match in
the whole `.text` section:

```
VA 0x6d64808d:  push esi
                mov  esi, ecx
VA 0x6d648090:  push 0x0f          ; 6A 0F   -> file offset 0x8091
                call emit_int8
VA 0x6d648097:  push 0xa2          ; 68 A2 00 00 00 -> file offset 0x8098
                mov  ecx, esi
                call emit_int8
                ret
```

This is `MacroAssembler::cpuid()` — the function whose entire job is
to write the two bytes `0F A2` (the `CPUID` opcode) into a
dynamically-generated code buffer.

> **Note on `jvm_server.dll`:** the same search pattern found **no
> match** in the server (C2) compiler's build. The server compiler
> appears to inline opcode emission differently than the client (C1)
> compiler, so this exact heuristic doesn't locate the equivalent
> site there. A proper disassembler (Ghidra/IDA) would be needed to
> map it. **The server DLL has not been patched or tested.**

---

## The Patch

Two single bytes, inside `jvm.dll` (`1.5.0-b64` build only):

| File offset | Original byte | Patched byte | Meaning |
|---|---|---|---|
| `0x8091` | `0x0F` | `0x90` (NOP) | first byte of the `CPUID` opcode |
| `0x8098` | `0xA2` | `0x90` (NOP) | second byte of the `CPUID` opcode |

With both bytes NOPed, the runtime-generated stub does nothing instead
of executing `CPUID`. `VM_Version` ends up reading whatever was
already sitting in `EAX`/`EBX`/`ECX`/`EDX` at that point rather than
real CPU-identification data — not "correct" in a strict sense, but
enough to get the VM safely past startup on this specific hardware
without hitting `WX86CPU.DLL`'s `illegal_op` fault.

No other bytes in the file are touched. File size and every other
byte are identical to the original.

### Patch script

```python
#!/usr/bin/env python3
"""
Patch: strip the CPUID instruction from HotSpot 1.5.0-b64 jvm_client.dll
so it runs under Wx86CPU.DLL (SoftWindows32 / Windows NT for PowerPC,
e.g. Nintendo Wii).

Root cause: on VM startup, HotSpot's runtime code-generator emits a
tiny stub containing the x86 CPUID opcode (0F A2) to probe CPU features.
Wx86CPU.DLL only emulates the base 486 instruction set (no CPUID),
so it raises an illegal-instruction fault and kills javaw.exe before
any Java bytecode ever runs.

Fix: NOP out the two bytes that make up the CPUID opcode inside the
generator function (confirmed via disassembly at file offsets
0x8091 / 0x8098 in this exact 1.5.0-b64 client build). The generated
stub then does nothing instead of faulting, and VM_Version falls back
to whatever was already in EAX/EBX/ECX/EDX -- enough to get the VM
past startup on this hardware.

Usage: python3 patch_cpuid.py jvm.dll jvm_client_patched.dll
"""
import sys

OFF_0F = 0x8091
OFF_A2 = 0x8098

def main():
    if len(sys.argv) != 3:
        print("usage: patch_cpuid.py <input.dll> <output.dll>")
        sys.exit(1)

    with open(sys.argv[1], "rb") as f:
        raw = bytearray(f.read())

    if raw[OFF_0F] != 0x0F or raw[OFF_A2] != 0xA2:
        print(f"WARNING: expected bytes not found at offsets "
              f"{hex(OFF_0F)}/{hex(OFF_A2)} -- this file may not be "
              f"exactly the 1.5.0-b64 build. Aborting.")
        sys.exit(2)

    raw[OFF_0F] = 0x90  # NOP
    raw[OFF_A2] = 0x90  # NOP

    with open(sys.argv[2], "wb") as f:
        f.write(raw)

    print("Patch applied successfully.")
    print(f"  offset {hex(OFF_0F)}: 0x0F -> 0x90 (NOP)")
    print(f"  offset {hex(OFF_A2)}: 0xA2 -> 0x90 (NOP)")

if __name__ == "__main__":
    main()
```

> The offsets are hardcoded on purpose rather than pattern-matched at
> runtime. Pattern-matching worked for this client build by luck, but
> failed outright on the server build — hardcoded + asserted offsets
> are safer for a build known in advance, and the script refuses to
> touch a file that doesn't match.

### Applying it

1. Get `jvm_client.dll` from your `jre1.5.0\bin\client\` folder.
2. Run: `python3 patch_cpuid.py jvm.dll jvm_client_patched.dll`
3. Back up the original, then replace `jvm.dll` inside
   `jre1.5.0\bin\client\` with the patched file (rename it to
   `jvm.dll`).
4. Launch with `-client` explicitly to make sure the patched client
   compiler is what actually loads.

---

## Result

With the patch applied, `javaw.exe` boots the VM, loads classes, JIT-
compiles methods (Runtime1/C1 stubs visible in later crash logs), and
successfully reaches the **Minecraft Beta 1.7.3 main menu**, confirmed
on real Wii hardware (`PROCESSOR_IDENTIFIER=Broadway` in the system
environment).

`javaw.exe -client -version` appears to print nothing visible — likely
just console output not being surfaced by the terminal under Wx86,
rather than a crash. Redirecting to a file
(`javaw.exe -client -version > out.txt 2>&1`) is recommended to check.

---

## Known Follow-up Issue: CodeCache error

Before reaching the menu, a run against the full Minecraft launch
command produced an HotSpot fatal error:

```
Internal Error (434F444523414348450E43505000D0)
```

Decoded, that hex blob spells `CODE` + `#` + `ACHE` + `\x0E` + `CPP` +
`\x00` + `\xD0` — i.e. a corrupted fragment of an error message
referencing `codeCache.cpp` (the JIT's compiled-code storage
subsystem). The corruption in the middle (`#` where a letter should
be) suggests memory/string handling getting disturbed somewhere in
this environment, possibly incidental to the JIT rather than the
CPUID issue itself.

**Open question:** does reaching the menu (see screenshot evidence)
require `-Xint` (interpreter-only, JIT disabled), or does it work with
the JIT active? This determines whether:

- the `CodeCache` error was a one-off side effect of the (now-fixed)
  CPUID garbage-register issue and can be ignored, or
- there's a separate JIT/CodeCache-related bug still worth patching
  before attempting anything more demanding than the main menu (e.g.
  world generation, which will stress the JIT and GC far more).

This is still being investigated.

---

## Not Yet Done

- **`jvm.dll` (server version of the dll) is unpatched.** The CPUID-emission site in the
  server (C2) compiler build has a different code shape than the
  client build and needs proper disassembler-assisted tracing
  (Ghidra/IDA) rather than the simple heuristic used here.
- **A "correct" fix** would patch the *caller* of the CPUID stub
  (`VM_Version::get_cpu_info()`) to skip the generated stub entirely
  and hardcode a safe/conservative feature bitmask (no CMOV, MMX,
  SSE), rather than NOPing the opcode and leaving garbage register
  values behind. The NOP patch works well enough in practice for this
  use case, but isn't fully principled.
- Resolving whether `-Xint` is required, and if so, whether that's
  acceptable long-term or worth patching around.

---

## Credits

Investigation and patch by [YLucas35](https://github.com/YL353) — reverse
engineering assisted by Claude (Anthropic).
