# Creative Suite application adapters

Each application owns four directories: `mac/`, `linux/`, `win/`, and `test/`.

- `mac/`, `linux/`, `win/`: platform-specific `build.sh` and `stage.sh` adapters.
- `test/test.sh`: one shared application smoke-test entry point, invoked as
  `test.sh <linux|mac|win> [headless|gui]`.

The nine existing Linux document/export smoke contracts reuse the established
LinuxKit guest runner and output validators. Those implementations remain in
`creativeSuite/linux_guest.py` and `creativeSuite/linux_smoke_tests.py` because
Update Data and the guest image still call them. Moving their internals into
individual scripts requires a separate coordinated guest-image migration.

Real GUI tests and macOS/Windows headless tests are not implemented; shared
scripts fail explicitly instead of treating artifact presence as a smoke pass.

Build/stage acquisition behavior and persistent application storage are unchanged.
