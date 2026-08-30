# LaunchAgent templates

The committed `.plist` files are macOS-only templates. They deliberately use
`__PROJECT_ROOT__`, `__HOME__`, and `__PATH__` placeholders rather than a
developer's absolute path. `app.runtime_recovery` renders those placeholders
into the user's `~/Library/LaunchAgents` directory only on Darwin, validates
the rendered files with `plutil`, and never attempts LaunchAgent operations on
Linux.
