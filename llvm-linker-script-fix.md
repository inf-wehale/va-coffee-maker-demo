# LLVM Linker Script Fix — Voice Assistant v1.3.0

**Date:** May 1, 2026  
**Project:** mtb-example-psoc-edge-voice-assistant-deploy (release-v1.3.0)  
**Target:** KIT_PSE84_EVAL_EPC2 (PSOC Edge E84)  
**Toolchain:** LLVM Embedded Toolchain for ARM 19.1.5  

---

## Summary

The Voice Assistant application fails to detect wake words ("OK Infineon") and appears non-functional after boot when compiled with the LLVM toolchain. The ARM Compiler 6 build works correctly. The root cause is a glob pattern mismatch in the CM55 LLVM linker script that creates an orphan `.text` section for `ifx_sp_enh_process`, preventing it from being copied from flash to ITCM at startup.

---

## Symptoms

- Application boots and prints all initialization messages over UART:
  ```
  PSOC Edge MCU: Voice Assistant Demo
  DEEPCRAFT Audio Enhancement initialized
  Voice Assistant initialized!
  Say the wake-word "OK Infineon" followed by a command.
  ```
- Blue User LED remains **OFF** (should be solid ON after init)
- Wake word detection does not trigger
- Push-to-Talk button (BTN1) does not respond
- No crash message or visible error on UART

The init messages appear because they are printed from `main()` before `vTaskStartScheduler()`. The failure occurs after the FreeRTOS scheduler starts, when the audio pipeline first calls `ifx_sp_enh_process()`.

---

## Root Cause

### Affected File

```
bsps/TARGET_APP_KIT_PSE84_EVAL_EPC2/COMPONENT_CM55/TOOLCHAIN_LLVM_ARM/pse84_ns_cm55.ld
```

### The Bug

The linker script has two sections that reference the `sp_enh` and `ifx_va` audio libraries:

1. **`.app_code_main`** (flash, XIP) — uses `EXCLUDE_FILE` to keep these libraries out of flash:
   ```
   *(EXCLUDE_FILE(... *ifx_va*.* *sp_enh*.*) .text*)
   ```

2. **`.app_code_itcm`** (ITCM, copied from flash at startup) — includes these libraries for fast execution:
   ```
   *ifx_va.*(.text* .rodata ...)
   *sp_enh.*(.text* .rodata ...)
   ```

The **EXCLUDE_FILE** patterns use `*sp_enh*.*` and `*ifx_va*.*` (with an extra `*` wildcard after the library name), but the **ITCM include** patterns use `*sp_enh.*` and `*ifx_va.*` (without the extra wildcard).

### Why This Matters

The `audio-voice-core` library provides `ifx_sp_enh_process` as a standalone compiled object file:

```
audio-voice-core/lib/SP_ENH/COMPONENT_CM55/src/ifx_sp_enh_process.o
```

This file's name is `ifx_sp_enh_process.o`. The glob patterns match differently:

| Pattern | Matches `sp_enh.a`? | Matches `ifx_sp_enh_process.o`? |
|---------|---------------------|---------------------------------|
| `*sp_enh.*` | Yes (`sp_enh` + `.a`) | **No** (`sp_enh_process` ≠ `sp_enh`) |
| `*sp_enh*.*` | Yes | **Yes** (`sp_enh` + `_process` + `.o`) |

Result:
- `ifx_sp_enh_process.o:.text.ifx_sp_enh_process` is **excluded** from `.app_code_main` (flash) ✅
- `ifx_sp_enh_process.o:.text.ifx_sp_enh_process` is **not captured** by `.app_code_itcm` ❌
- The linker places it as an **orphan section** in ITCM with VMA = LMA (no flash load address)

### The Consequence

The startup copy table (`__copy_table_start__` / `__copy_table_end__`) only copies `.app_code_itcm` from flash to ITCM. The orphan section `.text.ifx_sp_enh_process` is not part of any copy entry, so its ITCM memory contains uninitialized data (whatever was in ITCM after reset).

When the audio pipeline calls `ifx_sp_enh_process()`, execution jumps to garbage instructions in ITCM, causing a **HardFault**. The HardFault handler is an infinite `while(true){}` loop, so the system silently hangs — no LED updates, no button handling, no audio processing.

### Evidence from the Map File

**Before fix** — orphan section visible:
```
   1c3d0 60639f90        0     1         . = ALIGN ( 4 )          ← end of .app_code_itcm
   1c3d0    1c3d0      6a6     4 .text.ifx_sp_enh_process         ← ORPHAN: VMA=LMA (no flash copy!)
   1c3d0    1c3d0      6a6     4         .../ifx_sp_enh_process.o:(.text.ifx_sp_enh_process)
```

Note: VMA (`1c3d0`) equals LMA (`1c3d0`). A properly placed ITCM section would show VMA in ITCM (0x0xxxx) and LMA in flash (0x60xxxxxx).

**After fix** — no orphan, section absorbed into `.app_code_itcm`:
```
Memory report:
  .app_code_itcm  | 0x00000000 |  117,376    ← was 115,664 + 1,702 orphan = 117,366 ≈ 117,376 aligned
```

### Why ARM Compiler Is Not Affected

The ARM scatter file (`pse84_ns_cm55.sct`) uses a different mechanism:
```
app_code_ram ... {
    *ifx_va* (+RO, +RW, +ZI)
    *sp_enh* (+RO +RW +ZI)
}
```

ARM's `*sp_enh*` pattern with `+RO` captures **all** read-only sections (text + rodata) from any file matching `*sp_enh*`, including `ifx_sp_enh_process.o`. There is no separate exclude/include inconsistency.

---

## Fix

**Changed lines 236–237** in the LLVM linker script:

```diff
         /* Voice/Audio related libraries */
-        *ifx_va.*(.text* .rodata .rodata* .constdata .constdata* .conststring .conststring*)
-        *sp_enh.*(.text* .rodata .rodata* .constdata .constdata* .conststring .conststring*)
+        *ifx_va*.*(.text* .rodata .rodata* .constdata .constdata* .conststring .conststring*)
+        *sp_enh*.*(.text* .rodata .rodata* .constdata .constdata* .conststring .conststring*)
```

This makes the ITCM include patterns consistent with the EXCLUDE_FILE patterns, ensuring all `ifx_va` and `sp_enh` related object files — whether from archives (`.a`) or standalone (`.o`) — are properly placed in ITCM with correct flash-to-ITCM copy entries.

---

## Other Affected Files

The same bug exists in the template linker scripts (not used at build time, but used when creating new projects):

- `templates/TARGET_KIT_PSE84_EVAL_EPC2/COMPONENT_CM55/TOOLCHAIN_LLVM_ARM/pse84_ns_cm55.ld`
- `templates/TARGET_KIT_PSE84_EVAL_EPC4/COMPONENT_CM55/TOOLCHAIN_LLVM_ARM/pse84_ns_cm55.ld`
- `templates/TARGET_KIT_PSE84_AI/COMPONENT_CM55/TOOLCHAIN_LLVM_ARM/pse84_ns_cm55.ld`

---

## Verification

After applying the fix:

1. Clean the CM55 build: `make -C proj_cm55 clean_proj`
2. Rebuild and program: `make program TOOLCHAIN=LLVM_ARM CY_COMPILER_LLVM_ARM_DIR=<path> CONFIG=Debug`
3. Confirm:
   - Blue LED is solid ON after boot
   - "OK Infineon" wake word is detected
   - Push-to-Talk (BTN1) triggers command listening
   - Memory report shows no orphan sections and `.app_code_itcm` size increases by ~1.7 KB
