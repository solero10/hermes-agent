# Codex Usage Monitor systemd runner

These user-systemd units run the fast Codex usage collector every 15 seconds. The fast path polls current usage without appending human usage logs; slower cache work refreshes reset credits about every 30 minutes and long-history chart payloads about every 5 minutes. The units intentionally use `%h` instead of hardcoding a home directory and do not contain credentials.

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

Expected performance after optimization:

- `journalctl --user -u hermes-codex-usage-monitor.service` should still show a collector run roughly every 15 seconds.
- `systemd` CPU consumption per run should be well below the previous ~25-28 seconds/run RCA baseline.
- Manual dashboard refresh forces a live reset-credit refresh; normal background ticks reuse reset-credit cache until it is stale.
