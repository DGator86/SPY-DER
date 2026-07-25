# Deploying SPY-DER

SPY-DER deploys itself, pull-based. The VPS polls the origin every two minutes
and, when a new commit lands on the deploy branch, runs this repo's own
`deploy/remote-deploy.sh`.

```
git push → GitHub → (VPS polls every 2 min) → spy-der-update.timer
    → deploy/self-update.sh → deploy/remote-deploy.sh → units restarted
```

Only **outbound** HTTPS is required. A firewall change, an IP rotation, or a
blocked inbound SSH port cannot strand a release, because the box pulls.

## First run

```bash
# on the VPS, as root
curl -fsSL https://raw.githubusercontent.com/DGator86/SPY-DER/main/deploy/remote-deploy.sh | bash
```

That provisions everything: the `spy-der` service user, the checkout at
`/opt/spy-der`, the venv, the state tree under `/var/lib/spy-der`, every systemd
unit, and the self-update timer. It stops short of starting the runtime and
tells you what to do next, because the secrets file does not exist yet:

```bash
sudo install -D -m 640 -o root -g spy-der \
     /opt/spy-der/deploy/spy-der.env.example /etc/spy-der/spy-der.env
sudo nano /etc/spy-der/spy-der.env
# Set at least one market provider: TRADIER_ACCESS_TOKEN and/or MASSIVE_API_KEY.
# Also set XAI_API_KEY for the agent.
```

The next self-update run (within two minutes) starts the units. Nothing else is
needed — you never have to run the deploy by hand again.

`remote-deploy.sh` never writes `/etc/spy-der/spy-der.env`. A deploy cannot
overwrite a key.

## What each run does

| Step | Behaviour |
|---|---|
| Checkout | `git reset --hard` to the requested ref |
| Venv | `pip install -e .` — **not** just a pull |
| State | creates every directory `config.yaml.example` declares |
| Ownership | `chown -R spy-der:spy-der`, directories `0755` |
| Config | installs `config.yaml.example` only if `/etc/spy-der/config.yaml` is absent |
| Units | installs and restarts every service; enables every timer |

The `pip install` is not decoration. A bare `git reset --hard` moves source but
never creates new console-script entry points and never installs new
dependencies — which is exactly how `spy-der dashboard-api` came to exist in the
repo while being unrunnable on the box.

## Ownership

`/var/lib/spy-der` is owned by `spy-der` with `0755` directories, and the
runtime publishes reports `0644`. Both matter:

- The units declare `StateDirectory=spy-der`, so **systemd resets ownership to
  `spy-der` whenever a unit starts**. A deploy that chowns this tree to a
  different user does not win — it flaps, and the two fight on their own
  schedules.
- The state under `reports/` is a published surface. Other local readers — a
  dashboard adapter running as its own user — must be able to open it. That is
  what world-readable files and traversable directories are for, and it is why
  the fix is *permissions*, not *ownership*.

If another service on the box needs to read this tree, leave the ownership
alone and rely on the `0644`/`0755` modes.

## Units installed

| Unit | Kind |
|---|---|
| `spy-der-market` | service — provider ingestion |
| `spy-der-engine` | service — deterministic stages |
| `spy-der-settlement` | service — outcome labeling |
| `spy-der-agent` | service — decision boundary (`:8787`) |
| `spy-der-dashboard-api` | service — read-only report API (`:8788`) |
| `spy-der-dojo-{daily,recent,weekly}` | timers |
| `spy-der-validation-{daily,weekly}` | timers |
| `spy-der-update` | timer — the poller itself |

`tests/unit/test_deploy_independence.py` fails if a unit in `deploy/` is not
installed by `remote-deploy.sh`, and if a unit's `ExecStart` names a CLI
subcommand the package does not dispatch or a script the repo does not ship.

## Pinning or rolling back

```bash
# deploy a specific commit once
sudo DEPLOY_REF=<sha> bash /opt/spy-der/deploy/remote-deploy.sh

# follow a different branch
sudo systemctl edit spy-der-update.service   # Environment=DEPLOY_BRANCH=release
```

A one-off `DEPLOY_REF` deploy is reverted by the next poll, which fast-forwards
back to the branch head. To hold a pin, stop the timer:

```bash
sudo systemctl disable --now spy-der-update.timer
```

## Watching a deploy

```bash
journalctl -u spy-der-update.service -f
systemctl list-timers 'spy-der-*'
git -C /opt/spy-der rev-parse --short HEAD
```

The poller stays silent when the checkout is already current, so journal output
means a real deploy happened.
