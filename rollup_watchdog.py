# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
RollupWatchdog — consensus-backed L2 health oracle.

Anyone can trigger an assessment of a configured L2 rollup. Validators
independently fetch LIVE signals (latest-block freshness + the chain's
official status page), an LLM synthesizes them into a verdict, and GenLayer's
equivalence principle drives consensus on that verdict. The latest consensus
verdict per chain is stored on-chain, so wallets, bridges and dashboards can
consume it without trusting any single party.

Statuses: HEALTHY | DEGRADED | HALTED  (stored as strings)

Sprint notes (verify in GenLayer Studio before main deploy):
  - Etherscan V2 multichain endpoint used for block freshness:
    https://api.etherscan.io/v2/api?chainid=<id>&module=proxy&action=eth_getBlockByNumber&tag=latest&boolean=false
    Works keyless at low volume; append &apikey=<free key> if rate-limited.
  - status_url entries are statuspage.io public APIs (/api/v2/status.json needs
    no auth). Leave "" for chains with no status page — the contract skips it
    and the prompt weighs the remaining signals.
"""

import json

from genlayer import *

# Chains are configured at deploy time (constructor arg) so the contract is a
# reusable oracle framework, not a hardcoded 3-chain demo. Example:
#
# [
#   {"id": "arbitrum-one", "name": "Arbitrum One",
#    "etherscan_chain_id": 42161,
#    "status_url": "https://status.arbitrum.io/api/v2/status.json"},
#   {"id": "base", "name": "Base",
#    "etherscan_chain_id": 8453,
#    "status_url": ""},
#   {"id": "zksync-era", "name": "ZKsync Era",
#    "etherscan_chain_id": 324,
#    "status_url": ""}
# ]
#
# >>> VERIFY each status_url in Studio: it must return JSON (HTTP 200). <<<

VALID_STATUSES = ("HEALTHY", "DEGRADED", "HALTED")


def _validate_chain_config(c: dict) -> None:
    for key in ("id", "name", "etherscan_chain_id"):
        if key not in c:
            raise gl.UserError(f"Chain config missing required key: {key}")
    if not isinstance(c["id"], str) or not c["id"]:
        raise gl.UserError("Chain 'id' must be a non-empty string")


class RollupWatchdog(gl.Contract):
    owner: Address
    # Canonical JSON list of chain configs (see example above).
    chains_json: str
    # chain_id -> canonical JSON: {"status", "summary", "block_number",
    #                              "block_age_seconds", "assessed_at_block"}
    assessments: TreeMap[str, str]
    assessment_count: u256

    def __init__(self, chains_json: str):
        try:
            chains = json.loads(chains_json)
        except Exception:
            raise gl.UserError(
                "chains_json must be a JSON list of chain configs, e.g. "
                '[{"id": "arbitrum-one", "name": "Arbitrum One", '
                '"etherscan_chain_id": 42161, "status_url": ""}]'
            )
        if not isinstance(chains, list) or not chains:
            raise gl.UserError("chains_json must be a non-empty JSON list")
        seen = set()
        for c in chains:
            _validate_chain_config(c)
            if c["id"] in seen:
                raise gl.UserError(f"Duplicate chain id: {c['id']}")
            seen.add(c["id"])
        self.owner = gl.message.sender_address
        self.chains_json = json.dumps(chains, sort_keys=True)
        self.assessment_count = 0

    def _get_chain(self, chain_id: str) -> dict:
        """Deterministic lookup (runs outside nondet blocks, may touch storage)."""
        for c in json.loads(self.chains_json):
            if c["id"] == chain_id:
                return c
        raise gl.UserError(f"Unknown chain: {chain_id}")

    @gl.public.write
    def assess(self, chain_id: str) -> str:
        # Resolve config deterministically BEFORE entering the nondet block:
        # nondet functions cannot access storage.
        cfg = self._get_chain(chain_id)
        chain_name = cfg["name"]
        etherscan_cid = cfg["etherscan_chain_id"]
        status_url = cfg.get("status_url", "")

        def run_assessment() -> str:
            # ---- Live signal 1: latest block freshness (chain liveness) ----
            block_raw = gl.nondet.web.get(
                "https://api.etherscan.io/v2/api"
                f"?chainid={etherscan_cid}"
                "&module=proxy&action=eth_getBlockByNumber"
                "&tag=latest&boolean=false"
            )
            block = json.loads(block_raw)["result"]
            block_number = int(block["number"], 16)
            block_timestamp = int(block["timestamp"], 16)

            # ---- Live signal 2: official status page (optional) ----
            status_page = "UNAVAILABLE"
            if status_url:
                try:
                    status_page = gl.nondet.web.get(status_url)[:3000]
                except Exception:
                    status_page = "UNAVAILABLE (fetch failed)"

            prompt = (
                "You are a blockchain reliability analyst. Assess the health of "
                f"the L2 rollup '{chain_name}' from these LIVE signals.\n"
                f"Signal 1 - latest block: number {block_number}, unix timestamp "
                f"{block_timestamp}. Compare with the current time: a healthy L2 "
                "produces blocks every few seconds. A gap of many minutes can mean "
                "an outage, but at quiet hours it can also be low demand; weigh "
                "the evidence accordingly.\n"
                f"Signal 2 - official status page JSON: {status_page}\n"
                "Return ONLY a JSON object with exactly these keys: "
                '{"status": "HEALTHY|DEGRADED|HALTED", '
                '"summary": "<max 2 sentences, plain language>", '
                '"block_number": <int>, "block_age_seconds": <int>}. '
                "Rules: HALTED only if blocks have clearly stopped for an abnormal "
                "period or the status page reports a major outage. DEGRADED if a "
                "minor incident is reported or block production looks slow. "
                "Otherwise HEALTHY. Never invent data; if a signal is missing, "
                "say so in the summary."
            )
            verdict = json.loads(
                gl.nondet.exec_prompt(prompt, response_format="json")
            )
            if verdict.get("status") not in VALID_STATUSES:
                raise gl.UserError("Model returned an invalid status")
            return json.dumps(verdict, sort_keys=True)

        # Validators re-run the assessment; consensus requires the verdict to
        # match. prompt_comparative (not strict_eq) is used because fetch timing
        # and summary wording legitimately differ between validators.
        result = gl.eq_principle.prompt_comparative(
            run_assessment,
            "The 'status' field must match exactly (HEALTHY, DEGRADED or HALTED). "
            "Summaries may differ in wording but must describe the same key facts "
            "and cite the same signals.",
        )
        self.assessments[chain_id] = result
        self.assessment_count += 1
        return result

    @gl.public.view
    def get_assessment(self, chain_id: str) -> str:
        """Latest consensus verdict for a chain, or empty string if never assessed."""
        try:
            return self.assessments[chain_id]
        except KeyError:
            return ""

    @gl.public.view
    def list_chains(self) -> str:
        """Configured chains (canonical JSON list)."""
        return self.chains_json

    @gl.public.write
    def add_chain(
        self, chain_id: str, name: str, etherscan_chain_id: int, status_url: str
    ) -> str:
        """Owner-only: register a new chain so the oracle stays reusable."""
        if gl.message.sender_address != self.owner:
            raise gl.UserError("Only the owner can add chains")
        chains = json.loads(self.chains_json)
        if any(c["id"] == chain_id for c in chains):
            raise gl.UserError(f"Chain already registered: {chain_id}")
        new_chain = {
            "id": chain_id,
            "name": name,
            "etherscan_chain_id": etherscan_chain_id,
            "status_url": status_url,
        }
        _validate_chain_config(new_chain)
        chains.append(new_chain)
        self.chains_json = json.dumps(chains, sort_keys=True)
        return self.chains_json
