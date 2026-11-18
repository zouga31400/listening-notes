# Always-on listening on a free Oracle Cloud server

`radio_offshow.py` listens to the radio all day, outside the show, and fills its own
playlists ("Radio flux hors émission", its monthly copy, "Radio flux {decade}",
"Radio flux {mood} {period}") and the "Radio - Hors émission" Notion database. Settings:
the `offshow_*` keys in `config.txt`. No notifications, except errors.

It runs on an Oracle Cloud "Always Free" server (free for good; a card is asked only to
check your identity). GitHub Actions is not used for it: its terms forbid long-running jobs
that are unrelated to the project's software.

## 1. Create the server (about 15 minutes)

1. Sign up at <https://signup.cloud.oracle.com> (choose a home region close to you, e.g.
   Paris or Frankfurt; it cannot be changed later). Stay on the free tier.
2. Menu ☰ → Compute → Instances → **Create instance**.
   - Image: **Canonical Ubuntu 24.04**.
   - Shape: **Ampere A1** (VM.Standard.A1.Flex) with 1 OCPU and 6 GB of memory. If Oracle
     says it is out of capacity, pick **VM.Standard.E2.1.Micro** instead (also free; the
     install adds swap memory for it).
   - SSH keys: keep "Generate a key pair for me" and **download the private key**.
   - Create. Wait until the instance is "Running".
3. No port needs to be opened: the server only makes outgoing connections.
4. **Keep the server from being reclaimed.** Oracle may take back a free server that looks
   idle for 7 days (CPU under 20% most of the time, little network, and for Ampere A1 less
   than 20% of its memory used): this listener is that light. Upgrading the account to
   **Pay As You Go** (Billing → Upgrade) is the usual way to avoid it (widely reported; not
   spelled out in Oracle's documentation); the Always Free resources stay free. Add a
   budget alert of 1 € (Billing → Budgets) to be warned if anything ever costs money.

## 2. Install (about 10 minutes)

Connect to the server: on the instance page, **Cloud Shell** (top right icon `>_`), then

```
ssh -i <downloaded key> ubuntu@<public IP of the instance>
```

(Cloud Shell has a menu to upload the key file; `chmod 600` it first.) On the server:

```
curl -fsSL https://raw.githubusercontent.com/zouga31400/listening-notes/main/deploy/oracle/install.sh | sudo bash
sudo nano /etc/listening-notes/radio.env
```

Fill in the settings with the same values as the GitHub secrets (MUSIC_*, STREAM_URL,
NOTION_TOKEN, NTFY_TOPIC, SHOW_END_TOPIC...). Save (Ctrl+O, Enter, Ctrl+X), then:

```
sudo systemctl restart radio-offshow
sudo journalctl -u radio-offshow -f
```

The log shows "Off-show listening until ..." and, with VERBOSE_LOGS=1, each track. Ctrl+C
leaves the log (the listener keeps running). The settings file is readable by root only.

## Daily life

- The listener starts when the server starts and restarts by itself after a failure.
- Every morning at 05:10 (Paris time) it takes the latest code and `config.txt` from GitHub
  and restarts if something changed. Edit `config.txt` on GitHub as usual.
- A health check is optional: create a free check on healthchecks.io (period 10 minutes,
  grace 30 minutes), put its URL in `OFFSHOW_HEALTHCHECK_URL`, restart. You are warned if
  the server stops.
- Useful commands: `sudo systemctl status radio-offshow`, `sudo systemctl stop radio-offshow`,
  `sudo /opt/listening-notes/app/deploy/oracle/update.sh` (update now).

## The show

`offshow_show_here` in `config.txt`:

- `no` (start with this): the show stays on GitHub Actions. On weekdays the server pauses
  from `listen_start` until the "show ended" signal (or `latest_end`), then resumes. The
  music before the show starts is not caught in this mode.
- `yes`: the server also runs the show, with exactly the same code and playlists as
  `radio.yml`; the tracks before its detected start and after its detected end go to the
  off-show playlists. Then **turn off the cron-job.org call that starts "Radio listening"**,
  otherwise the show runs twice. The workflow stays in place as a backup (run it by hand).
  Put the show's `HEALTHCHECK_URL` in the settings file too.
