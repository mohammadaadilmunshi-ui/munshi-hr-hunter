# Persistent State Policy

Git stores source and configuration.

Git does not store production state.

Persistent production state currently includes at minimum:

1. Hunter SQLite database.
2. n8n SQLite database.
3. n8n encryption key, stored as a secret rather than a file in Git.
4. Any runtime state later proven authoritative.

Cloud deployment must use persistent storage and a controlled snapshot process.
