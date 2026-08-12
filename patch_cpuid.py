#!/usr/bin/env python3
"""
Patch: strip the CPUID instruction from HotSpot 1.5.0-b64 jvm.dll
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

Usage: python3 patch_cpuid_en.py jvm.dll jvm_client_patched.dll
"""
import sys

OFF_0F = 0x8091
OFF_A2 = 0x8098

def main():
    if len(sys.argv) != 3:
        print("usage: patch_cpuid_en.py <input.dll> <output.dll>")
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
