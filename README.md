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

The `sofilab.conf` file uses a simple block format:

```properties
[alias1,alias2,alias3]
host="IP_ADDRESS"
user="USERNAME"
password="PASSWORD"           # Optional
port="SSH_PORT"              # Optional, defaults to 22
keyfile="ssh/alias_key"      # Optional
```

### Scripts & Arguments

You can run local scripts on remote hosts via `run-script` (one script) or `run-scripts` (all scripts listed for a host). Arguments can be configured in `sofilab.conf` so you don’t need to pass them on the CLI each time.

- Inline in `scripts=`: add args after each script name (parsed shell‑style).
- Per‑script keys: use `script_args.<script>` for long or complex args.
- Default for all: use `default_script_args` when no per‑script args.

Examples inside a server block:

```properties
[pmx]
host="192.168.50.136"
user="root"
keyfile="ssh/pmx_key"

# Inline args (easy):
scripts="testarg1.sh --name alpha, testarg2.sh 'hello world' 42, testarg3.sh --flag A --path '/etc'"

# Or explicit keys (more verbose):
# scripts="testarg1.sh, testarg2.sh, testarg3.sh"
# default_script_args="--non-interactive"
# script_args.testarg1.sh="--name alpha"
# script_args.testarg2.sh="'hello world' 42"
# script_args.testarg3.sh="--flag A --path /etc"
```

Precedence when running scripts:

- CLI args override config (`--script-args ...` or `-- ...`).
- Then `script_args.<script>`.
- Then inline args inside `scripts=`.
- Then `default_script_args`.

Your scripts are searched under `scripts/` and uploaded to the remote user’s home at `~/.sofilab_scripts/` for execution, then removed. SofiLab sets useful environment variables for scripts:

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
  - Positionals + end‑of‑options: `sofilab run-script pmx my.sh -- a "b c" 2`
  - Named options: `sofilab run-script --host-alias pmx --script my.sh --script-args a "b c" 2`

- Run all configured scripts (same args applied to each):
  - `sofilab run-scripts pmx --script-args abc "b c" 123`
  - `sofilab run-scripts --hostname pmx -- --abc "b c" 123`

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

- Add a host to `sofilab.conf` with `scripts=` entries.
- Place your `.sh` files in `scripts/` and make them executable.
- Verify: `sofilab list-scripts <alias>` and `sofilab status <alias>`.
- Run all: `sofilab run-scripts <alias>`.
- Iterate: adjust per‑script args inline or with `script_args.<script>` keys.

### Troubleshooting

- Host key mismatch: `sofilab reset-hostkey <alias>` then retry.
- PATH/wrapper issues (Windows): `sofilab doctor --repair-path`.
- SSH authentication: ensure key path in `keyfile` is correct and readable. If a matching `ssh/<alias>_key` exists, it’s auto‑used.

## Project Structure

```text
sofilab/
├── README.md             # This documentation
├── sofilab.py            # Main server management script (Python)
├── sofilab.conf.sample   # Sample configuration file
├── TODO.md               # Development notes
├── .gitignore            # Git ignore rules
└── ssh/                  # SSH key storage (excluded from git)
    ├── pmx_key           # Example: Proxmox private key
    ├── pmx_key.pub       # Example: Proxmox public key
    └── ...               # Your SSH keys
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

- macOS or Linux
- `bash` shell
- `sshpass` tool for password authentication (optional but recommended)
  - Install on macOS: `brew install sshpass`
  - Install on Linux: `apt-get install sshpass` or `yum install sshpass`

## Roadmap

### Phase 1: SSH Management ✅

- [x] Multi-server SSH connections
- [x] Alias-based configuration
- [x] SSH key and password authentication
- [x] Global installation system

### Phase 2: Server Monitoring (In Progress)

- [ ] Real-time system monitoring (CPU, RAM, disk)
- [ ] Service status monitoring
- [ ] Network connectivity checks
- [ ] Performance metrics collection

### Phase 3: Installation Management (Planned)

- [ ] Package installation automation
- [ ] Configuration management
- [ ] Service deployment
- [ ] Update management

### Phase 4: Advanced Administration (Future)

- [ ] User management tools
- [ ] Security auditing
- [ ] Backup automation
- [ ] Log analysis tools

## Contributing

SofiLab is designed to be simple and extensible. Contributions are welcome!

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see LICENSE file for details.

## Author

**Arafat Ali**  
Email: [arafat@sofibox.com](mailto:arafat@sofibox.com)  
GitHub: [@arafatx](https://github.com/arafatx)

---

**SofiLab** - Simplifying server management since 2025
