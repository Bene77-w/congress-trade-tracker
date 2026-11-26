# Schedule Quiver downloads and imports with a systemd user timer

These unit templates run the Quiver scraper (`scrapers/quiver_scraper.py`) and trade import (`scripts/import_trades.py`) daily at **03:00**. The service uses the project's virtual environment and local `.env` file. The timer runs in the user session and uses `Persistent=true` to catch up on a missed run when the session starts.

## Install the units

From the repository root, set `PROJECT_DIR` to the absolute path of your checkout and install the templates under `~/.config/systemd/user/`.

```bash
PROJECT_DIR=/absolute/path/to/congress_tracker
mkdir -p ~/.config/systemd/user
sed "s#__PROJECT_DIR__#$PROJECT_DIR#g" deploy/systemd/quiver-import.service > ~/.config/systemd/user/quiver-import.service
cp deploy/systemd/quiver-import.timer ~/.config/systemd/user/quiver-import.timer

systemctl --user daemon-reload
systemctl --user enable --now quiver-import.timer
```

To run the timer while logged out, enable lingering once with:

```bash
sudo loginctl enable-linger "$USER"
```

## Run and troubleshoot

```bash
systemctl --user list-timers quiver-import.timer   # Show next and previous runs
systemctl --user start quiver-import.service      # Run immediately
systemctl --user status quiver-import.service     # Show the result and exit code
journalctl --user -u quiver-import.service -e     # See also logs/quiver_cron.log
```

The service runs its two `ExecStart` commands in order. A nonzero scraper exit stops the service before import. Exit code `2` indicates a login failure, and `3` indicates a download or subscription failure.
