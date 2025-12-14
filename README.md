# Ampelmann

LLM-powered system monitoring. Runs scheduled checks, analyzes output with a local LLM (Ollama), and sends notifications via ntfy when action is needed.

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

# Add to root crontab
sudo crontab -e
# Add: */15 * * * * /usr/local/bin/ampelmann run >> /var/log/ampelmann/cron.log 2>&1
```

## Quick Start

```bash
# List available checks
ampelmann list

# Test a check without sending alerts
ampelmann test system-health --verbose

# Run all due checks
ampelmann run

# Run a specific check
ampelmann run system-health

# Run all checks regardless of schedule
ampelmann run --all --force
```

## Configuration

Main config: `/etc/ampelmann/config.toml`

```toml
[ollama]
host = "http://localhost:11434"
model = "qwen2.5-coder:32b-instruct-q4_K_M"
timeout = 600  # 10 min for large models on CPU

[ntfy]
url = "https://ntfy.sh"
topic = "ampelmann"
# token = "tk_..."  # Access token if server requires auth

[database]
path = "/var/lib/ampelmann/ampelmann.db"

[dashboard]
output_dir = "/var/www/ampelmann"
auto_update = true  # Regenerate after each run

[defaults]
analyze_errors = true
default_history_context = 3
```

## Writing Checks

Checks live in `/etc/ampelmann/checks.d/*.toml`. There are two types:

### Simple Checks (No LLM)

Fast checks using bash logic. Exit code determines status (0=OK, non-zero=ALERT):

```toml
name = "disk-full"
description = "Alert if any disk is 90% or more full"
enabled = true
use_llm = false
schedule = "*/30 * * * *"  # Every 30 minutes
timeout = 10

# Exit 0 = OK, Exit 1 = ALERT (output becomes alert message)
command = """
df -h | awk 'NR>1 {gsub(/%/,"",$5); if($5>=90) {print $6": "$5"%"; err=1}} END{exit err?1:0}'
"""

[notify]
priority = "high"
tags = ["disk", "critical"]
```

### LLM Checks (Comprehensive Analysis)

Use a large model for smart analysis. Best for daily comprehensive reports:

```toml
name = "system-health"
description = "Daily comprehensive system health analysis"
enabled = true
schedule = "0 8,20 * * *"  # 8am and 8pm
timeout = 120
sudo = true  # Needed for journalctl, dmesg, smartctl

command = """
echo "=== SYSTEM HEALTH REPORT ==="
echo "\n### DISK USAGE ###"
df -h | grep -v tmpfs
echo "\n### SMART STATUS ###"
for disk in /dev/sd? /dev/nvme?n?; do
  [ -e "$disk" ] && smartctl -H "$disk" 2>/dev/null | grep -E "SMART|result"
done
echo "\n### FAILED SERVICES ###"
systemctl --failed --no-pager --no-legend
echo "\n### JOURNAL ERRORS (24h) ###"
journalctl -p err --since "24 hours ago" --no-pager -q | tail -30
echo "\n### DMESG ERRORS ###"
dmesg --level=err,warn --ctime 2>/dev/null | tail -20
echo "\n### MEMORY ###"
free -h
echo "\n### LOAD ###"
uptime
"""

[llm]
model = "qwen2.5-coder:32b-instruct-q4_K_M"
timeout = 600  # 10 min for 32B on CPU
history_context = 3  # Compare with previous reports
prompt = """
You are a Linux sysadmin analyzing a daily health report.

Your response MUST start with exactly one of:
- STATUS: OK
- STATUS: WARNING
- STATUS: CRITICAL

Then briefly list any issues (1 line each). If OK, say "All systems normal."

Be concise. Only mention actual issues from the output. Do not invent problems.
"""

[notify]
priority = "high"
tags = ["daily", "health"]
```

### Recommended Setup

| Check | Type | Schedule | Purpose |
|-------|------|----------|---------|
| `disk-full` | No LLM | Every 30 min | Fast alert on critical disk usage |
| `system-health` | LLM | 2x daily | Comprehensive system analysis |
| `security-audit` | LLM | Daily 6am | Auth anomalies, new ports, cron changes |

Simple checks catch emergencies fast. LLM checks do smart daily analysis.

### Privileged Commands

Some checks need root access (smartctl, dmesg, journalctl). Set `sudo = true`:

```toml
command = "smartctl -H /dev/sda"
sudo = true
```

If running as non-root, configure passwordless sudo in `/etc/sudoers.d/ampelmann`:

```
ampelmann ALL=(root) NOPASSWD: /usr/sbin/smartctl
ampelmann ALL=(root) NOPASSWD: /bin/dmesg
ampelmann ALL=(root) NOPASSWD: /usr/bin/journalctl
```

### Schedule Format

Standard cron syntax: `minute hour day month weekday`

```
*/30 * * * *    # Every 30 minutes
0 * * * *       # Hourly
0 8,20 * * *    # 8am and 8pm
0 6 * * *       # Daily at 6am
0 6 * * 1       # Weekly on Monday at 6am
```

### History Context

LLM checks can access previous command outputs for trend detection:

```toml
[llm]
history_context = 3  # Include last 3 runs in LLM context
```

History only includes raw command output (not LLM responses) to prevent hallucination feedback loops.

### LLM Prompt Tips

- Start with structured output: `STATUS: OK/WARNING/CRITICAL`
- Be explicit about thresholds: "Disk >= 85% → WARNING"
- Add "Do not invent problems" to prevent hallucinations
- Keep response format simple for parsing

### Two-Stage Analysis

Use a fast small model for triage, only calling the large model when issues are detected:

```toml
[llm]
triage_model = "qwen2.5:3b"      # Fast model for OK/ALERT decision
analysis_model = "qwen2.5:32b"   # Large model for detailed analysis (only on ALERT)
skip_analysis = false            # Set true to skip detailed analysis entirely
```

This reduces cost/latency for checks that are usually OK.

### Error Analysis

When commands fail (non-zero exit), optionally analyze the error with LLM:

```toml
[defaults]
analyze_errors = true            # Enable error analysis
error_model = "qwen2.5:7b"       # Model for analyzing failures (optional)
```

### Matrix Expansion

Define multiple similar checks from a single file:

```toml
name = "smart-${disk}"
command = "smartctl -H /dev/${disk}"
schedule = "0 6 * * *"
use_llm = false
sudo = true

[matrix]
disk = ["sda", "sdb", "nvme0n1"]
```

Creates: `smart-sda`, `smart-sdb`, `smart-nvme0n1`

## CLI Reference

```
ampelmann status
    Compact traffic-light view of all checks.

ampelmann run [CHECK] [--all] [--force] [--dry-run] [--no-notify]
    Run checks. Without arguments, runs due checks only.

ampelmann list
    List all checks with status and schedule.

ampelmann show <name>
    Show detailed info about a check.

ampelmann test <name> [-v]
    Test a check without sending alerts or saving to database.

ampelmann validate
    Validate all check configurations.

ampelmann history [--status ok|alert|error] [--limit N]
    Show recent check history.

ampelmann dashboard
    Generate static HTML dashboard.

ampelmann alert "message" [--priority P] [--tags T]
    Send a manual notification.

ampelmann enable <name>
    Enable a disabled check.

ampelmann disable <name>
    Disable a check.

ampelmann cleanup [--days N]
    Remove old history entries (default: 90 days).
```

## Ollama Setup

For comprehensive analysis, use a large model:

```bash
ollama pull qwen2.5-coder:32b-instruct-q4_K_M
```

## File Locations

```
/etc/ampelmann/
├── config.toml          # Main configuration
└── checks.d/            # Check definitions

/var/lib/ampelmann/
└── ampelmann.db         # SQLite database

/var/log/ampelmann/
├── ampelmann.log        # Application log
└── cron.log             # Cron output

/var/www/ampelmann/      # Dashboard (optional)
└── data/                # JSON data files
```

## Development

```bash
make test           # Run tests
make lint           # Run linter
make typecheck      # Run type checker
make check-all      # All of the above
```

## License

MIT
