# Ampelmann

LLM-powered system alert filter. Runs scheduled checks, analyzes output with a local LLM (Ollama), and sends notifications via ntfy when action is needed.

Named after the iconic DDR traffic light figure — green means all good, red means stop and pay attention.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai/) running locally
- [ntfy](https://ntfy.sh/) server (self-hosted or ntfy.sh)

## Installation

```bash
# Development
git clone https://github.com/mkoskinen/ampelmann.git
cd ampelmann
make install-dev

# System-wide (as root)
sudo make install-system
sudo make deploy-checks
sudo make deploy-systemd
sudo systemctl enable --now ampelmann.timer
```

## Quick Start

```bash
# List available checks
ampelmann list

# Test a check without sending alerts
ampelmann test disk-space --verbose

# Run all due checks
ampelmann run

# Run a specific check
ampelmann run disk-space

# Run all checks regardless of schedule
ampelmann run --all --force
```

## Configuration

Main config: `/etc/ampelmann/config.toml`

```toml
[ollama]
host = "http://localhost:11434"
model = "qwen2.5:7b"
timeout = 120

[ntfy]
url = "https://ntfy.sh"
topic = "ampelmann"
# token = "tk_..."  # Access token if server requires auth

[database]
path = "/var/lib/ampelmann/ampelmann.db"

[dashboard]
output_dir = "/srv/site/ampelmann/www"
auto_update = true  # Regenerate after each run

[defaults]
analyze_errors = true  # Use LLM to explain command failures
# error_model = "llama3:70b"  # Use smarter model for errors
default_history_context = 3  # Previous runs to include in LLM context
```

## Writing Checks

Checks live in `/etc/ampelmann/checks.d/*.toml`:

```toml
name = "disk-space"
description = "Check disk space usage"
enabled = true
command = "df -h"
schedule = "0 */4 * * *"  # Every 4 hours
timeout = 30

[llm]
prompt = """
Analyze this disk space output. Respond with ONLY:
- "OK" if all filesystems have adequate space (< 85% used)
- A brief alert message if action is needed
"""

[notify]
priority = "default"
tags = ["disk", "storage"]
```

### Privileged Commands

Some checks need root access (e.g., `smartctl`). Set `sudo = true` and the command runs via sudo:

```toml
name = "smart-disk"
command = "smartctl -a /dev/sda"
sudo = true
```

Configure passwordless sudo for specific commands in `/etc/sudoers.d/ampelmann`:

```
username ALL=(root) NOPASSWD: /usr/sbin/smartctl
```

See `examples/sudoers.d/ampelmann` for a template.

### Schedule Format

Standard cron syntax: `minute hour day month weekday`

```
* * * * *       # Every minute
*/15 * * * *    # Every 15 minutes
0 * * * *       # Hourly
0 6 * * *       # Daily at 6am
0 6 * * 1       # Weekly on Monday at 6am
```

### Two-Stage LLM Analysis

For efficiency, use a fast model for triage and a smarter model only when issues are detected:

```toml
[llm]
triage_model = "qwen2.5:3b"   # Fast: just OK/ALERT decision
analysis_model = "qwen2.5:7b"  # Detailed: explain the issue
# skip_analysis = true  # Skip detailed analysis, just use triage result
history_context = 5  # Override default for this check

prompt = """
Analyze this output. If there's an issue, explain what's wrong.
"""
```

When `triage_model` is set:
1. Fast model determines OK/ALERT
2. If OK → done (no second call)
3. If ALERT → analysis model explains the issue

### LLM Prompt Tips

- **Keep alerts short** — specify exact output format, e.g. `Format: "<mount> at <N>%"`
- **Forbid verbosity** — add "No explanations" or "One line per issue"
- List specific conditions that warrant alerts
- Prompts are used by the analysis model; triage has its own built-in prompt

### Matrix Expansion

Define multiple similar checks from a single file using `[matrix]`:

```toml
name = "smart-${disk}"
description = "SMART health for /dev/${disk}"
command = "smartctl -a /dev/${disk}"
schedule = "0 6 * * *"

[matrix]
disk = ["sda", "sdb", "nvme0n1"]

[llm]
prompt = "Analyze SMART output for ${disk}..."

[notify]
tags = ["smart", "${disk}"]
```

This expands to three independent checks: `smart-sda`, `smart-sdb`, `smart-nvme0n1`. Each has its own history, status, and alerts.

Variables use `${name}` syntax and are substituted in all string fields.

Multiple matrix variables create a cartesian product:

```toml
[matrix]
host = ["web1", "web2"]
port = ["80", "443"]
# Creates: check-web1-80, check-web1-443, check-web2-80, check-web2-443
```

## CLI Reference

```
ampelmann status
    Compact traffic-light view of all checks.

ampelmann run [CHECK] [--all] [--force] [--dry-run] [--no-notify]
    Run checks. Without arguments, runs due checks only.

ampelmann list
    List all checks with status and schedule.

ampelmann show <name>
    Show check details and recent history.

ampelmann test <name> [-v]
    Test a check without sending alerts or saving to database.

ampelmann validate
    Validate all check configurations.

ampelmann history [--status ok|alert|error] [--limit N]
    Show recent check history.

ampelmann dashboard
    Regenerate dashboard JSON files.

ampelmann alert "message" [--priority P] [--tags T]
    Send a manual notification.

ampelmann enable <name>
    Enable a check.

ampelmann disable <name>
    Disable a check.

ampelmann cleanup [--days N]
    Remove old data from database.
```

## Dashboard

Static HTML dashboard served by your existing web server:

```
/srv/site/ampelmann/www/
├── index.html
├── data/
│   ├── status.json
│   ├── history.json
│   ├── stats.json
│   └── checks/*.json
└── assets/
    ├── style.css
    └── ampelmann.svg
```

Regenerates automatically after each run if `auto_update = true` in config, or manually:

```bash
ampelmann dashboard
```

## File Locations

```
/etc/ampelmann/
├── config.toml          # Main configuration
└── checks.d/            # Check definitions

/var/lib/ampelmann/
└── ampelmann.db         # SQLite database

/var/log/ampelmann/
└── ampelmann.log        # Application log
```

## Development

```bash
make test           # Run tests
make test-cov       # Run tests with coverage
make lint           # Run linter
make typecheck      # Run type checker
make check-all      # All of the above
```

## License

MIT
