#!/usr/bin/env python3
"""Animated CinterForge terminal display for a real command's output.

Usage: python3 scripts/steam/fire.py --stage 'BUILDING GUEST LAYER' -- command arg...
       python3 scripts/steam/fire.py --demo

The bottom row reflects raw terminal output (including carriage-return updates).
A complete byte-for-byte output stream is optionally saved with --log FILE.
"""
import argparse
import codecs
from collections import deque
import math
import os
import pty
import random
import re
import select
import shutil
import signal
import subprocess
import sys
import time

ESC = '\033['
RESET = '\033[0m'
FLAME = [
    '                 ##                  ',
    '                ####                 ',
    '               #####                 ',
    '              #######                ',
    '          ##  ########               ',
    '         #### ##########             ',
    '         ################            ',
    '       ####################          ',
    '      ######################         ',
    '     ########################        ',
    '      ########################       ',
    '       ########################      ',
    '         ####################        ',
    '          ##################         ',
    '            ##############          ',
    '              ##########            ',
]
W, H = max(map(len, FLAME)), len(FLAME)
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])')


def color(r, g, b):
    return f'\033[38;2;{r};{g};{b}m'


def put(out, row, text, columns, *, style='', center=True):
    text = text[:columns]
    left = max(0, (columns - len(text)) // 2) if center else 0
    out.append(f'{ESC}{row};1H{ESC}2K{ESC}{row};{left + 1}H{style}{text}{RESET}')


def draw(frame, stage, terminal_lines):
    """Render ONLY supplied stage/output strings; never manufacture status text."""
    columns, rows = shutil.get_terminal_size((80, 24))
    # Some PTYs report 0x0 before the parent terminal propagates its size.
    columns, rows = columns or 80, rows or 24
    # Ten log rows always take priority over decorative flame height.
    output_rows = min(10, max(1, rows - 4))
    title_row = rows - output_rows - 2
    stage_row = title_row + 1
    log_start = rows - output_rows + 1
    flame_height = max(0, min(H, title_row - 2))
    left = max(0, (columns - W) // 2)
    out = []
    # Clear only the lines we own, including stale log lines.
    for row_number in range(1, rows + 1):
        out.append(f'{ESC}{row_number};1H{ESC}2K')
    for y in range(flame_height):
        row = FLAME[y]
        line = [' '] * W
        for x, ch in enumerate(row):
            if ch != '#':
                continue
            wobble = int(2 * math.sin(frame * .28 + y * .48) * (1 - y / H))
            xx = x + wobble
            if not 0 <= xx < W:
                continue
            heat = (1 - abs(x - W / 2) / (W / 2)) * .65 + (y / H) * .35
            heat += random.uniform(-.12, .12)
            rgb = ((255, 245, 90) if heat > .72 else
                   (255, 160, 0) if heat > .52 else
                   (255, 75, 0) if heat > .32 else (215, 20, 0))
            line[xx] = color(*rgb) + '█' + RESET
        if columns >= W:
            out.append(f'{ESC}{y + 1};{left + 1}H' + ''.join(line))
    if title_row >= 1:
        put(out, title_row, 'C I N T E R F O R G E', columns, style='\033[1m')
    if stage_row >= 1:
        put(out, stage_row, f'[ {stage} ]', columns, style='\033[1;36m')
    # The command reader supplies these strings; the renderer never reads a log.
    visible = list(terminal_lines)[-output_rows:]
    # Bottom-align output: the newest line always stays at the bottom.
    for index, line in enumerate(visible):
        row_number = rows - len(visible) + index + 1
        if row_number <= rows:
            put(out, row_number, line, columns, center=False)
    sys.stdout.write(''.join(out))
    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', default='INSTALLING')
    parser.add_argument('--log', help='Optional raw command-output log')
    parser.add_argument('--demo', action='store_true', help='Preview with actual output from a small demo command')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.demo and args.command:
        parser.error('--demo cannot be combined with a real installer command')
    if args.demo:
        command = [sys.executable, '-u', '-c',
                   'import time\nfor stage in ("Checking prerequisites", "Preparing runtime", "Installing packages", "Verifying installation", "Complete"):\n print(stage, flush=True); time.sleep(1.5)']
        args.stage = 'INSTALLER PREVIEW'
    if not command:
        parser.error('provide -- command args, or --demo')
    # A real installer invocation never uses the preview status generator.
    if not sys.stdout.isatty():
        # Noninteractive installs still capture the same unmodified output.
        with open(args.log, 'ab') if args.log else open(os.devnull, 'wb') as log:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for chunk in iter(lambda: proc.stdout.read(8192), b''):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                if args.log:
                    log.write(chunk)
                    log.flush()
            return proc.wait()
    master, slave = pty.openpty()
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=slave,
                            stderr=slave, start_new_session=True)
    os.close(slave)
    log = open(args.log, 'ab') if args.log else None
    current = ''
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    # ANSI escape sequences can straddle PTY reads; retain parser state.
    escape = ''
    history = deque(maxlen=10)  # Actual output only; no simulated status lines.
    log_offset = os.path.getsize(args.log) if args.log else 0
    log_partial = b''
    log_history = deque(maxlen=10)
    frame = 0
    last = 0.0
    alive = True
    # Alternate screen keeps animation frames out of normal terminal scrollback.
    # Hide cursor and paint in place; only this renderer writes to the TTY.
    sys.stdout.write(f'{ESC}?1049h{ESC}?25l{ESC}H{ESC}2J')
    sys.stdout.flush()
    try:
        while alive or proc.poll() is None:
            ready, _, _ = select.select([master] if alive else [], [], [], .06)
            if ready:
                try:
                    data = os.read(master, 16384)
                except OSError:
                    data = b''
                if not data:
                    alive = False
                else:
                    if log:
                        log.write(data)
                        log.flush()
                    for ch in decoder.decode(data):
                        if escape:
                            escape += ch
                            # CSI, OSC, and short escape sequences.
                            if escape.startswith('\x1b['):
                                if '@' <= ch <= '~':
                                    escape = ''
                            elif escape.startswith('\x1b]'):
                                if ch == '\x07' or escape.endswith('\x1b\\'):
                                    escape = ''
                            elif len(escape) >= 2:
                                escape = ''
                            if len(escape) > 4096:
                                escape = ''
                            continue
                        if ch == '\x1b':
                            escape = ch
                        elif ch == '\r':
                            # Keep the last completed line; update progress in place.
                            current = ''
                        elif ch == '\n':
                            history.append(current)
                            current = ''
                        elif ch == '\b':
                            current = current[:-1]
                        elif ch == '\t':
                            current += ' ' * (8 - len(current) % 8)
                        elif ch.isprintable():
                            current = (current + ch)[-4096:]
            # The installer log is the authoritative source for the display.
            # Read it separately from the animated terminal: child processes may
            # redirect their output to the log instead of the launch PTY.
            if args.log:
                try:
                    size = os.path.getsize(args.log)
                    if size < log_offset:
                        log_offset, log_partial = 0, b''
                        log_history.clear()
                    if size > log_offset:
                        with open(args.log, 'rb') as source:
                            source.seek(log_offset)
                            appended = source.read(min(size - log_offset, 1024 * 1024))
                        log_offset += len(appended)
                        combined = log_partial + appended
                        pieces = combined.split(b'\n')
                        log_partial = pieces.pop()
                        for piece in pieces:
                            # Progress tools commonly update one line with CR.
                            log_history.append(ANSI.sub('', piece.decode('utf-8', 'replace').rstrip('\r').split('\r')[-1]))
                except OSError:
                    pass
            if time.monotonic() - last > .09:
                if args.log:
                    lines = list(log_history)
                    if log_partial:
                        lines.append(ANSI.sub('', log_partial.decode('utf-8', 'replace').rstrip('\r').split('\r')[-1]))
                else:
                    lines = [*history, current] if current else list(history)
                if not lines:
                    lines = ['Waiting for installer output...']
                draw(frame, args.stage, lines)
                frame += 1
                last = time.monotonic()
    except KeyboardInterrupt:
        os.killpg(proc.pid, signal.SIGINT)
    finally:
        proc.wait()
        os.close(master)
        if log:
            log.close()
        # Restore cursor and the untouched original terminal/scrollback.
        sys.stdout.write(RESET + f'{ESC}?25h{ESC}?1049l')
        sys.stdout.flush()
    return proc.returncode


if __name__ == '__main__':
    sys.exit(main())
