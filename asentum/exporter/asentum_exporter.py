#!/usr/bin/env python3
"""Prometheus exporter for an Asentum validator.

The Asentum node has no /metrics. This exporter reads its HTTP API and the
public one and exports the node (height, finality, block age, lag behind the
network) and, for one validator address: bond, status, committee state and
membership, misses, participation and wallet balance.

Standard library only. Configuration through environment variables:

  ASENTUM_ADDRESS     validator address (ase1... or 0x...)       required
  ASENTUM_LOCAL_RPC   your node's RPC     default http://127.0.0.1:8545
  ASENTUM_PUBLIC_RPC  reference RPC       default https://testnet.asentum.com
  LISTEN_ADDR         host:port to serve  default 0.0.0.0:7980

The validator set and the committee are read from the public RPC, so they stay
visible while your node is down; participation is read from your node (it scans
the last 1000 blocks, which is heavy to ask of a public RPC every minute).
"""
import json
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADDRESS = os.environ.get("ASENTUM_ADDRESS", "").strip().lower()
LOCAL_RPC = os.environ.get("ASENTUM_LOCAL_RPC", "http://127.0.0.1:8545").rstrip("/")
PUBLIC_RPC = os.environ.get("ASENTUM_PUBLIC_RPC", "https://testnet.asentum.com").rstrip("/")
LISTEN_ADDR = os.environ.get("LISTEN_ADDR", "0.0.0.0:7980")

WEI = 10**18
USER_AGENT = "asentum-validator-monitoring/1.0"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def bech32_to_hex(addr):
    """ase1... -> 0x + 40 hex (the same 20-byte account). The checksum is not verified."""
    data = [BECH32.index(c) for c in addr[addr.rindex("1") + 1:-6]]
    acc, bits, out = 0, 0, bytearray()
    for v in data:
        acc = (acc << 5) | v
        bits += 5
        while bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return "0x" + out.hex()


def hex_to_bech32(hex_addr, hrp="ase"):
    def polymod(values):
        gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
        chk = 1
        for v in values:
            top = chk >> 25
            chk = (chk & 0x1FFFFFF) << 5 ^ v
            for i in range(5):
                chk ^= gen[i] if (top >> i) & 1 else 0
        return chk

    acc, bits, data = 0, 0, []
    for b in bytes.fromhex(hex_addr[2:]):
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    expand = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    pm = polymod(expand + data + [0] * 6) ^ 1
    checksum = [(pm >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(BECH32[d] for d in data + checksum)


def num(v):
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return int(v, 16) if v.startswith("0x") else float(v)
    return v


def get(base, path):
    req = urllib.request.Request(base + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def rpc(base, method, params):
    req = urllib.request.Request(
        base,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["result"]


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


def fetch(base, path):
    try:
        return get(base, path)
    except Exception as e:  # noqa: BLE001 - a failed call only drops its metrics
        print(f"{base}{path} failed: {e}", file=sys.stderr, flush=True)
        return None


def collect(addr_hex, addr_bech):
    calls = {
        "ready": (LOCAL_RPC, "/ready"),
        "chain": (LOCAL_RPC, "/chain"),
        "heads": (LOCAL_RPC, "/v3/heads"),
        "participation": (LOCAL_RPC, "/participation"),
        "net_heads": (PUBLIC_RPC, "/v3/heads"),
        "validators": (PUBLIC_RPC, "/validators"),
        "committee": (PUBLIC_RPC, "/v3/committee"),
    }
    with ThreadPoolExecutor(len(calls)) as pool:
        futures = {k: pool.submit(fetch, *v) for k, v in calls.items()}
        r = {k: f.result() for k, f in futures.items()}

    out = Out()
    ready, chain, heads, net = r["ready"], r["chain"], r["heads"], r["net_heads"]

    out.g("asentum_node_up", int(ready is not None), "The node answered /ready")
    if ready is not None:
        out.g("asentum_node_ready", int(ready.get("status") == "ready"), "The node reports itself ready")
        out.g("asentum_node_last_block_age_seconds", num(ready.get("lastBlockAgeSec")),
              "Seconds since the node's last block")
    if chain is not None:
        out.g("asentum_node_height", num(chain.get("height")), "Height of the node")
        out.g("asentum_node_mempool_size", num(chain.get("mempoolSize")), "Transactions in the node's mempool")
    if heads is not None:
        out.g("asentum_node_tip", num(heads.get("tip")), "Tip of the node (/v3/heads)")
        out.g("asentum_node_finalized", num(heads.get("finalized")), "Finalized height of the node")

    out.g("asentum_network_up", int(net is not None), "The public RPC answered /v3/heads")
    if net is not None:
        out.g("asentum_network_tip", num(net.get("tip")), "Tip of the network (public RPC)")
        out.g("asentum_network_finalized", num(net.get("finalized")), "Finalized height of the network")

    committee = r["committee"]
    members = [a.lower() for a in (committee or {}).get("committee", [])]
    if committee is not None:
        out.g("asentum_committee_epoch", num(committee.get("epoch")), "Current epoch")
        out.g("asentum_committee_size", len(members), "Validators in the committee this epoch")

    vals = (r["validators"] or {}).get("validators")
    if vals is not None:
        out.g("asentum_validators_bonded", len(vals), "Bonded validators")
        me = next((v for v in vals if v.get("address", "").lower() in (addr_bech, addr_hex)), None)
        out.g("asentum_validator_registered", int(me is not None), "The validator is bonded")
        if me is not None:
            lbl = {"address": addr_bech, "status": me.get("status", ""),
                   "committee_state": me.get("committeeState", "")}
            out.g("asentum_validator_info", 1, "Status and committee state of the validator", lbl)
            out.g("asentum_validator_active", int(me.get("status") == "active"), "Bond status is active")
            out.g("asentum_validator_stake_ase", num(me.get("stake")) / WEI, "Bonded stake, ASE")
            out.g("asentum_validator_bonded_at", num(me.get("bondedAt")), "Height of the bond")
            out.g("asentum_validator_consecutive_misses", num(me.get("consecutiveMisses")),
                  "Consecutive missed votes")
            out.g("asentum_validator_last_voted_height", num(me.get("lastVotedHeight")),
                  "Height of the last vote (0 = never voted)")
            out.g("asentum_validator_next_committee_entry_at", num(me.get("nextCommitteeEntryAt")),
                  "Height of the next committee entry attempt (0 = none pending)")
    if committee is not None:
        out.g("asentum_validator_in_committee", int(addr_hex in members), "In the committee this epoch")

    part = r["participation"]
    if part is not None:
        out.g("asentum_participation_lookback", num(part.get("lookback")), "Blocks scanned for participation")
        for key in ("signing", "idle", "dormant"):
            out.g("asentum_validators_by_participation", num(part.get(key)),
                  "Validators by participation status", {"status": key})
        mine = next((v for v in part.get("validators", []) if v.get("address", "").lower() == addr_hex), None)
        if mine is not None:
            out.g("asentum_validator_signed_blocks", num(mine.get("signedBlocks")),
                  "Blocks signed in the participation lookback")
            out.g("asentum_validator_participation_ratio", num(mine.get("participationRatio")),
                  "Signed share of the participation lookback")
            out.g("asentum_validator_last_signed_block", num(mine.get("lastSignedBlock")),
                  "Height of the last signed block (0 = none)")

    try:
        bal = num(rpc(PUBLIC_RPC, "eth_getBalance", [addr_bech, "latest"]))
        out.g("asentum_validator_balance_ase", bal / WEI, "Wallet balance outside the bond, ASE")
    except Exception as e:  # noqa: BLE001
        print(f"eth_getBalance failed: {e}", file=sys.stderr, flush=True)
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            data = collect(self.server.addr_hex, self.server.addr_bech).text().encode()
            self._send(200, data, "text/plain; version=0.0.4")
        elif self.path == "/":
            self._send(200, b"asentum-validator-monitoring: /metrics\n")
        else:
            self._send(404, b"not found\n")

    def _send(self, code, data, ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # quiet: failed calls are logged in fetch()
        pass


def main():
    if re.fullmatch(r"0x[0-9a-f]{40}", ADDRESS):
        addr_hex, addr_bech = ADDRESS, hex_to_bech32(ADDRESS)
    elif re.fullmatch(r"ase1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{38}", ADDRESS):
        addr_bech, addr_hex = ADDRESS, bech32_to_hex(ADDRESS)
    else:
        sys.exit("set ASENTUM_ADDRESS to the validator address (ase1... or 0x...), see the node log")
    host, port = LISTEN_ADDR.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port)), Handler)
    server.addr_hex, server.addr_bech = addr_hex, addr_bech
    print(f"asentum exporter for {addr_bech} ({addr_hex}) on {LISTEN_ADDR} "
          f"(local {LOCAL_RPC}, public {PUBLIC_RPC})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
