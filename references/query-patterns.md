# Dune Query Patterns & Templates

## DuneSQL Quick Reference

DuneSQL is Trino-based. Key differences from standard SQL:

```sql
-- Time functions
DATE_TRUNC('day', block_time)           -- not DATE(block_time)
CURRENT_DATE - INTERVAL '7' DAY        -- not DATE_SUB()
FROM_UNIXTIME(unix_ts)                  -- Unix timestamp → timestamp
DATE_FORMAT(ts, '%Y-%m-%d')             -- format timestamp

-- Hex/Address
FROM_HEX('deadbeef')                    -- hex string → varbinary
TO_HEX(bytes_col)                       -- varbinary → hex string
LOWER(CAST(address AS VARCHAR))         -- normalize address

-- Aggregation
APPROX_DISTINCT(col)                    -- faster COUNT(DISTINCT col)
APPROX_PERCENTILE(col, 0.5)            -- median
ARRAY_AGG(col ORDER BY x)              -- ordered array

-- Casting
TRY_CAST(x AS BIGINT)                  -- returns NULL on failure
CAST(amount AS DOUBLE)                  -- strict cast

-- Byte manipulation
BYTEARRAY_SUBSTRING(data, 1, 4)        -- extract bytes
BYTEARRAY_TO_UINT256(bytes_col)        -- bytes → uint256
```

---

## Template: DEX Volume Analysis

```sql
-- Daily DEX volume by protocol (last 30 days)
SELECT
    DATE_TRUNC('day', block_time) AS day,
    project,
    COUNT(*) AS trade_count,
    SUM(amount_usd) AS volume_usd,
    APPROX_DISTINCT(taker) AS unique_traders
FROM dex.trades
WHERE blockchain = '{{blockchain}}'
  AND block_date >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY 1, 2
ORDER BY 1 DESC, 4 DESC
```

## Template: Top Trading Pairs

```sql
-- Top 20 trading pairs by volume (last 7 days)
SELECT
    token_pair,
    COUNT(*) AS trades,
    SUM(amount_usd) AS volume_usd,
    APPROX_DISTINCT(taker) AS unique_traders
FROM dex.trades
WHERE blockchain = '{{blockchain}}'
  AND block_date >= CURRENT_DATE - INTERVAL '7' DAY
  AND amount_usd > 0
GROUP BY 1
ORDER BY 3 DESC
LIMIT 20
```

## Template: Token Transfer Analysis

```sql
-- Large transfers of a specific token (last 7 days)
SELECT
    block_time,
    "from",
    "to",
    CAST(amount_raw AS DOUBLE) / POWER(10, tok.decimals) AS amount,
    tx_hash
FROM tokens.transfers t
JOIN tokens.erc20 tok
  ON tok.blockchain = t.blockchain
  AND tok.contract_address = t.contract_address
WHERE t.blockchain = '{{blockchain}}'
  AND t.contract_address = {{token_address}}
  AND t.block_date >= CURRENT_DATE - INTERVAL '7' DAY
ORDER BY amount DESC
LIMIT 100
```

## Template: Wallet Portfolio

```sql
-- Current token balances for a wallet
WITH transfers AS (
    SELECT
        contract_address,
        SUM(CASE WHEN "to" = {{wallet}} THEN CAST(amount_raw AS DOUBLE)
                 WHEN "from" = {{wallet}} THEN -CAST(amount_raw AS DOUBLE)
                 ELSE 0 END) AS net_amount_raw
    FROM tokens.transfers
    WHERE blockchain = '{{blockchain}}'
      AND ("to" = {{wallet}} OR "from" = {{wallet}})
    GROUP BY 1
    HAVING SUM(CASE WHEN "to" = {{wallet}} THEN CAST(amount_raw AS DOUBLE)
                    WHEN "from" = {{wallet}} THEN -CAST(amount_raw AS DOUBLE)
                    ELSE 0 END) > 0
)
SELECT
    tok.symbol,
    t.net_amount_raw / POWER(10, tok.decimals) AS balance,
    p.price,
    t.net_amount_raw / POWER(10, tok.decimals) * p.price AS value_usd
FROM transfers t
JOIN tokens.erc20 tok
  ON tok.blockchain = '{{blockchain}}'
  AND tok.contract_address = t.contract_address
LEFT JOIN prices.usd_daily p
  ON p.blockchain = '{{blockchain}}'
  AND p.contract_address = t.contract_address
  AND p.day = CURRENT_DATE - INTERVAL '1' DAY
ORDER BY value_usd DESC
```

## Template: NFT Collection Stats

```sql
-- NFT collection performance (last 30 days)
SELECT
    DATE_TRUNC('day', block_time) AS day,
    COUNT(*) AS sales,
    SUM(amount_usd) AS volume_usd,
    APPROX_PERCENTILE(amount_usd, 0.5) AS median_price_usd,
    MIN(amount_usd) AS floor_usd,
    APPROX_DISTINCT(buyer) AS unique_buyers
FROM nft.trades
WHERE blockchain = '{{blockchain}}'
  AND nft_contract_address = {{collection_address}}
  AND block_date >= CURRENT_DATE - INTERVAL '30' DAY
  AND amount_usd > 0
GROUP BY 1
ORDER BY 1
```

## Template: Protocol Revenue / Fees

```sql
-- Daily protocol fees (DEX example)
SELECT
    DATE_TRUNC('day', block_time) AS day,
    project,
    SUM(amount_usd * 0.003) AS estimated_fees_usd  -- adjust fee rate per protocol
FROM dex.trades
WHERE blockchain = '{{blockchain}}'
  AND project = '{{project}}'
  AND block_date >= CURRENT_DATE - INTERVAL '30' DAY
GROUP BY 1, 2
ORDER BY 1
```

## Template: Gas Analysis

```sql
-- Average gas price trends by chain
SELECT
    DATE_TRUNC('hour', block_time) AS hour,
    AVG(gas_price / 1e9) AS avg_gas_gwei,
    APPROX_PERCENTILE(gas_price / 1e9, 0.5) AS median_gas_gwei,
    APPROX_PERCENTILE(gas_price / 1e9, 0.95) AS p95_gas_gwei
FROM gas.fees
WHERE blockchain = '{{blockchain}}'
  AND block_date >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY 1
ORDER BY 1
```

## Template: Cross-Chain Comparison

```sql
-- Daily active addresses across chains
SELECT
    blockchain,
    DATE_TRUNC('day', block_time) AS day,
    APPROX_DISTINCT(taker) AS unique_traders,
    SUM(amount_usd) AS volume_usd
FROM dex.trades
WHERE block_date >= CURRENT_DATE - INTERVAL '30' DAY
  AND blockchain IN ('ethereum', 'arbitrum', 'base', 'optimism', 'polygon')
GROUP BY 1, 2
ORDER BY 2, 4 DESC
```

## Template: Whale Tracking

```sql
-- Top wallets by DEX volume (last 7 days)
SELECT
    taker AS wallet,
    l.name AS label,
    COUNT(*) AS trade_count,
    SUM(amount_usd) AS total_volume_usd,
    APPROX_DISTINCT(token_pair) AS pairs_traded
FROM dex.trades t
LEFT JOIN labels.all l
  ON l.blockchain = t.blockchain AND l.address = t.taker
WHERE t.blockchain = '{{blockchain}}'
  AND t.block_date >= CURRENT_DATE - INTERVAL '7' DAY
GROUP BY 1, 2
ORDER BY 4 DESC
LIMIT 50
```

## Template: Lending Protocol Overview

```sql
-- Lending supply and borrow by token (current state approximation)
SELECT
    token_symbol,
    SUM(CASE WHEN evt_type = 'supply' THEN amount_usd ELSE 0 END) AS total_supplied_usd,
    SUM(CASE WHEN evt_type = 'borrow' THEN amount_usd ELSE 0 END) AS total_borrowed_usd
FROM (
    SELECT token_symbol, amount_usd, 'supply' AS evt_type
    FROM lending.supply
    WHERE blockchain = '{{blockchain}}' AND project = '{{project}}'
      AND block_date >= CURRENT_DATE - INTERVAL '30' DAY
    UNION ALL
    SELECT token_symbol, amount_usd, 'borrow' AS evt_type
    FROM lending.borrow
    WHERE blockchain = '{{blockchain}}' AND project = '{{project}}'
      AND block_date >= CURRENT_DATE - INTERVAL '30' DAY
) combined
GROUP BY 1
ORDER BY 2 DESC
```

---

## Anti-Patterns to Avoid

| Bad | Good | Why |
|-----|------|-----|
| `COUNT(DISTINCT address)` | `APPROX_DISTINCT(address)` | 10x faster, <2% error |
| `WHERE block_time > NOW() - INTERVAL '7' DAY` | `WHERE block_date >= CURRENT_DATE - INTERVAL '7' DAY` | Must filter on partition column |
| `JOIN prices.usd ON minute = block_time` | `JOIN prices.usd ON minute = DATE_TRUNC('minute', block_time)` | block_time has seconds |
| `SELECT * FROM ethereum.transactions` | Add `WHERE block_date >= ...` and select specific columns | Full scan = timeout + expensive |
| `CAST(amount AS DECIMAL)` | `CAST(amount AS DOUBLE)` | DuneSQL prefers DOUBLE |
| Subquery in WHERE with large table | Use CTE or JOIN instead | Performance |
