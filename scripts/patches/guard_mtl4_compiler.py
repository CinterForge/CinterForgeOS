#!/usr/bin/env python3
"""Guard the Metal 4 compiler's first Objective-C dispatch in KosmicKrisp.

The host driver is built from a separate Mesa source tree. Patch only the
copied source under external Builds, never the developer's source checkout.
Refuse unknown versions instead of silently changing/ignoring upstream code.
"""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: guard_mtl4_compiler.py path/to/mtl_compiler.m')
path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f'error: missing KosmicKrisp Metal compiler bridge: {path}')
source = path.read_text()
# A crash at mtl_new_compiler + 36 can occur before the first Metal 4
# selector is dispatched. Checkpoints make that distinction observable.
if '#include <objc/runtime.h>' not in source:
    source = source.replace('#include <stdio.h>', '#include <stdio.h>\n#include <objc/runtime.h>', 1)
old = '''      MTL4CompilerDescriptor *desc = [[MTL4CompilerDescriptor new] autorelease];

      NSError *error;
      id<MTL4Compiler> compiler = [dev newCompilerWithDescriptor:desc error:&error];'''
new = '''      /* Resolve the Metal 4 class through the running Objective-C runtime,
       * rather than using an unchecked weak-import class reference. The
       * latter can be null and cause an instruction fetch from address zero
       * when the driver is loaded dynamically by the Vulkan ICD loader. */
      Class compiler_descriptor_class = objc_getClass("MTL4CompilerDescriptor");
      if (compiler_descriptor_class == Nil) {
         fprintf(stderr, "KosmicKrisp: Metal 4 MTL4CompilerDescriptor class is unavailable at runtime\\n");
         return NULL;
      }
      if (dev == nil || ![dev respondsToSelector:@selector(newCompilerWithDescriptor:error:)]) {
         fprintf(stderr, "KosmicKrisp: MTLDevice does not expose Metal 4 compiler creation\\n");
         return NULL;
      }
      MTL4CompilerDescriptor *desc = [[compiler_descriptor_class new] autorelease];
      if (desc == nil) {
         fprintf(stderr, "KosmicKrisp: failed to allocate Metal 4 compiler descriptor\\n");
         return NULL;
      }

      NSError *error = nil;
      id<MTL4Compiler> compiler = [dev newCompilerWithDescriptor:desc error:&error];
      if (compiler == nil) {
         fprintf(stderr, "KosmicKrisp: Metal compiler creation failed\\n");
      }'''
if new in source:
    print(f'[Xcode] Metal 4 compiler runtime guard already applied: {path}')
    sys.exit(0)
if source.count(old) != 1:
    raise SystemExit(f'error: unsupported upstream Metal compiler implementation: {path}; expected exactly one compiler initializer')
# Trace entry *before* autorelease pool setup, to distinguish an Objective-C
# pool/runtime fault from a missing Metal 4 class or selector fault.
entry_old = '''mtl_new_compiler(mtl_device *device)
{
   @autoreleasepool {'''
entry_new = '''mtl_new_compiler(mtl_device *device)
{
   @autoreleasepool {
'''
if source.count(entry_old) != 1:
    raise SystemExit(f'error: unexpected mtl_new_compiler entry in {path}')
patched = source.replace(old, new, 1).replace(entry_old, entry_new, 1)
path.write_text(patched)
print(f'[Xcode] Guarded Metal 4 class resolution and compiler initialization: {path}')
