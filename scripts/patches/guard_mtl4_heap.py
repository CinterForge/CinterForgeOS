#!/usr/bin/env python3
"""Guard Metal heap creation in the disposable Mesa staging copy."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: guard_mtl4_heap.py path/to/mtl_heap.m')
path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f'error: missing KosmicKrisp heap bridge: {path}')
source = path.read_text()
old = '''      id<MTLDevice> dev = (id<MTLDevice>)device;
      MTLHeapDescriptor *descriptor = [[MTLHeapDescriptor new] autorelease];
      descriptor.type = MTLHeapTypePlacement;'''
new = '''      id<MTLDevice> dev = (id<MTLDevice>)device;
      /* Avoid dispatch through an unavailable weak-linked class stub. */
      Class heap_descriptor_class = NSClassFromString(@"MTLHeapDescriptor");
      if (heap_descriptor_class == Nil) {
         fprintf(stderr, "KosmicKrisp: MTLHeapDescriptor unavailable at runtime\\n");
         return NULL;
      }
      if (dev == nil || ![dev respondsToSelector:@selector(newHeapWithDescriptor:)]) {
         fprintf(stderr, "KosmicKrisp: Metal device cannot create heaps\\n");
         return NULL;
      }
      MTLHeapDescriptor *descriptor = [[heap_descriptor_class new] autorelease];
      if (descriptor == nil) {
         fprintf(stderr, "KosmicKrisp: MTLHeapDescriptor allocation failed\\n");
         return NULL;
      }
      descriptor.type = MTLHeapTypePlacement;'''
if new in source:
    print(f'[Xcode] Metal heap runtime guard already applied: {path}')
    sys.exit(0)
if source.count(old) != 1:
    raise SystemExit(f'error: unexpected KosmicKrisp Metal heap source: {path}')
path.write_text(source.replace(old, new, 1))
print(f'[Xcode] Guarded Metal heap initialization: {path}')
