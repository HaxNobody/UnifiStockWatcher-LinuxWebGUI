# Upstream attribution

The core and command-line watcher derive from **thefyfy/UnifiStockWatcher**,
revision `961af6b8888f1d601849411407624c012c11723d`, itself derived from
**jamesccupps/UnifiStockWatcher**. Both repositories carry the MIT license with
`Copyright (c) 2026 James Cupps`; that exact notice is preserved in
`licenses/upstream-MIT.txt` and the root license.

This fork adds a Linux/systemd installation, Linux reliability changes, alert
controls, and a browser frontend. The Windows desktop program is not bundled.
All runtime project source is included; the installer does not fetch upstream code.

Release 1.4.0 additionally ports store parsing from jamesccupps/UnifiStockWatcher
commit `3515b84d1d96b31382efbc8019d37af4c1b55397` (v3.2.7 core), under the same
preserved MIT notice. See app/store_api.py and CHANGELOG.md for Linux adaptations.

Flask, Gunicorn, Requests and their dependencies are installed from PyPI under
their respective licenses; their source/wheels are not included in this archive.
The ntfy service and mobile applications are separate projects and are not bundled.

Not affiliated with or endorsed by Ubiquiti, Tailscale, ntfy, or anyone else.
