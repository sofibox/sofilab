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
```

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
