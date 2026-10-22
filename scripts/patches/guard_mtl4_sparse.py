#!/usr/bin/env python3
"""Keep Metal sparse-placement capability detection SDK-compatible.

Apply only to the disposable KosmicKrisp Mesa source copy. The Metal 4
selector is optional and may be absent from the selected SDK's MTLDevice
protocol even when it exists on newer macOS runtimes.
"""
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: guard_mtl4_sparse.py path/to/mtl_device.m')
path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f'error: missing KosmicKrisp device bridge: {path}')
source = path.read_text()
old = 'return dev.supportsPlacementSparse;'
marker = 'KosmicKrisp: dynamically query supportsPlacementSparse'
if marker in source:
    print(f'[Xcode] Metal sparse placement SDK guard already applied: {path}')
    sys.exit(0)
if source.count(old) != 1:
    raise SystemExit(f'error: expected exactly one direct Metal sparse-placement property access: {path}')
if 'mtl_device_supports_placement_sparse' not in source:
    raise SystemExit(f'error: missing sparse-placement capability function: {path}')
# Use runtime introspection; do not hardcode unsupported capabilities as false.
# The selector takes no arguments and returns BOOL. On arm64 the typed call
# through objc_msgSend is the correct Objective-C ABI for this signature.
replacement = '''{
         /* KosmicKrisp: dynamically query supportsPlacementSparse.
          * Xcode's SDK may not declare this optional newer selector. */
         SEL selector = sel_registerName("supportsPlacementSparse");
         if (![dev respondsToSelector:selector])
            return false;
         return ((BOOL (*)(id, SEL))objc_msgSend)(dev, selector);
      }'''
source = source.replace(old, replacement, 1)
anchor = '#include <Metal/MTLDevice.h>'
if source.count(anchor) != 1:
    raise SystemExit(f'error: expected one Metal device header: {path}')
source = source.replace(anchor, anchor + '\n#include <objc/message.h>\n#include <objc/runtime.h>', 1)
path.write_text(source)
print(f'[Xcode] Guarded optional Metal sparse-placement runtime selector: {path}')
