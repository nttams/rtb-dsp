-- reserve.lua — called by the Rust engine at bid time.
-- KEYS: [1] budget hash  [2] reservation hash  [3] resv_index zset  [4] stats hash
-- ARGV: [1] amount_micros  [2] reservation_id  [3] bid_id  [4] campaign_id  [5] ttl_ms
-- Returns: {ok(0|1), reason, available_after}

local budget, resv, zidx, stats = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local amount = tonumber(ARGV[1])
local rid, bid_id, campaign_id, ttl_ms = ARGV[2], ARGV[3], ARGV[4], tonumber(ARGV[5])

-- 1. Replay guard (finding M2). reserve was the only script in the family without one.
-- A client-side timeout on a call the server actually executed, plus any retry with the
-- same reservation_id, would double-increment `reserved` while the hash recorded `amount`
-- once. settle then decrements once and the surplus is stranded permanently, invisible to
-- the sweeper, clearing only on a Redis restart.
if redis.call('EXISTS', resv) == 1 then
  return {1, 'REPLAY', num(budget, 'allocated') - num(budget, 'reserved') - num(budget, 'spent')}
end

-- 2. Reject non-positive amounts (finding M6). The original guard was `available < amount`,
-- which for amount=0 is `0 < 0` -> false -> the reserve SUCCEEDS against a zero-budget
-- campaign, forever. Reachable through the CRUD form: bid_cpm_micros only has CHECK (> 0),
-- so any CPM below $0.001 truncates to 0 per-impression.
if amount == nil or amount <= 0 then
  return {0, 'BAD_AMOUNT', 0}
end

if redis.call('EXISTS', budget) == 0 then
  redis.call('HINCRBY', stats, 'nobid_budget', 1)
  return {0, 'NO_BUDGET', 0}
end

local allocated = num(budget, 'allocated')
local reserved  = hclamp0(budget, 'reserved')
local spent     = hclamp0(budget, 'spent')
local available = allocated - reserved - spent
if available < 0 then available = 0 end

if available < amount then
  redis.call('HINCRBY', stats, 'nobid_budget', 1)
  return {0, 'INSUFFICIENT', available}
end

local t = now_ms()
local expires_at = t + ttl_ms

redis.call('HINCRBY', budget, 'reserved', amount)
redis.call('HSET', resv,
  'campaign_id', campaign_id, 'amount', amount, 'bid_id', bid_id,
  'state', 'reserved', 'expires_at_ms', expires_at)
-- No PEXPIRE (finding M3). A TTL on this hash would delete it without decrementing
-- `reserved`; the ZSET member carries everything reclaim needs instead.
redis.call('ZADD', zidx, expires_at, resv_member(rid, campaign_id, amount))
redis.call('HINCRBY', stats, 'bids', 1)

return {1, 'OK', available - amount}
