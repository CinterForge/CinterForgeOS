#!/usr/bin/env python3
"""Resolve Metal library descriptor/options classes at runtime in staged Mesa.

Only modifies the disposable source copy. Fail on unexpected source layouts;
do not silently build an unguarded Vulkan ICD.
"""
from pathlib import Path
import re
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: guard_mtl4_library.py path/to/mtl_compiler.m')
path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f'error: missing KosmicKrisp compiler bridge: {path}')
source = path.read_text()
marker = 'KosmicKrisp: entering mtl_new_library'
if marker in source:
    print(f'[Xcode] Metal library class guards already applied: {path}')
    sys.exit(0)
start = re.search(r'\bmtl_new_library\s*\([^;]*?\)\s*\{', source, re.S)
if not start:
    raise SystemExit(f'error: cannot find mtl_new_library implementation: {path}')
# Mesa bridge functions are separated by a brace-terminated function body;
# locate its real closing brace while respecting lexical nesting.
level = 1
end = start.end()
while end < len(source) and level:
    if source[end] == '{':
        level += 1
    elif source[end] == '}':
        level -= 1
    end += 1
if level:
    raise SystemExit(f'error: unterminated mtl_new_library implementation: {path}')
body = source[start.end():end-1]
classes = ('MTLCompileOptions', 'MTL4LibraryDescriptor')
for cls in classes:
    pattern = re.compile(r'\[\[\s*' + cls + r'\s+new\s*\]\s+autorelease\s*\]')
    occurrences = len(pattern.findall(body))
    if occurrences != 1:
        raise SystemExit(f'error: expected one {cls} class allocation in mtl_new_library, found {occurrences}: {path}')
    variable = 'kk_' + re.sub(r'(?<!^)(?=[A-Z])', '_', cls).lower() + '_class'
    replacement = ('({ Class ' + variable + ' = objc_getClass("' + cls + '"); '
                   'if (' + variable + ' == Nil) { fprintf(stderr, "KosmicKrisp: missing ' + cls + ' runtime class\\n"); fflush(stderr); } '
                   '(' + variable + ' == Nil ? nil : [[' + variable + ' new] autorelease]); })')
    # No GNU statement-expression reliance: use helper C function instead.
    body = pattern.sub('kk_mtl_new_runtime_object("' + cls + '")', body)
# Objective-C id return is compatible with descriptor/options declarations.
body = ('\n   fprintf(stderr, "KosmicKrisp: entering mtl_new_library compiler=%p\\n", (void *)compiler);\n'
        '   fflush(stderr);\n' + body)
# Guard NULL allocations before downstream Objective-C property access.
# Sending to nil is safe but passing incomplete descriptors to Metal is not.
for var in ('opts', 'desc'):
    # Check that expected declaration still exists in the source.
    if not re.search(r'\b' + var + r'\s*=\s*kk_mtl_new_runtime_object\(', body):
        raise SystemExit(f'error: cannot identify Metal library {var} variable: {path}')
    body = re.sub(r'([^;\n]*\b' + var + r'\s*=\s*kk_mtl_new_runtime_object\([^;]+;)',
                  r'\1\n      if (' + var + r' == nil) return NULL;', body, count=1)
helper = '''\n/* The Metal classes may be weak imported even when the Metal 4 compiler is
 * present. Resolve them against the active Objective-C runtime. */
static id
kk_mtl_new_runtime_object(const char *class_name)
{
   Class cls = objc_getClass(class_name);
   fprintf(stderr, "KosmicKrisp: library class %s=%p\\n", class_name, (void *)cls);
   fflush(stderr);
   if (cls == Nil)
      return nil;
   return [[cls new] autorelease];
}
\n'''
if '#include <objc/runtime.h>' not in source:
    source = source.replace('#include <stdio.h>', '#include <stdio.h>\n#include <objc/runtime.h>', 1)
    start = re.search(r'\bmtl_new_library\s*\([^;]*?\)\s*\{', source, re.S)
    if not start:
        raise SystemExit('error: lost mtl_new_library after header injection')
    level, end = 1, start.end()
    while end < len(source) and level:
        if source[end] == '{': level += 1
        elif source[end] == '}': level -= 1
        end += 1
# Insert the helper at the Objective-C runtime include, never between a
# function return type and its name. This is independent of upstream return
# types (id, id<MTLLibrary>, etc.) and surrounding attributes/comments.
anchor = '#include <objc/runtime.h>'
if source.count(anchor) != 1:
    raise SystemExit(f'error: expected one Objective-C runtime include: {path}')
if 'kk_mtl_new_runtime_object(const char *class_name)' in source:
    raise SystemExit(f'error: helper already exists without guard marker: {path}')
source = source.replace(anchor, anchor + '\n' + helper, 1)
# Re-find the original body after inserting the helper (offsets have changed).
start = re.search(r'\bmtl_new_library\s*\([^;]*?\)\s*\{', source, re.S)
if not start:
    raise SystemExit(f'error: cannot find mtl_new_library after helper insertion: {path}')
level = 1
end = start.end()
while end < len(source) and level:
    if source[end] == '{':
        level += 1
    elif source[end] == '}':
        level -= 1
    end += 1
if level:
    raise SystemExit(f'error: unterminated mtl_new_library after insertion: {path}')
source = source[:start.end()] + body + source[end-1:]
path.write_text(source)
print(f'[Xcode] Guarded Metal library descriptor/options runtime classes: {path}')
