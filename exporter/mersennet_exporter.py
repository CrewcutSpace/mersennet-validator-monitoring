#!/usr/bin/env python3
"""Prometheus exporter for a Mersennet validator.

The node's own /metrics says nothing about its place in the validator set.
This exporter asks mersennet_validatorSet on the local node and on the public
RPC and exports, for one validator identity: status, benched/jailed, leader
slots of the epoch, stake, rank, active set, and whether the node runs the
current official release.

Standard library only. Configuration through environment variables:

  MERSENNET_IDENTITY    node identity (0x..., from mersennet_nodeIdentity)  required
  MERSENNET_LOCAL_RPC   your node's RPC        default http://127.0.0.1:8545
  MERSENNET_PUBLIC_RPC  reference RPC          default https://rpc.mersennet.com
  LISTEN_ADDR           host:port to serve on  default 0.0.0.0:7979

Endpoints:
  /metrics              both sources, every series labelled source="local"|"public"
  /probe?target=<rpc>   one RPC, no source label (for multi-target setups)
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IDENTITY = os.environ.get("MERSENNET_IDENTITY", "").lower()
LOCAL_RPC = os.environ.get("MERSENNET_LOCAL_RPC", "http://127.0.0.1:8545")
PUBLIC_RPC = os.environ.get("MERSENNET_PUBLIC_RPC", "https://rpc.mersennet.com")
LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "0.0.0.0:7979")

WEI = 10**18
# The official installer takes the current release from this file.
RELEASES = "https://mersennet.com/downloads/SHA256SUMS"
USER_AGENT = "mersennet-validator-monitoring/1.0"
_latest = {"rev": None, "at": 0.0}


def num(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else float(v)
    return v or 0


def rpc(target, method):
    req = urllib.request.Request(
        target,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": []}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["result"]


def latest_rev():
    """Current official release; re-read every 10 minutes, keep the last value on errors."""
    if time.time() - _latest["at"] > 600:
        try:
            req = urllib.request.Request(RELEASES, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                m = re.search(r"mersennet-node-linux-x86_64-([0-9a-f]+)\.tar\.gz", r.read().decode())
            if m:
                _latest.update(rev=m.group(1), at=time.time())
        except Exception:  # noqa: BLE001
            _latest["at"] = time.time() - 540  # retry in a minute
    return _latest["rev"]


class Out:
    """Collects gauges in the Prometheus text format, one HELP/TYPE per name."""

    def __init__(self):
        self.series = {}
        self.help = {}

    def g(self, name, value, help_, labels=None):
        self.help.setdefault(name, help_)
        lbl = ""
        if labels:
            lbl = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}"
        self.series.setdefault(name, []).append(f"{name}{lbl} {value}")

    def text(self):
        out = []
        for name, lines in self.series.items():
            out.append(f"# HELP {name} {self.help[name]}\n# TYPE {name} gauge")
            out.extend(lines)
        return "\n".join(out) + "\n"


def collect(out, target, extra):
    """Add the metrics of one RPC to out; extra are labels added to every series."""
    L = lambda **kw: {**extra, **kw}  # noqa: E731
    try:
        r = rpc(target, "mersennet_validatorSet")
    except Exception as e:  # noqa: BLE001 - any failure is a failed probe
        out.g("mersennet_validatorset_up", 0, "The RPC answered mersennet_validatorSet", L())
        print(f"probe {target} failed: {e}", file=sys.stderr, flush=True)
        return

    p = r.get("params", {})
    height = num(r["height"])
    max_set = num(p.get("maxValidators", 0))
    if p.get("maxValidatorsHeight") and height >= num(p["maxValidatorsHeight"]):
        max_set = num(p.get("maxValidatorsAfter", max_set))
    active = [a.lower() for a in r.get("activeSet", [])]
    vals = r.get("validators", [])
    # Rank by voting stake; on equal stake the earlier registration ranks higher (as on the explorer).
    by_stake = sorted(
        (v for v in vals if not v.get("exiting")),
        key=lambda v: (-num(v.get("votingStake", 0)), num(v.get("registeredAt", 0))),
    )
    active_stakes = [num(v.get("votingStake", 0)) for v in vals if v["identity"].lower() in active]

    out.g("mersennet_validatorset_up", 1, "The RPC answered mersennet_validatorSet", L())
    out.g("mersennet_validatorset_height", height, "Chain height seen by the RPC", L())
    out.g("mersennet_validatorset_epoch", num(r["epoch"]), "Current epoch", L())
    out.g("mersennet_validatorset_next_epoch_at", num(r["nextEpochAt"]), "Height of the next epoch boundary", L())
    out.g("mersennet_validatorset_validators", len(vals), "Registered validators", L())
    out.g("mersennet_validatorset_active", len(active), "Validators in the active set", L())
    out.g("mersennet_validatorset_max_active", max_set, "Size limit of the active set at this height", L())
    out.g("mersennet_validatorset_active_stake_mrsn", sum(active_stakes) / WEI,
          "Voting stake of the active set, MRSN", L())
    out.g("mersennet_validatorset_active_min_stake_mrsn", (min(active_stakes) / WEI) if active_stakes else 0,
          "Smallest voting stake in the active set, MRSN", L())

    me = next((v for v in vals if v["identity"].lower() == IDENTITY), None)
    if me is not None:
        lbl = L(identity=IDENTITY, status=me.get("status", ""))
        rank = next((i for i, v in enumerate(by_stake, 1) if v is me), 0)
        out.g("mersennet_validator_registered", 1, "The validator is registered", lbl)
        for key, name, help_ in [
            ("benched", "benched", "Benched this epoch"),
            ("exiting", "exiting", "Unregistering"),
            ("missedSlots", "missed_slots", "Leader slots missed this epoch"),
            ("proposedSlots", "proposed_slots", "Leader slots proposed this epoch"),
            ("totalProposed", "total_proposed", "Blocks proposed since registration"),
            ("jailedUntilEpoch", "jailed_until_epoch", "Jailed until this epoch"),
            ("timesJailed", "times_jailed", "Consecutive jails"),
            ("commissionBps", "commission_bps", "Commission, basis points"),
        ]:
            out.g(f"mersennet_validator_{name}", num(me.get(key, 0)), help_, lbl)
        out.g("mersennet_validator_self_stake_mrsn", num(me.get("selfStake", 0)) / WEI, "Self-stake, MRSN", lbl)
        out.g("mersennet_validator_delegated_mrsn", num(me.get("delegated", 0)) / WEI, "Delegated stake, MRSN", lbl)
        out.g("mersennet_validator_voting_stake_mrsn", num(me.get("votingStake", 0)) / WEI, "Voting stake, MRSN", lbl)
        out.g("mersennet_validator_rank", rank,
              "Position by voting stake among all validators (1 = largest)", lbl)
        out.g("mersennet_validator_in_active_set", int(IDENTITY in active), "In the active set this epoch", lbl)

    # Release of the queried node vs. the current official one.
    try:
        version = rpc(target, "mersennet_nodeIdentity").get("version", "")
    except Exception:  # noqa: BLE001 - some RPCs do not serve it
        version = ""
    rev = version.rsplit("-", 1)[-1] if "-" in version else ""
    latest = latest_rev()
    if rev:
        out.g("mersennet_node_version_info", 1, "Release of the queried node", L(version=version, rev=rev))
    if latest:
        out.g("mersennet_release_latest_info", 1, "Current official release (downloads/SHA256SUMS)", L(rev=latest))
    if rev and latest:
        out.g("mersennet_node_outdated", int(rev != latest),
              "1 when the queried node does not run the current release", L())


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        out = Out()
        if u.path == "/metrics":
            def one(source):
                part = Out()
                collect(part, *source)
                return part

            sources = [(LOCAL_RPC, {"source": "local"}), (PUBLIC_RPC, {"source": "public"})]
            with ThreadPoolExecutor(2) as pool:
                parts = list(pool.map(one, sources))
            for part in parts:  # merge, keeping one HELP/TYPE per metric
                for name, lines in part.series.items():
                    out.help.setdefault(name, part.help[name])
                    out.series.setdefault(name, []).extend(lines)
        elif u.path == "/probe":
            target = urllib.parse.parse_qs(u.query).get("target", [""])[0]
            collect(out, target, {})
        elif u.path == "/":
            self._send(200, b"mersennet-validator-monitoring: /metrics, /probe?target=<rpc>\n")
            return
        else:
            self._send(404, b"not found\n")
            return
        self._send(200, out.text().encode(), "text/plain; version=0.0.4")

    def _send(self, code, data, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quiet: one line per failed probe only
        pass


def main():
    if not re.fullmatch(r"0x[0-9a-f]{40}", IDENTITY):
        sys.exit("set MERSENNET_IDENTITY to the node identity (0x + 40 hex), see mersennet_nodeIdentity")
    host, port = LISTEN_ADDR.rsplit(":", 1)
    print(f"mersennet exporter for {IDENTITY} on {LISTEN_ADDR} (local {LOCAL_RPC}, public {PUBLIC_RPC})", flush=True)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
