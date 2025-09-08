#!/usr/bin/env bash
#
# sofilab.sh - Server Management and Administration Tool
# Provides SSH connections, server monitoring, and installation management
#
# Author: Arafat Ali <arafat@sofibox.com>
# Repository: https://github.com/arafatx/sofilab
#

set -Eeuo pipefail

# Version information
VERSION="1.0.0"
BUILD_DATE="2025-09-05"

# Global variables
# Resolve symlink to get the real script directory
if [[ -L "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$(readlink "${BASH_SOURCE[0]}")")" && pwd)"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
CONFIG_FILE="$SCRIPT_DIR/sofilab.conf"

# Default logging configuration (will be overridden by config file)
LOG_DIR="$SCRIPT_DIR/logs"
LOG_LEVEL="INFO"
ENABLE_LOGGING="true"
MAX_LOG_SIZE="10M"
MAX_LOG_FILES=5
SCRIPT_EXIT_ON_ERROR="true"  # Default to exit remote scripts on error for safety

# Initialize log file variables (will be set by init_logging)
MAIN_LOG=""
ERROR_LOG=""
REMOTE_LOG=""

# Load global configuration from sofilab.conf
load_global_config() {
    local config_file="$1"
    local in_global_section=false
    local config_errors=()
    
    # Check if config file exists
    if [[ ! -f "$config_file" ]]; then
        echo "❌ Configuration file not found: $config_file" >&2
        return 1
    fi
    
    # Read and validate configuration
    while IFS= read -r line; do
        # Remove leading/trailing whitespace
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^# ]] && continue
        
        # Check for [global] section
        if [[ "$line" =~ ^\[global\]$ ]]; then
            in_global_section=true
            continue
        elif [[ "$line" =~ ^\[.*\]$ ]]; then
            # Another section started, exit global section
            in_global_section=false
            continue
        fi
        
        # Parse global configuration
        if [[ "$in_global_section" == true && "$line" =~ ^([^=]+)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local value="${BASH_REMATCH[2]}"
            
            # Remove quotes from value
            value="${value#\"}"
            value="${value%\"}"
            
            # Validate and set configuration values
            case "$key" in
                log_dir)
                    if [[ -n "$value" ]]; then
                        # Support both absolute and relative paths
                        if [[ "$value" = /* ]]; then
                            LOG_DIR="$value"
                        else
                            LOG_DIR="$SCRIPT_DIR/$value"
                        fi
                    else
                        config_errors+=("log_dir cannot be empty")
                    fi
                    ;;
                log_level)
                    if [[ "$value" =~ ^(DEBUG|INFO|WARN|ERROR)$ ]]; then
                        LOG_LEVEL="$value"
                    else
                        config_errors+=("Invalid log_level: $value (must be DEBUG, INFO, WARN, or ERROR)")
                    fi
                    ;;
                enable_logging)
                    if [[ "$value" =~ ^(true|false)$ ]]; then
                        ENABLE_LOGGING="$value"
                    else
                        config_errors+=("Invalid enable_logging: $value (must be true or false)")
                    fi
                    ;;
                max_log_size)
                    if [[ "$value" =~ ^[0-9]+[KMG]?$ ]]; then
                        MAX_LOG_SIZE="$value"
                    else
                        config_errors+=("Invalid max_log_size: $value (format: number with optional K/M/G suffix)")
                    fi
                    ;;
                max_log_files)
                    if [[ "$value" =~ ^[0-9]+$ ]] && [[ "$value" -gt 0 ]]; then
                        MAX_LOG_FILES="$value"
                    else
                        config_errors+=("Invalid max_log_files: $value (must be a positive number)")
                    fi
                    ;;
                script_exit_on_error)
                    if [[ "$value" =~ ^(true|false)$ ]]; then
                        SCRIPT_EXIT_ON_ERROR="$value"
                    else
                        config_errors+=("Invalid script_exit_on_error: $value (must be true or false)")
                    fi
                    ;;
                *)
                    # Unknown key in global section - just warn, don't fail
                    warn "Unknown global configuration key: $key"
                    ;;
            esac
        fi
    done < "$config_file"
    
    # Report any configuration errors
    if [[ ${#config_errors[@]} -gt 0 ]]; then
        echo "❌ Configuration validation errors found:" >&2
        for err in "${config_errors[@]}"; do
            echo "❌   - $err" >&2
        done
        return 1
    fi
    
    return 0
}

# Validate configuration file syntax
validate_config_syntax() {
    local config_file="$1"
    local line_num=0
    local current_section=""
    local syntax_errors=()
    local valid_server_keys="host user password port keyfile scripts"
    local valid_global_keys="log_dir log_level enable_logging max_log_size max_log_files script_exit_on_error"
    
    while IFS= read -r line; do
        ((line_num++))
        
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        
        # Remove leading/trailing whitespace for checking
        local trimmed_line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        
        # Check for section headers
        if [[ "$trimmed_line" =~ ^\[([^]]+)\]$ ]]; then
            current_section="${BASH_REMATCH[1]}"
            # Validate section name format (allow spaces around commas)
            if [[ ! "$current_section" =~ ^[a-zA-Z0-9,._[:space:]-]+$ ]]; then
                syntax_errors+=("Line $line_num: Invalid section name format: [$current_section]")
            fi
            continue
        fi
        
        # Check for key=value pairs
        if [[ "$trimmed_line" =~ ^([^=]+)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local value="${BASH_REMATCH[2]}"
            
            # Check if we're in a section
            if [[ -z "$current_section" ]]; then
                syntax_errors+=("Line $line_num: Key-value pair found outside of any section: $trimmed_line")
                continue
            fi
            
            # Validate keys based on section type
            if [[ "$current_section" == "global" ]]; then
                if [[ ! " $valid_global_keys " =~ " $key " ]]; then
                    syntax_errors+=("Line $line_num: Unknown key '$key' in [global] section")
                fi
            else
                # Server section
                if [[ ! " $valid_server_keys " =~ " $key " ]]; then
                    syntax_errors+=("Line $line_num: Unknown key '$key' in server section [$current_section]")
                fi
            fi
            
            # Check for proper quote usage
            if [[ "$value" =~ ^\".*[^\"]$ ]] || [[ "$value" =~ ^[^\"].*\"$ ]]; then
                syntax_errors+=("Line $line_num: Mismatched quotes in value: $value")
            fi
        else
            # Line doesn't match expected format
            syntax_errors+=("Line $line_num: Invalid syntax (expected key=\"value\" or [section]): $trimmed_line")
        fi
    done < "$config_file"
    
    # Report syntax errors
    if [[ ${#syntax_errors[@]} -gt 0 ]]; then
        echo "❌ Configuration syntax errors found in $config_file:" >&2
        for err in "${syntax_errors[@]}"; do
            echo "❌   $err" >&2
        done
        return 1
    fi
    
    return 0
}

# Initialize logging
init_logging() {
    [[ "$ENABLE_LOGGING" != "true" ]] && return 0
    
    # Create logs directory if it doesn't exist
    mkdir -p "$LOG_DIR" 2>/dev/null || return 1
    
    # Set log file paths
    MAIN_LOG="$LOG_DIR/sofilab.log"
    ERROR_LOG="$LOG_DIR/sofilab-error.log"
    REMOTE_LOG="$LOG_DIR/sofilab-remote.log"
    
    # Rotate logs if they're too large
    rotate_log_if_needed "$MAIN_LOG"
    rotate_log_if_needed "$ERROR_LOG"
    rotate_log_if_needed "$REMOTE_LOG"
    
    return 0
}

# Rotate log file if it exceeds maximum size
rotate_log_if_needed() {
    local log_file="$1"
    [[ ! -f "$log_file" ]] && return 0
    
    # Check if log file exceeds maximum size (convert to bytes for comparison)
    local max_bytes
    case "$MAX_LOG_SIZE" in
        *M|*m) max_bytes=$((${MAX_LOG_SIZE%[Mm]} * 1024 * 1024)) ;;
        *K|*k) max_bytes=$((${MAX_LOG_SIZE%[Kk]} * 1024)) ;;
        *G|*g) max_bytes=$((${MAX_LOG_SIZE%[Gg]} * 1024 * 1024 * 1024)) ;;
        *) max_bytes="$MAX_LOG_SIZE" ;;
    esac
    
    local current_size=$(stat -f%z "$log_file" 2>/dev/null || stat -c%s "$log_file" 2>/dev/null || echo 0)
    
    if [[ "$current_size" -gt "$max_bytes" ]]; then
        # Rotate existing logs
        for ((i=MAX_LOG_FILES-1; i>=1; i--)); do
            [[ -f "$log_file.$i" ]] && mv "$log_file.$i" "$log_file.$((i+1))"
        done
        
        # Move current log to .1
        mv "$log_file" "$log_file.1"
        
        # Clean up old logs beyond MAX_LOG_FILES
        local cleanup_index=$((MAX_LOG_FILES + 1))
        [[ -f "$log_file.$cleanup_index" ]] && rm -f "$log_file.$cleanup_index"
    fi
}

# Enhanced logging functions with file output
log_message() {
    local level="$1"
    local message="$2"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local log_entry="[$timestamp] [$level] $message"
    
    # Always log to main log file if logging is enabled
    if [[ "$ENABLE_LOGGING" == "true" ]] && [[ -n "$MAIN_LOG" ]]; then
        echo "$log_entry" >> "$MAIN_LOG" 2>/dev/null
    fi
    
    # Log errors to separate error log
    if [[ "$level" == "ERROR" ]] && [[ "$ENABLE_LOGGING" == "true" ]] && [[ -n "$ERROR_LOG" ]]; then
        echo "$log_entry" >> "$ERROR_LOG" 2>/dev/null
    fi
}

# Check if we should log based on level
should_log() {
    local level="$1"
    case "$LOG_LEVEL" in
        DEBUG) return 0 ;;
        INFO) [[ "$level" != "DEBUG" ]] && return 0 ;;
        WARN) [[ "$level" == "WARN" || "$level" == "ERROR" ]] && return 0 ;;
        ERROR) [[ "$level" == "ERROR" ]] && return 0 ;;
    esac
    return 1
}

# Enhanced logging functions
info() { 
    echo "💡 $*" >&2
    should_log "INFO" && log_message "INFO" "$*"
}

error() { 
    echo "❌ $*" >&2
    should_log "ERROR" && log_message "ERROR" "$*"
}

warn() { 
    echo "⚠️  $*" >&2
    should_log "WARN" && log_message "WARN" "$*"
}

success() { 
    echo "✅ $*" >&2
    should_log "INFO" && log_message "INFO" "SUCCESS: $*"
}

progress() { 
    echo "🔄 $*" >&2
    should_log "INFO" && log_message "INFO" "PROGRESS: $*"
}

debug() {
    should_log "DEBUG" && {
        echo "🐛 $*" >&2
        log_message "DEBUG" "$*"
    }
}

# Log remote script output
log_remote_output() {
    local alias="$1"
    local script_name="$2"
    local line="$3"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    if [[ "$ENABLE_LOGGING" == "true" ]] && [[ -n "$REMOTE_LOG" ]]; then
        echo "[$timestamp] [$alias] [$script_name] $line" >> "$REMOTE_LOG" 2>/dev/null
    fi
}

# Show usage
usage() {
    cat << EOF
Usage: $SCRIPT_NAME <command> [host-alias] [options]

Commands:
  login <host-alias>               Connect to configured host using SSH alias
  reset-hostkey <host-alias>       Remove stored SSH host key (for reinstalled servers)
  run-scripts <host-alias>         Run all scripts defined for host-alias in order
  run-script <host-alias> <script> Run a specific script on remote server
  list-scripts <host-alias>        List available scripts for a host-alias
  logs [type] [lines]         Show logs (type: main|error|remote, default: main, lines: default 50)
  clear-logs [type]           Clear logs (type: main|error|remote|all, default: all)
  install                     Install sofilab command globally (requires sudo)
  uninstall                   Uninstall sofilab command (requires sudo)
  --version, -V               Show version information
  --help, -h                  Show this help message

Examples:
  $SCRIPT_NAME login pmx
  $SCRIPT_NAME reset-hostkey pmx    # Use after server reinstall
  $SCRIPT_NAME run-scripts pmx
  $SCRIPT_NAME run-script pmx pmx-update-server.sh
  $SCRIPT_NAME list-scripts pmx
  $SCRIPT_NAME logs                 # Show last 50 lines of main log
  $SCRIPT_NAME logs remote 100     # Show last 100 lines of remote script log
  $SCRIPT_NAME logs error          # Show error log
  $SCRIPT_NAME clear-logs           # Clear all logs
  $SCRIPT_NAME clear-logs remote    # Clear only remote script log
  $SCRIPT_NAME install

Configuration format in sofilab.conf:
  Global Configuration:
  [global]
  log_dir="logs"                    # Directory for log files
  log_level="INFO"                  # Logging level: DEBUG, INFO, WARN, ERROR
  enable_logging="true"             # Enable/disable logging: true or false
  max_log_size="10M"               # Maximum log file size before rotation
  max_log_files="5"                # Number of rotated log files to keep
  script_exit_on_error="true"      # Exit remote scripts on first error: true or false

  Server Configuration:
  [host-alias1,host-alias2]
  host="IP_ADDRESS"
  user="USERNAME"
  password="PASSWORD"
  port="SSH_PORT" (optional, default 22)
  keyfile="ssh/host-alias_key" (optional)
  scripts="script1.sh,script2.sh" (optional, comma-separated)

Logging Configuration:
  Logs are stored in: $SCRIPT_DIR/logs/
  - sofilab.log         (main log with all operations)
  - sofilab-error.log   (errors only)
  - sofilab-remote.log  (remote script outputs)

EOF
}

# Load and parse server configuration for given alias
get_server_config() {
    local alias="$1"
    local in_section=false
    local host="" user="" password="" port="" keyfile="" scripts=""
    
    [[ ! -f "$CONFIG_FILE" ]] && { error "Config file not found: $CONFIG_FILE"; exit 1; }
    
    while IFS= read -r line; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [[ -z "$line" || "$line" =~ ^# ]] && continue
        
        # Check for section header [alias1,alias2,...]
        if [[ "$line" =~ ^\[([^]]+)\]$ ]]; then
            in_section=false
            IFS=',' read -ra aliases <<< "${BASH_REMATCH[1]}"
            for a in "${aliases[@]}"; do
                a=$(echo "$a" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                [[ "$a" == "$alias" ]] && { in_section=true; break; }
            done
            continue
        fi
        
        # Parse key=value pairs in current section
        if [[ "$in_section" == true && "$line" =~ ^([^=]+)=(.*)$ ]]; then
            local key="${BASH_REMATCH[1]}"
            local value="${BASH_REMATCH[2]}"
            value="${value#\"}"  # Remove quotes
            value="${value%\"}"
            
            case "$key" in
                host) host="$value" ;;
                user) user="$value" ;;
                password) password="$value" ;;
                port) port="$value" ;;
                keyfile) keyfile="$value" ;;
                scripts) scripts="$value" ;;
            esac
        fi
    done < "$CONFIG_FILE"
    
    # Export parsed values
    SERVER_HOST="$host"
    SERVER_USER="$user"
    SERVER_PASSWORD="$password"
    SERVER_PORT="${port:-22}"
    SERVER_KEYFILE="$keyfile"
    SERVER_SCRIPTS="$scripts"
}

# Check if a port is open without authentication (won't trigger firewall rules)
check_port_open() {
    local host="$1"
    local port="$2"
    local timeout=3
    
    # Try using nc (netcat) if available
    if command -v nc >/dev/null 2>&1; then
        # Different nc versions have different syntax
        if nc -h 2>&1 | grep -q "GNU netcat"; then
            # GNU netcat
            nc -z -w "$timeout" "$host" "$port" >/dev/null 2>&1
        else
            # BSD/macOS netcat
            nc -z -w "$timeout" "$host" "$port" >/dev/null 2>&1
        fi
        return $?
    fi
    
    # Fallback to bash's /dev/tcp if nc not available
    if [[ -n "$BASH_VERSION" ]]; then
        timeout "$timeout" bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null
        return $?
    fi
    
    # Last resort: use telnet if available
    if command -v telnet >/dev/null 2>&1; then
        (echo quit | timeout "$timeout" telnet "$host" "$port" 2>/dev/null | grep -q "Connected") 2>/dev/null
        return $?
    fi
    
    # If no tools available, return success to proceed with SSH attempt
    return 0
}

# Test SSH connectivity quickly
test_ssh_connection() {
    local port="$1"
    local keyfile="$2"
    
    # First check if there's a host key mismatch issue
    local ssh_output
    ssh_output=$(ssh -p "$port" -o ConnectTimeout=5 -o BatchMode=yes "$SERVER_USER@$SERVER_HOST" "echo connected" 2>&1)
    
    if echo "$ssh_output" | grep -q "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED\|Host key verification failed"; then
        # Host key has changed - likely a fresh install
        warn "Host key has changed - this is expected for a fresh installation"
        info "Automatically removing old host key..."
        
        # Remove old host keys
        ssh-keygen -R "$SERVER_HOST" 2>/dev/null
        ssh-keygen -R "[$SERVER_HOST]:$port" 2>/dev/null
        
        info "Old host key removed. Retrying connection..."
    fi
    
    # Try SSH key first if available
    if [[ -n "$keyfile" && -f "$keyfile" ]]; then
        if ssh -i "$keyfile" -p "$port" -o StrictHostKeyChecking=accept-new -o PasswordAuthentication=no -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "echo connected" >/dev/null 2>&1; then
            return 0  # SSH key worked
        fi
    fi
    
    # Try password if SSH key failed or not available
    if [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
        if sshpass -p "$SERVER_PASSWORD" ssh -p "$port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "echo connected" >/dev/null 2>&1; then
            return 0  # Password worked
        fi
    fi
    
    # Try direct SSH as last resort
    ssh -p "$port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "echo connected" >/dev/null 2>&1
}

# Connect to server interactively
connect_ssh() {
    local port="$1"
    local keyfile="$2"
    
    # Try SSH key first if available
    if [[ -n "$keyfile" && -f "$keyfile" ]]; then
        if ssh -i "$keyfile" -p "$port" -o StrictHostKeyChecking=accept-new -o PasswordAuthentication=no -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "echo connected" >/dev/null 2>&1; then
            ssh -i "$keyfile" -p "$port" -o StrictHostKeyChecking=accept-new "$SERVER_USER@$SERVER_HOST"
            return
        fi
    fi
    
    # Try password if SSH key failed or not available
    if [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
        sshpass -p "$SERVER_PASSWORD" ssh -p "$port" -o StrictHostKeyChecking=accept-new "$SERVER_USER@$SERVER_HOST"
        return
    fi
    
    # Direct SSH as last resort
    ssh -p "$port" -o StrictHostKeyChecking=accept-new "$SERVER_USER@$SERVER_HOST"
}

# Main SSH login function
ssh_login() {
    local alias="$1"
    
    info "Loading configuration: $CONFIG_FILE"
    get_server_config "$alias"
    
    [[ -z "$SERVER_HOST" ]] && { error "Unknown host-alias: $alias"; exit 1; }
    
    info "Connecting to $SERVER_HOST as $SERVER_USER"
    log_message "INFO" "SSH login attempt - alias: $alias, host: $SERVER_HOST, user: $SERVER_USER"
    
    # Determine SSH key
    local keyfile=""
    if [[ -n "$SERVER_KEYFILE" ]]; then
        keyfile="$SCRIPT_DIR/$SERVER_KEYFILE"
        [[ -f "$keyfile" ]] && info "Using SSH key: $keyfile" || keyfile=""
    fi
    
    # Auto-detect key if not specified
    if [[ -z "$keyfile" ]]; then
        local auto_key="$SCRIPT_DIR/ssh/${alias}_key"
        [[ -f "$auto_key" ]] && { keyfile="$auto_key"; info "Using auto-detected SSH key: $keyfile"; }
    fi
    
    # Determine which port to use by checking port availability first
    local use_port=$(determine_ssh_port "$SERVER_PORT" "$SERVER_HOST")
    [[ -z "$use_port" ]] && exit 1
    
    # Now attempt SSH connection on the verified open port
    info "Attempting SSH connection on port $use_port..."
    if test_ssh_connection "$use_port" "$keyfile"; then
        info "Authentication successful"
        log_message "INFO" "SSH authentication successful for $alias ($SERVER_HOST:$use_port)"
        connect_ssh "$use_port" "$keyfile"
        info "Disconnected from $SERVER_HOST"
        log_message "INFO" "SSH session ended for $alias ($SERVER_HOST:$use_port)"
        return 0
    else
        error "Authentication failed on port $use_port"
        error "Please check your credentials or SSH key"
        log_message "ERROR" "SSH authentication failed for $alias ($SERVER_HOST:$use_port)"
        exit 1
    fi
}

# Show version information
show_version() {
    echo "SofiLab Server Management Tool"
    echo "Version: $VERSION"
    echo "Build Date: $BUILD_DATE"
    echo "Author: Arafat Ali <arafat@sofibox.com>"
    echo "Repository: https://github.com/arafatx/sofilab"
    echo ""
    echo "Features: SSH connections, server monitoring, installation management"
}

# Install sofilab command globally
install_sofilab() {
    local install_dir="/usr/local/bin"
    local install_name="sofilab"
    local script_path="$SCRIPT_DIR/$SCRIPT_NAME"
    local symlink_path="$install_dir/$install_name"
    
    info "Installing sofilab command globally..."
    
    # Check if script exists
    if [[ ! -f "$script_path" ]]; then
        error "Script not found: $script_path"
        exit 1
    fi
    
    # Make script executable
    chmod +x "$script_path" || { error "Failed to make script executable"; exit 1; }
    info "Made script executable: $script_path"
    
    # Check if /usr/local/bin exists, create if needed
    if [[ ! -d "$install_dir" ]]; then
        info "Creating $install_dir directory..."
        sudo mkdir -p "$install_dir" || { error "Failed to create $install_dir"; exit 1; }
    fi
    
    # Remove existing symlink if it exists
    if [[ -L "$symlink_path" ]]; then
        info "Removing existing symlink..."
        sudo rm "$symlink_path" || { error "Failed to remove existing symlink"; exit 1; }
    elif [[ -f "$symlink_path" ]]; then
        error "File already exists at $symlink_path and is not a symlink"
        error "Please remove it manually or choose a different installation method"
        exit 1
    fi
    
    # Create symlink
    info "Creating symlink: $symlink_path -> $script_path"
    sudo ln -s "$script_path" "$symlink_path" || { error "Failed to create symlink"; exit 1; }
    
    # Verify installation
    if command -v "$install_name" >/dev/null 2>&1; then
        info "✓ Installation successful!"
        info "You can now use 'sofilab' command from anywhere"
        info ""
        info "Try: sofilab --help"
    else
        warn "Installation completed but 'sofilab' command not found in PATH"
        warn "You may need to add $install_dir to your PATH"
        warn "Add this line to your ~/.bashrc or ~/.zshrc:"
        warn "  export PATH=\"$install_dir:\$PATH\""
    fi
}

# Uninstall sofilab command
uninstall_sofilab() {
    local install_dir="/usr/local/bin"
    local install_name="sofilab"
    local symlink_path="$install_dir/$install_name"
    
    info "Uninstalling sofilab command..."
    
    if [[ -L "$symlink_path" ]]; then
        # It's a symlink, safe to remove
        sudo rm "$symlink_path" || { error "Failed to remove symlink"; exit 1; }
        info "✓ Removed symlink: $symlink_path"
        info "Uninstallation successful!"
    elif [[ -f "$symlink_path" ]]; then
        # It's a regular file, be cautious
        error "Found regular file at $symlink_path (not a symlink)"
        error "Please verify and remove manually if needed"
        exit 1
    else
        warn "No installation found at $symlink_path"
        info "Nothing to uninstall"
    fi
}

# Determine the working SSH port with fallback
determine_ssh_port() {
    local configured_port="$1"
    local host="$2"
    
    # Check configured port first
    progress "Checking connection to $host:$configured_port..."
    if check_port_open "$host" "$configured_port"; then
        info "Port $configured_port is open $([ "$configured_port" == "22" ] && echo "(default SSH port)" || echo "(custom SSH port)")"
        echo "$configured_port"
        return 0
    elif [[ "$configured_port" != "22" ]]; then
        # Only try port 22 as fallback if configured port is different
        progress "Port $configured_port not accessible, trying fallback port 22..."
        if check_port_open "$host" "22"; then
            info "Port 22 is open (fallback to default SSH port)"
            echo "22"
            return 0
        else
            error "Neither port $configured_port nor port 22 are accessible"
            error "Please check your network connection and firewall settings"
            return 1
        fi
    else
        error "Port $configured_port is not accessible"
        error "Please check your network connection and firewall settings"
        return 1
    fi
}

# Get SSH key file for an alias
get_ssh_keyfile() {
    local alias="$1"
    local silent="${2:-false}"  # Optional parameter to suppress info messages
    local keyfile=""
    
    if [[ -n "$SERVER_KEYFILE" ]]; then
        keyfile="$SCRIPT_DIR/$SERVER_KEYFILE"
        if [[ -f "$keyfile" ]]; then
            [[ "$silent" != "true" ]] && info "Using SSH key: $keyfile"
            echo "$keyfile"
            return 0
        fi
    fi
    
    # Auto-detect key if not specified
    local auto_key="$SCRIPT_DIR/ssh/${alias}_key"
    if [[ -f "$auto_key" ]]; then
        [[ "$silent" != "true" ]] && info "Using auto-detected SSH key: $auto_key"
        echo "$auto_key"
        return 0
    fi
    
    # No key found
    echo ""
    return 1
}

# Upload a script file to remote server
upload_script() {
    local script_file="$1"
    local alias="$2"
    local use_port="$3"
    # Upload to user's home directory in a .sofilab_scripts folder
    local remote_dir=".sofilab_scripts"
    local remote_path="$remote_dir/$(basename "$script_file")"
    
    progress "Uploading $script_file to server..."
    log_message "INFO" "Starting script upload: $script_file to $alias ($SERVER_HOST:$use_port)"
    
    # Check if script exists locally
    if [[ ! -f "$SCRIPT_DIR/scripts/$script_file" ]]; then
        error "Script not found: $SCRIPT_DIR/scripts/$script_file"
        log_message "ERROR" "Script file not found locally: $SCRIPT_DIR/scripts/$script_file"
        return 1
    fi
    
    # Get SSH key (show info message)
    local keyfile=$(get_ssh_keyfile "$alias" "false")
    
    # Create remote directory first - try key, then password
    local mkdir_cmd="mkdir -p ~/$remote_dir"
    local mkdir_success=false
    
    debug "Creating remote directory: $remote_dir"
    
    if [[ -n "$keyfile" ]]; then
        # Try SSH key first
        if ssh -i "$keyfile" -p "$use_port" -o StrictHostKeyChecking=accept-new -o PasswordAuthentication=no -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$mkdir_cmd" 2>/dev/null; then
            mkdir_success=true
        elif [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
            # SSH key failed, add small delay to avoid triggering fail2ban
            sleep 2
            progress "SSH key failed, trying password authentication..."
            sshpass -p "$SERVER_PASSWORD" ssh -p "$use_port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$mkdir_cmd" 2>/dev/null && mkdir_success=true
        fi
    elif [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
        # No SSH key, use password directly
        sshpass -p "$SERVER_PASSWORD" ssh -p "$use_port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$mkdir_cmd" 2>/dev/null && mkdir_success=true
    else
        # Try without any authentication method (will prompt for password)
        ssh -p "$use_port" -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$mkdir_cmd" 2>/dev/null && mkdir_success=true
    fi
    
    # Upload using scp - try key first, then password
    local upload_success=false
    
    debug "Uploading script file via SCP"
    
    if [[ -n "$keyfile" ]]; then
        # Try SSH key first
        if scp -i "$keyfile" -P "$use_port" -o StrictHostKeyChecking=accept-new -o PasswordAuthentication=no -o ConnectTimeout=5 "$SCRIPT_DIR/scripts/$script_file" "$SERVER_USER@$SERVER_HOST:$remote_path" 2>/dev/null; then
            upload_success=true
        elif [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
            # SSH key failed, add small delay to avoid triggering fail2ban
            sleep 2
            progress "SSH key failed, using password authentication..."
            sshpass -p "$SERVER_PASSWORD" scp -P "$use_port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SCRIPT_DIR/scripts/$script_file" "$SERVER_USER@$SERVER_HOST:$remote_path" 2>/dev/null && upload_success=true
        fi
    elif [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
        # No SSH key, use password directly
        sshpass -p "$SERVER_PASSWORD" scp -P "$use_port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SCRIPT_DIR/scripts/$script_file" "$SERVER_USER@$SERVER_HOST:$remote_path" 2>/dev/null && upload_success=true
    else
        # Try without any authentication method (will prompt for password)
        scp -P "$use_port" -o ConnectTimeout=5 "$SCRIPT_DIR/scripts/$script_file" "$SERVER_USER@$SERVER_HOST:$remote_path" 2>/dev/null && upload_success=true
    fi
    
    if [[ "$upload_success" == true ]]; then
        log_message "INFO" "Script upload successful: $script_file to $alias"
        return 0
    else
        log_message "ERROR" "Script upload failed: $script_file to $alias"
        return 1
    fi
}

# Execute a script on remote server
execute_remote_script() {
    local script_file="$1"
    local alias="$2"
    local use_port="$3"
    # Script is in user's home directory
    local remote_dir=".sofilab_scripts"
    local remote_path="$remote_dir/$(basename "$script_file")"
    
    # Get SSH key (silent mode to avoid duplicate messages)
    local keyfile=$(get_ssh_keyfile "$alias" "true")
    
    # Prepare environment variables for the script
    # Pass both configured port and actual connection port, plus SSH key content
    local ssh_key_path=""
    local ssh_public_key=""
    if [[ -n "$keyfile" ]]; then
        # Remove .pub extension if present to get base key path
        ssh_key_path="${keyfile%.pub}"
        # Read the actual public key content if it exists
        if [[ -f "${ssh_key_path}.pub" ]]; then
            ssh_public_key="$(cat "${ssh_key_path}.pub")"
            info "Including SSH public key for automatic setup"
        fi
    fi
    local env_vars="SSH_PORT='$SERVER_PORT' ACTUAL_PORT='$use_port' ADMIN_USER='$SERVER_USER' SSH_KEY_PATH='$ssh_key_path' SSH_PUBLIC_KEY='$ssh_public_key'"
    
    # Execute script remotely (using full path from home directory)
    # Apply script_exit_on_error setting if enabled, store the exit code and clean up regardless of success/failure
    local bash_opts=""
    if [[ "$SCRIPT_EXIT_ON_ERROR" == "true" ]]; then
        bash_opts="-e"  # Exit script on first error
    fi
    local ssh_cmd="cd ~ && chmod +x $remote_path && $env_vars bash $bash_opts $remote_path; script_exit_code=\$?; rm -f $remote_path; exit \$script_exit_code"
    
    local exec_success=false
    
    # Function to prefix remote output and log it
    prefix_and_log_remote_output() {
        while IFS= read -r line; do
            echo "[REMOTE] $line"
            log_remote_output "$alias" "$script_file" "$line"
        done
    }
    
    debug "Executing remote script: $script_file on $alias ($SERVER_HOST:$use_port)"
    log_message "INFO" "Starting remote script execution: $script_file on $alias ($SERVER_HOST:$use_port)"
    
    # Use PIPESTATUS to capture the exit code of the SSH command, not the pipe
    # We need to distinguish between authentication failures (255) and script execution failures (other non-zero codes)
    local ssh_exit_code=0
    local auth_attempted=false
    
    if [[ -n "$keyfile" ]]; then
        # Try SSH key first
        ssh -i "$keyfile" -p "$use_port" -o StrictHostKeyChecking=accept-new -o PasswordAuthentication=no -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$ssh_cmd" 2>&1 | prefix_and_log_remote_output
        ssh_exit_code=${PIPESTATUS[0]}
        auth_attempted=true
        
        if [[ $ssh_exit_code -eq 0 ]]; then
            exec_success=true
        elif [[ $ssh_exit_code -eq 255 ]] && [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
            # SSH authentication failed (exit code 255), try password
            sleep 2
            progress "SSH key failed, using password authentication..."
            sshpass -p "$SERVER_PASSWORD" ssh -p "$use_port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$ssh_cmd" 2>&1 | prefix_and_log_remote_output
            ssh_exit_code=${PIPESTATUS[0]}
            if [[ $ssh_exit_code -eq 0 ]]; then
                exec_success=true
            fi
        elif [[ $ssh_exit_code -ne 255 ]]; then
            # Script execution failed (not an auth failure)
            exec_success=false
        fi
    elif [[ -n "$SERVER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
        # No SSH key, use password directly
        sshpass -p "$SERVER_PASSWORD" ssh -p "$use_port" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$ssh_cmd" 2>&1 | prefix_and_log_remote_output
        ssh_exit_code=${PIPESTATUS[0]}
        auth_attempted=true
        if [[ $ssh_exit_code -eq 0 ]]; then
            exec_success=true
        fi
    else
        # Try without any authentication method (will prompt for password)
        ssh -p "$use_port" -o ConnectTimeout=5 "$SERVER_USER@$SERVER_HOST" "$ssh_cmd" 2>&1 | prefix_and_log_remote_output
        ssh_exit_code=${PIPESTATUS[0]}
        auth_attempted=true
        if [[ $ssh_exit_code -eq 0 ]]; then
            exec_success=true
        fi
    fi
    
    if [[ "$exec_success" == true ]]; then
        log_message "INFO" "Remote script execution completed successfully: $script_file on $alias"
        return 0
    else
        log_message "ERROR" "Remote script execution failed: $script_file on $alias"
        return 1
    fi
}

# Run all scripts defined for a host-alias in order
run_scripts() {
    local alias="$1"
    
    info "Loading configuration for alias: $alias"
    get_server_config "$alias"
    
    [[ -z "$SERVER_HOST" ]] && { error "Unknown host-alias: $alias"; exit 1; }
    [[ -z "$SERVER_SCRIPTS" ]] && { warn "No scripts defined for host-alias: $alias"; exit 0; }
    
    log_message "INFO" "Starting script execution batch for $alias ($SERVER_HOST:$SERVER_PORT) - scripts: $SERVER_SCRIPTS"
    
    echo ""
    echo "🚀 Starting script execution on server"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "📍 Server: $SERVER_HOST:$SERVER_PORT"
    echo "👤 User: $SERVER_USER"
    echo "📜 Scripts: $SERVER_SCRIPTS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # Determine working port with fallback
    local use_port=$(determine_ssh_port "$SERVER_PORT" "$SERVER_HOST")
    [[ -z "$use_port" ]] && exit 1
    
    # Split scripts by comma and run each
    IFS=',' read -ra script_array <<< "$SERVER_SCRIPTS"
    local total=${#script_array[@]}
    local count=0
    local failed_scripts=()
    
    for script in "${script_array[@]}"; do
        script=$(echo "$script" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')  # Trim whitespace
        count=$((count + 1))
        
        echo ""
        echo "📋 [$count/$total] Processing: $script"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        
        log_message "INFO" "Processing script $count/$total: $script for $alias"
        
        # Upload script
        if upload_script "$script" "$alias" "$use_port"; then
            success "Script uploaded successfully"
        else
            error "Failed to upload script: $script"
            failed_scripts+=("$script (upload failed)")
            log_message "ERROR" "Script batch execution failed at upload stage: $script for $alias"
            exit 1
        fi
        
        # Execute script
        echo ""
        progress "Executing $script on $SERVER_HOST..."
        echo "┌─ Remote Script Output ─────────────────────────────────┐"
        if execute_remote_script "$script" "$alias" "$use_port"; then
            echo "└─────────────────────────────────────────────────────────┘"
            echo ""
            success "Script executed successfully: $script"
        else
            echo "└─────────────────────────────────────────────────────────┘"
            echo ""
            error "Script execution failed: $script"
            failed_scripts+=("$script (execution failed)")
            log_message "ERROR" "Script batch execution failed at execution stage: $script for $alias"
            exit 1
        fi
        
        # Add delay between scripts to avoid triggering fail2ban
        if [[ $count -lt $total ]]; then
            info "Waiting 3 seconds before next script to avoid rate limiting..."
            sleep 3
        fi
        
        echo ""
    done
    
    echo ""
    echo "🏁 All scripts completed successfully!"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    success "Script execution completed for $alias"
    log_message "INFO" "Script batch execution completed successfully for $alias - $total scripts executed"
}

# Run a specific script on remote server
run_single_script() {
    local alias="$1"
    local script_name="$2"
    
    info "Loading configuration for alias: $alias"
    get_server_config "$alias"
    
    [[ -z "$SERVER_HOST" ]] && { error "Unknown host-alias: $alias"; exit 1; }
    
    info "Server: $SERVER_HOST:$SERVER_PORT"
    info "User: $SERVER_USER"
    info "Script to run: $script_name"
    echo ""
    
    log_message "INFO" "Single script execution requested: $script_name for $alias ($SERVER_HOST:$SERVER_PORT)"
    
    # Check if script exists
    if [[ ! -f "$SCRIPT_DIR/scripts/$script_name" ]]; then
        error "Script not found: $SCRIPT_DIR/scripts/$script_name"
        log_message "ERROR" "Script file not found for single execution: $SCRIPT_DIR/scripts/$script_name"
        echo ""
        echo "Available scripts in $SCRIPT_DIR/scripts/:"
        ls -1 "$SCRIPT_DIR/scripts/" 2>/dev/null | grep -E '\.sh$' | sed 's/^/  - /'
        exit 1
    fi
    
    # Determine working port with fallback
    local use_port=$(determine_ssh_port "$SERVER_PORT" "$SERVER_HOST")
    [[ -z "$use_port" ]] && exit 1
    
    # Upload script
    if upload_script "$script_name" "$alias" "$use_port"; then
        success "Script uploaded successfully"
    else
        error "Failed to upload script: $script_name"
        log_message "ERROR" "Single script execution failed at upload: $script_name for $alias"
        exit 1
    fi
    
    # Execute script
    echo ""
    progress "Executing script: $script_name on $SERVER_HOST"
    echo "┌─ Remote Script Output ─────────────────────────────────┐"
    if execute_remote_script "$script_name" "$alias" "$use_port"; then
        echo "└─────────────────────────────────────────────────────────┘"
        echo ""
        success "Script executed successfully: $script_name"
        log_message "INFO" "Single script execution completed successfully: $script_name for $alias"
    else
        echo "└─────────────────────────────────────────────────────────┘"
        echo ""
        error "Script execution failed: $script_name"
        log_message "ERROR" "Single script execution failed at execution: $script_name for $alias"
        exit 1
    fi
}

# List available scripts for a host-alias
list_scripts() {
    local alias="$1"
    
    info "Loading configuration for alias: $alias"
    get_server_config "$alias"
    
    [[ -z "$SERVER_HOST" ]] && { error "Unknown host-alias: $alias"; exit 1; }
    
    echo ""
    echo "Server: $SERVER_HOST"
    echo "Host-alias: $alias"
    echo ""
    
    if [[ -n "$SERVER_SCRIPTS" ]]; then
        echo "Configured scripts (will run in this order):"
        IFS=',' read -ra script_array <<< "$SERVER_SCRIPTS"
        local count=0
        for script in "${script_array[@]}"; do
            script=$(echo "$script" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            count=$((count + 1))
            echo "  $count. $script"
        done
    else
        echo "No scripts configured for this host-alias."
    fi
    
    echo ""
    echo "All available scripts in $SCRIPT_DIR/scripts/:"
    if [[ -d "$SCRIPT_DIR/scripts" ]]; then
        ls -1 "$SCRIPT_DIR/scripts/" 2>/dev/null | grep -E '\.sh$' | sed 's/^/  - /' || echo "  (no scripts found)"
    else
        echo "  (scripts directory not found)"
    fi
    
    echo ""
    echo "To run all configured scripts in order:"
    echo "  $SCRIPT_NAME run-scripts $alias"
    echo ""
    echo "To run a specific script:"
    echo "  $SCRIPT_NAME run-script $alias <script-name>"
}

# Show logs function
show_logs() {
    local log_type="${1:-main}"
    local lines="${2:-50}"
    
    [[ "$ENABLE_LOGGING" != "true" ]] && { warn "Logging is disabled"; return 1; }
    [[ ! -d "$LOG_DIR" ]] && { warn "Log directory not found: $LOG_DIR"; return 1; }
    
    local log_file=""
    case "$log_type" in
        main|all)
            log_file="$MAIN_LOG"
            echo "📋 Main Log (last $lines lines):"
            ;;
        error|errors)
            log_file="$ERROR_LOG"
            echo "❌ Error Log (last $lines lines):"
            ;;
        remote)
            log_file="$REMOTE_LOG"
            echo "🖥️  Remote Script Log (last $lines lines):"
            ;;
        *)
            error "Unknown log type: $log_type"
            echo "Available log types: main, error, remote"
            return 1
            ;;
    esac
    
    if [[ -f "$log_file" ]]; then
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        tail -n "$lines" "$log_file"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "Log file location: $log_file"
        echo "Log file size: $(du -h "$log_file" 2>/dev/null | cut -f1 || echo "unknown")"
    else
        warn "Log file not found: $log_file"
        echo "Available log files in $LOG_DIR:"
        ls -la "$LOG_DIR" 2>/dev/null || echo "  (none)"
    fi
}

# Clear logs function
clear_logs() {
    local log_type="${1:-all}"
    
    [[ "$ENABLE_LOGGING" != "true" ]] && { warn "Logging is disabled"; return 1; }
    [[ ! -d "$LOG_DIR" ]] && { warn "Log directory not found: $LOG_DIR"; return 1; }
    
    case "$log_type" in
        main)
            [[ -f "$MAIN_LOG" ]] && { > "$MAIN_LOG"; info "Main log cleared"; } || warn "Main log not found"
            ;;
        error|errors)
            [[ -f "$ERROR_LOG" ]] && { > "$ERROR_LOG"; info "Error log cleared"; } || warn "Error log not found"
            ;;
        remote)
            [[ -f "$REMOTE_LOG" ]] && { > "$REMOTE_LOG"; info "Remote log cleared"; } || warn "Remote log not found"
            ;;
        all)
            local cleared=0
            [[ -f "$MAIN_LOG" ]] && { > "$MAIN_LOG"; ((cleared++)); }
            [[ -f "$ERROR_LOG" ]] && { > "$ERROR_LOG"; ((cleared++)); }
            [[ -f "$REMOTE_LOG" ]] && { > "$REMOTE_LOG"; ((cleared++)); }
            
            # Remove rotated logs
            rm -f "$LOG_DIR"/*.log.[0-9]* 2>/dev/null
            
            if [[ $cleared -gt 0 ]]; then
                info "All logs cleared ($cleared log files)"
            else
                warn "No log files found to clear"
            fi
            ;;
        *)
            error "Unknown log type: $log_type"
            echo "Available log types: main, error, remote, all"
            return 1
            ;;
    esac
}

# Reset SSH host key for a server (useful after reinstall)
reset_hostkey() {
    local alias="$1"
    
    info "Loading configuration for alias: $alias"
    get_server_config "$alias"
    
    [[ -z "$SERVER_HOST" ]] && { error "Unknown host-alias: $alias"; exit 1; }
    
    echo ""
    echo "Removing SSH host keys for: $SERVER_HOST"
    echo "This is useful when a server has been reinstalled."
    echo ""
    
    log_message "INFO" "Resetting SSH host keys for $alias ($SERVER_HOST)"
    
    # Remove from known_hosts
    local removed=false
    
    # Try to remove by hostname
    if ssh-keygen -R "$SERVER_HOST" 2>/dev/null; then
        info "Removed host key for: $SERVER_HOST"
        removed=true
    fi
    
    # Also try to remove entries for different ports
    for port in 22 896 "$SERVER_PORT"; do
        if ssh-keygen -R "[$SERVER_HOST]:$port" 2>/dev/null; then
            info "Removed host key for: [$SERVER_HOST]:$port"
            removed=true
        fi
    done
    
    if [[ "$removed" == true ]]; then
        echo ""
        info "✓ Host keys removed successfully"
        log_message "INFO" "SSH host key reset successful for $alias ($SERVER_HOST)"
        echo ""
        echo "You can now connect to the server without host key warnings:"
        echo "  $SCRIPT_NAME login $alias"
    else
        warn "No host keys found for $SERVER_HOST"
        log_message "WARN" "No SSH host keys found to remove for $alias ($SERVER_HOST)"
        echo "The server might not be in your known_hosts file."
    fi
}

# Main function
main() {
    # Load global configuration first (before logging is initialized)
    if ! load_global_config "$CONFIG_FILE"; then
        echo "⚠️  Failed to load configuration from $CONFIG_FILE" >&2
        echo "⚠️  Using default configuration values" >&2
    fi
    
    # Validate configuration syntax
    if ! validate_config_syntax "$CONFIG_FILE"; then
        echo "⚠️  Configuration file has syntax errors - some features may not work correctly" >&2
    fi
    
    # Initialize logging after loading configuration
    init_logging
    
    # Log the command being executed
    log_message "INFO" "Command executed: $SCRIPT_NAME $*"
    log_message "INFO" "Configuration loaded - LOG_DIR: $LOG_DIR, LOG_LEVEL: $LOG_LEVEL, ENABLE_LOGGING: $ENABLE_LOGGING"
    
    case "${1:-}" in
        login)
            [[ -z "${2:-}" ]] && { error "Host-alias required for login command"; usage; exit 1; }
            ssh_login "$2"
            ;;
        reset-hostkey)
            [[ -z "${2:-}" ]] && { error "Host-alias required for reset-hostkey command"; usage; exit 1; }
            reset_hostkey "$2"
            ;;
        run-scripts)
            [[ -z "${2:-}" ]] && { error "Host-alias required for run-scripts command"; usage; exit 1; }
            run_scripts "$2"
            ;;
        run-script)
            [[ -z "${2:-}" ]] && { error "Host-alias required for run-script command"; usage; exit 1; }
            [[ -z "${3:-}" ]] && { error "Script name required for run-script command"; usage; exit 1; }
            run_single_script "$2" "$3"
            ;;
        list-scripts)
            [[ -z "${2:-}" ]] && { error "Host-alias required for list-scripts command"; usage; exit 1; }
            list_scripts "$2"
            ;;
        logs)
            show_logs "${2:-main}" "${3:-50}"
            ;;
        clear-logs)
            clear_logs "${2:-all}"
            ;;
        install)
            install_sofilab
            ;;
        uninstall)
            uninstall_sofilab
            ;;
        --version|-V|version)
            show_version
            ;;
        --help|-h|help)
            usage
            ;;
        "")
            usage
            ;;
        *)
            error "Unknown command: ${1:-}"
            usage
            exit 1
            ;;
    esac
}

# Run main function if script is executed directly
[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
