#!/usr/bin/env bash
# Run ON the VPS as root.
# 1) Repair cursor-agent authorized_keys line
# 2) Copy blank provider keys from /etc/zerodte/zerodte.env into /etc/spy-der/spy-der.env
# 3) Restart spy-der-market and print non-secret verification
set -euo pipefail

SRC=/etc/zerodte/zerodte.env
DST=/etc/spy-der/spy-der.env
PUB='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKY00daToP6dMG35gHAi58mGIb52AtNJiYsmVhbUGYcX cursor-agent'
KEYBLOB='AAAAC3NzaC1lZDI1NTE5AAAAIKY00daToP6dMG35gHAi58mGIb52AtNJiYsmVhbUGYcX'

echo "== repair authorized_keys =="
mkdir -p /root/.ssh
chmod 700 /root /root/.ssh
touch /root/.ssh/authorized_keys
grep -vF "$KEYBLOB" /root/.ssh/authorized_keys > /tmp/ak.clean || true
printf '%s\n' "$PUB" >> /tmp/ak.clean
mv /tmp/ak.clean /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
chown root:root /root/.ssh/authorized_keys
grep -nF "$KEYBLOB" /root/.ssh/authorized_keys | cat -A
ssh-keygen -lf /root/.ssh/authorized_keys | grep cqW0haHmSQnf1ArmcwujuRqHNUGQui5j5C70S06Hv50 || true

echo "== ensure env file =="
if [ ! -f "$DST" ]; then
  install -D -m 640 -o root -g spy-der /opt/spy-der/deploy/spy-der.env.example "$DST"
fi
if [ ! -f "$SRC" ]; then
  echo "Missing $SRC" >&2
  exit 1
fi

echo "== copy blank keys from zerodte.env =="
python3 - "$SRC" "$DST" <<'PY'
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
keys = [
    "TRADIER_ACCESS_TOKEN",
    "TRADIER_API_KEY",
    "TRADIER_BASE_URL",
    "MASSIVE_API_KEY",
    "MASSIVE_BASE_URL",
    "MASSIVE_AUTH",
    "XAI_API_KEY",
]

def parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v
    return out

s = parse(src)
d = parse(dst)
changed: list[str] = []
for k in keys:
    sv = (s.get(k) or "").strip()
    dv = (d.get(k) or "").strip()
    if sv and not dv:
        d[k] = s[k]
        changed.append(k)

lines = dst.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
out: list[str] = []
for raw in lines:
    st = raw.strip()
    if st and not st.startswith("#") and "=" in st:
        k = st.split("=", 1)[0].strip()
        if k in d:
            out.append(f"{k}={d[k]}")
            seen.add(k)
            continue
    out.append(raw)
for k in keys:
    if k in d and k not in seen:
        out.append(f"{k}={d[k]}")
dst.write_text("\n".join(out) + "\n", encoding="utf-8")
print("changed", ",".join(changed) if changed else "none")
for k in ("TRADIER_ACCESS_TOKEN", "MASSIVE_API_KEY", "XAI_API_KEY"):
    val = (d.get(k) or "").strip()
    print(f"{k}={'set' if val else 'EMPTY'} len={len(val)}")
PY

chown root:spy-der "$DST"
chmod 640 "$DST"

echo "== restart spy-der-market =="
systemctl daemon-reload
systemctl restart spy-der-market.service
sleep 2
active=$(systemctl is-active spy-der-market.service || true)
pid=$(systemctl show -p MainPID --value spy-der-market)
echo "active=$active PID=$pid"
if [ "$pid" != 0 ] && [ -r "/proc/$pid/environ" ]; then
  echo "== process env (names only) =="
  tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^(TRADIER_|MASSIVE_|XAI_)' | sed 's/=.*/=set/' || echo process_env=NONE
else
  echo "== service not running; journal =="
  journalctl -u spy-der-market -n 50 --no-pager || true
fi
echo "DONE"
