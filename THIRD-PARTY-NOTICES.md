# Upstream attribution

The core and command-line watcher derive from **thefyfy/UnifiStockWatcher**,
revision `961af6b8888f1d601849411407624c012c11723d`, itself derived from
**jamesccupps/UnifiStockWatcher**. Both repositories carry the MIT license with
`Copyright (c) 2026 James Cupps`; that exact notice is preserved in
`licenses/upstream-MIT.txt` and the root license.

This fork adds a Linux/systemd installation, Linux reliability changes, alert
controls, and a browser frontend. The Windows desktop program is not bundled.
All runtime project source is included; the installer does not fetch upstream code.

Flask, Gunicorn, Requests and their dependencies are installed from PyPI under
their respective licenses; their source/wheels are not included in this archive.
The ntfy service and mobile applications are separate projects and are not bundled.

Not affiliated with or endorsed by Ubiquiti, Tailscale, ntfy, or anyone else.
