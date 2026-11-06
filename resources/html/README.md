# CinterForgeOS shared KDE-style Desktop presentation

This is the **CFOS-owned HTML Desktop UI**. The original desktop reference
artwork and dock icons are retained. `index.html` runs unchanged in the browser
and can be hosted by Swift `WKWebView` under a same-origin mini-server.

- Browser/site mode (`?mode=demo`): launch Code, Voxel and Desktop browser demos
  in local windows and open product pages for other applications. No native
  execution, downloads, or installed-application claims are made.
- Native mode (`?mode=native`): native launch requests go through an **explicit**
  `window.webkit.messageHandlers.cfosDesktop` bridge supplied by the Swift host.
  The bridge is not installed in the current Swift launcher; without it the
  page stays in read-only web-demo mode. The host must allowlist every app ID
  and enforce authorization; never interpret untrusted commands from the page.

`build/website_content.py build` includes these authored files under
`static/cfos/desktop/` in the signed/checksummed CFOS Website content ZIP.
The Website consumes that ZIP through its existing Hugo static mount; it
**does not** duplicate the desktop UI or build Ubuntu to obtain it.

Native KDE Plasma itself remains the actual guest desktop. A `WKWebView`
frontend is a presentation/launch surface, not an OS emulator or terminal.
