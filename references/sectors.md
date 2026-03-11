# Spellbook Sector Reference

## DEX (dex subproject)

**Core tables:**
| Table | Description | Key Columns | Partition |
|-------|-------------|-------------|-----------|
| `dex.trades` | All DEX trades across chains | blockchain, project, version, block_time, token_pair, amount_usd, tx_hash | block_month |
| `dex.pools` | Liquidity pool metadata | blockchain, project, pool, token0, token1 | - |
| `dex.prices` | DEX-derived token prices | blockchain, token_address, price, block_time | block_month |

**Supported DEX projects (40+):** Uniswap (v2/v3), Curve, Balancer, SushiSwap, PancakeSwap, Trader Joe, Camelot, Aerodrome, Velodrome, GMX, dYdX, Maverick, Ambient, etc.

**Supported chains (40+):** ethereum, arbitrum, optimism, base, polygon, bnb, avalanche_c, gnosis, fantom, celo, zksync, scroll, linea, blast, mantle, mode, zora, etc.

**Key relationships:**
```
dex.trades (core fact table)
  ├── JOIN prices.usd → USD values
  ├── JOIN tokens.erc20 → token metadata/decimals
  └── JOIN labels.all → trader identity

dex.pools
  └── JOIN tokens.erc20 → pool token metadata
```

**Common analyses:**
- Daily/weekly DEX volume by chain/protocol
- Top trading pairs by volume
- Unique traders over time
- Market share across DEXes
- Arbitrage detection (roundtrip_trades)
- Multi-hop trade analysis

---

## NFT (nft subproject)

**Core tables:**
| Table | Description | Key Columns | Partition |
|-------|-------------|-------------|-----------|
| `nft.trades` | All NFT marketplace trades | blockchain, project, nft_contract_address, token_id, amount_usd, buyer, seller | block_month |
| `nft.mints` | NFT minting events | blockchain, nft_contract_address, token_id, minter | block_month |
| `nft.transfers` | NFT transfer events | blockchain, token_id, from, to | block_month |

**Supported marketplaces:** OpenSea (Seaport), Blur, LooksRare, X2Y2, Sudoswap, Reservoir, Element, etc.

**Common analyses:**
- Collection floor price trends
- Top collections by volume
- Wash trading detection
- Holder distribution
- Mint-to-list time analysis

---

## Tokens (tokens subproject)

**Core tables:**
| Table | Description | Key Columns | Partition |
|-------|-------------|-------------|-----------|
| `tokens.erc20` | ERC20 token metadata | blockchain, contract_address, symbol, decimals | - |
| `tokens.nft` | NFT collection metadata | blockchain, contract_address, name, standard | - |
| `tokens.transfers` | All token transfers | blockchain, contract_address, from, to, amount_raw | block_date |
| `prices.usd` | Token prices (minute-level) | blockchain, contract_address, minute, price, decimals | minute |
| `prices.usd_daily` | Token prices (daily) | blockchain, contract_address, day, price | day |

**Critical: prices.usd join pattern:**
```sql
-- Always truncate block_time to minute for price joins
LEFT JOIN prices.usd p
  ON p.blockchain = t.blockchain
  AND p.contract_address = t.token_address
  AND p.minute = DATE_TRUNC('minute', t.block_time)
```

---

## Lending (daily_spellbook → _sector/lending)

**Core tables:**
| Table | Description | Key Columns |
|-------|-------------|-------------|
| `lending.borrow` | Borrow events | blockchain, project, token_address, amount, borrower |
| `lending.supply` | Supply/deposit events | blockchain, project, token_address, amount, depositor |
| `lending.liquidations` | Liquidation events | blockchain, project, liquidated_user, collateral |

**Supported projects:** Aave (v2/v3), Compound (v2/v3), Maker, Spark, Morpho, Radiant, etc.

---

## Gas (daily_spellbook → _sector/gas)

**Core tables:**
| Table | Description | Key Columns |
|-------|-------------|-------------|
| `gas.fees` | Gas fee data | blockchain, block_time, tx_hash, gas_price, gas_used, tx_fee |

**Common analyses:**
- Average gas price trends
- Gas usage by contract/protocol
- EIP-1559 base fee vs priority fee
- Layer 2 gas savings comparison

---

## Labels (daily_spellbook → _sector/labels)

**Core table:** `labels.all`
| Column | Description |
|--------|-------------|
| blockchain | Chain |
| address | Labeled address |
| name | Entity name |
| category | Category (dex, cex, defi, contract, etc.) |
| contributor | Who added the label |
| model_name | Source model |

**Use cases:** Enrich any address-based query with entity identification.

---

## Bridges (daily_spellbook → _sector/bridges)

**Core tables:**
| Table | Description |
|-------|-------------|
| `bridges.flows` | Cross-chain bridge transfers |

**Common analyses:**
- Bridge volume by chain pair
- Bridge TVL trends
- Bridge market share

---

## Project-Specific Schemas

Major protocols have dedicated schemas with detailed models:

| Project | Schema Pattern | Key Tables |
|---------|---------------|------------|
| Uniswap | `uniswap_v3_<chain>.*` | pool_created, swaps, positions |
| Aave | `aave_v3_<chain>.*` | supply, borrow, repay, liquidation |
| Lido | `lido_ethereum.*` | staking, withdrawals |
| Compound | `compound_v3_<chain>.*` | supply, borrow, liquidation |
| Maker | `maker_ethereum.*` | vaults, dai_supply |
| Curve | `curve_<chain>.*` | pools, swaps, gauges |
| Balancer | `balancer_v2_<chain>.*` | pools, swaps, joins_exits |
| ENS | `ens_ethereum.*` | registrations, renewals |
| Safe | `safe_<chain>.*` | transactions, creations |

**Discovery pattern:** Use `mcp__dune__searchTables` with `schemas: ["uniswap_v3_ethereum"]` to find all tables for a specific project+chain.

---

## Solana-Specific (solana subproject)

| Table | Description |
|-------|-------------|
| `solana.transactions` | All Solana transactions |
| `solana.account_activity` | Account state changes |
| `jupiter_solana.*` | Jupiter DEX aggregator |
| `tokens_solana.*` | SPL token metadata |

**Note:** Solana uses different address format (base58) and token standard (SPL). Amount decimals vary per token.
