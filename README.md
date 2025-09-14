# SofiLab - Server Management Tool

A comprehensive server management tool for SSH connections, server monitoring, installation management, and administration of multiple servers including Proxmox VE hosts, routers, and other remote systems.

## Features Overview

### 🔗 SSH Connection Management

- Connect to multiple configured servers using aliases
- Support for Proxmox VE, routers, and any SSH-accessible host
- SSH key and password authentication
- Smart port fallback and connection handling

### 🖥️ Server Monitoring (Coming Soon)

- Real-time server status monitoring
- Resource usage tracking (CPU, memory, disk)
- Service status checks
- Network connectivity monitoring

### 📦 Installation Management (Coming Soon)

- Automated software installation and updates
- Package management across different distributions
- Configuration deployment and management
- Service orchestration and management

### 🛡️ Security & Administration (Coming Soon)

- User and permission management
- Security audit tools
- Backup and restore operations
- Log monitoring and analysis

## Installation

### Python CLI (Cross‑Platform)

- Install dependencies:

  ```bash
  pip install -r requirements.txt
  ```

- Copy config and edit:

  ```bash
  cp sofilab.conf.sample sofilab.conf
  ```

- Run commands (works on macOS, Linux, Windows):

  ```bash
  python sofilab.py --help
  python sofilab.py status pmx
  python sofilab.py login pmx
  python sofilab.py run-scripts pmx
  ```

- Optional install shortcut:
  - macOS/Linux: `python sofilab.py install` creates `/usr/local/bin/sofilab` symlink.
  - Windows: create a `sofilab.cmd` wrapper that runs `python sofilab.py` and place it on your PATH.

### Quick Setup (Recommended)

1. Clone the repository:

   ```bash
   git clone https://github.com/arafatx/sofilab.git
   cd sofilab
   ```

2. Install Python dependencies (auto-installs on first run too):

   ```bash
   pip install -r requirements.txt
   ```

3. Create your configuration:

   ```bash
   cp sofilab.conf.sample sofilab.conf
   nano sofilab.conf  # Edit with your server details
   ```

4. Install the CLI globally (macOS/Linux):

   ```bash
   python sofilab.py install
   ```

5. Verify installation:

   ```bash
   sofilab --version
   ```

Now you can use `sofilab` from anywhere!

### Manual Usage (without installing)

If you prefer not to install globally, you can run directly with Python:

```bash
python sofilab.py login pmx
```

## Quick Start

1. **Create your configuration from the sample:**

   ```bash
   cp sofilab.conf.sample sofilab.conf
   ```

2. **Edit the configuration with your server details:**

   ```bash
   nano sofilab.conf
   ```
   
   Example configuration:

   ```properties
   [pmx,pmx-home]
   host="192.168.1.100"
   user="root"
   password="your_password"
   port="22"
   keyfile="ssh/pmx_key"
   ```

3. **Connect to your servers:**

   ```bash
   # Connect to Proxmox
   sofilab login pmx
   
   # Connect to router
   sofilab login router
   ```

## Configuration

The `sofilab.conf` file uses a simple block format (no `scripts=` needed; scripts are now discovered from folders):

```properties
[alias1,alias2,alias3]
host="IP_ADDRESS"
user="USERNAME"
password="PASSWORD"           # Optional
port="SSH_PORT"              # Optional, defaults to 22
keyfile="ssh/alias_key"      # Optional
```

### Script Layout (new)

You can run local scripts on remote hosts in two ways:

1) Single scripts under `scripts/main/` (recommended for ad‑hoc):

```text
scripts/main/update.sh
scripts/main/tools/net/ping-check.sh
```

Run:

```bash
sofilab run-script --host-alias pmx update.sh
sofilab run-script --host-alias pmx tools/net/ping-check.sh -- --count 5
```

2) Ordered sets under `scripts/sets/<name>/` (priority by number):

```text
scripts/sets/proxmox/
  10_update.sh
  20_secure.sh
  30_setup-2fa.sh
  _env                     # optional KEY=VALUE for all scripts
  _args/20_secure.args     # optional per‑script args
```

Rules:
- Numbered scripts (e.g., `5_*.sh`, `10_*.sh`) run first, sorted by numeric prefix (1+ digits)
- Unnumbered scripts run afterwards, sorted alphabetically

Run:

```bash
# Preview order and args
sofilab run-scripts --host-alias pmx --set proxmox --dry-run -- --flag1 A --flag2 "B C"

# Execute with common args applied to each
sofilab run-scripts --host-alias pmx --set proxmox -- --flag1 A --flag2 "B C"
```

Execution model and interpreters:

- SofiLab uploads scripts to `~/.sofilab_scripts/` on the remote and removes them after execution.
- Scripts execute on the remote host; your local OS does not affect script execution.
- By default SofiLab invokes a POSIX shell (sh/bash) to run scripts. Use portable `.sh` for maximum compatibility (BusyBox, Bash, Debian/Alpine, Proxmox).
- If you want non‑shell steps (Python/Node/etc.), ensure the interpreter exists on the remote and call it from a shell wrapper, for example:

  ```sh
  #!/usr/bin/env sh
  exec python3 my_step.py "$@"
  ```

During execution, SofiLab sets useful environment variables:

- `SSH_PORT`: configured SSH port for the host
- `ACTUAL_PORT`: effective port used (after auto‑detection)
- `ADMIN_USER`: remote username
- `SSH_KEY_PATH`: path to private key (without `.pub`) if used
- `SSH_PUBLIC_KEY`: public key contents if available

### Authentication Priority

1. SSH key (if `keyfile` specified or `ssh/<alias>_key` exists)
2. Password (if specified in config)
3. Direct SSH (uses SSH agent or default keys)

## Usage

```bash
# Show help and available commands
sofilab --help

# Show version information
sofilab --version

# Connect using any configured alias
sofilab login pmx-home
sofilab login router
sofilab login rt

# Reboot a server by alias (optional wait)
sofilab reboot pmx           # issue reboot and exit
sofilab reboot pmx --wait    # wait up to 180s by default
sofilab reboot pmx --wait 300  # custom timeout seconds

# Copy files (scp-like; preferred)
# Remote paths use alias:/path
sofilab cp pmx:/var/log/syslog ./logs
sofilab cp -r pmx:/etc/nginx ./backups
sofilab cp ./notes.txt pmx:~/uploads
sofilab cp -r ./mydir pmx:~/projects

# List files on remote host (SFTP)
sofilab ls-remote pmx ~
```

### CLI Quick Reference

- Flexible alias options: you can use positionals or named flags anywhere.
  - `--host-alias pmx` or `--hostname pmx` work with all host commands.

- Login/status examples:
  - `sofilab login pmx`
  - `sofilab login --hostname pmx`
  - `sofilab status --host-alias pmx`

- Run one script (with args):
  - `sofilab run-script --host-alias pmx update.sh`
  - `sofilab run-script --host-alias pmx tools/net/ping-check.sh -- --count 3`

- Run an ordered set (same args applied to each):
  - `sofilab run-scripts --host-alias pmx --set proxmox`
  - `sofilab run-scripts --host-alias pmx --set proxmox -- --flag value`

- TTY control (place before `--` if you use it):
  - `--tty` or `--no-tty` with any command that executes scripts.

- Preview what will run (shows args from config):
  - `sofilab list-scripts pmx`

Tips:

- Use quotes in `sofilab.conf` for arguments with spaces (parsed shell‑style).
- CLI `--` stops option parsing; anything after goes to the script(s).
- If a router lacks SFTP (e.g., BusyBox/Dropbear), SofiLab falls back to a shell upload automatically.
- Logs live under `logs/`. Tail recent output:
  - `sofilab logs main 100`, `sofilab logs remote 200`, `sofilab clear-logs remote`.

### Typical Workflow

- Add a host to `sofilab.conf`
- Place single scripts under `scripts/main/`
- Or create a set under `scripts/sets/<name>/` with numbered scripts
- Preview: `sofilab run-scripts --host-alias <alias> --set <name> --dry-run`
- Execute: `sofilab run-scripts --host-alias <alias> --set <name>`

### Troubleshooting

- Host key mismatch: `sofilab reset-hostkey <alias>` then retry.
- PATH/wrapper issues (Windows): `sofilab doctor --repair-path`.
- SSH authentication: ensure key path in `keyfile` is correct and readable. If a matching `ssh/<alias>_key` exists, it’s auto‑used.

## Project Structure

```text
sofilab/
├── README.md
├── sofilab.py
├── sofilab.conf.sample
├── scripts/
│   ├── main/
│   │   ├── update.sh
│   │   └── tools/net/ping-check.sh
│   ├── sets/
│   │   └── proxmox/
│   │       ├── 10_update.sh
│   │       ├── 20_secure.sh
│   │       ├── 30_setup-2fa.sh
│   │       ├── _env
│   │       └── _args/20_secure.args
│   └── hooks/
│       ├── login/scripts/main.py
│       ├── status/scripts/main.py
│       └── reboot/scripts/main.py
└── ssh/
    ├── pmx_key
    └── pmx_key.pub
```

**Note:** The `ssh/` directory and `sofilab.conf` are excluded from git for security.

## Setup Commands

Use the Python CLI:

```bash
# Install sofilab globally (macOS/Linux)
python sofilab.py install

# Uninstall
python sofilab.py uninstall
```

## Security Notes

- SSH keys are stored in the `ssh/` directory
- Passwords in `sofilab.conf` should be secured appropriately
- The script supports SSH agent for additional security
- Consider using SSH key authentication over passwords when possible

## Adding New Servers

To add a new server configuration:

1. Edit `sofilab.conf`
2. Add a new block with your preferred aliases:

   ```properties
   [myserver,srv]
   host="192.168.1.100"
   user="admin"
   password="your_password"  # Optional
   port="22"                 # Optional
   keyfile="ssh/myserver_key" # Optional
   ```

3. Generate SSH keys if using key authentication:

   ```bash
   ssh-keygen -t rsa -b 4096 -f ssh/myserver_key
   ssh-copy-id -i ssh/myserver_key.pub user@192.168.1.100
   ```

## Requirements

- Python 3.8+
- Paramiko (auto‑installed by SofiLab when needed)
- SSH server on the remote hosts

<!-- Roadmap removed intentionally -->
