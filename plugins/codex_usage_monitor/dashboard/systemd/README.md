# Codex Usage Monitor systemd runner

These user-systemd units run the collector every 15 seconds. They intentionally use `%h` instead of hardcoding a home directory and do not contain credentials.

Install from the repository root:

```bash
mkdir -p ~/.config/systemd/user
cp plugins/codex_usage_monitor/dashboard/systemd/hermes-codex-usage-monitor.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-codex-usage-monitor.timer
systemctl --user status hermes-codex-usage-monitor.timer
journalctl --user -u hermes-codex-usage-monitor.service -n 50 --no-pager
```

Manual one-shot check:

```bash
python3 plugins/codex_usage_monitor/dashboard/collector.py once
```
