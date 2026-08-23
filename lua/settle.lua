-- settle.lua — called by the win-notification service.
-- KEYS: [1] reservation hash  [2] budget hash  [3] resv_index zset  [4] stats hash
-- ARGV: [1] reservation_id  [2] settled_price_micros_per_imp  [3] campaign_id  [4] amount_micros
-- Returns: {ok, reason, settled_micros}
--
-- UNIT CONTRACT (finding M1): ARGV[2] is PER-IMPRESSION, not CPM. OpenRTB bid.price is a
-- CPM, so the exchange must divide by 1000 before posting. The old name settled_price_micros
-- was ambiguous, and because step 3 clamps with min(), posting a CPM produced a numerically
-- correct result BY ACCIDENT -- hiding a 1000x error and making every win settle at exactly
-- the reserved amount, so the second-price auction had no observable effect at all.

local resv, budget, zidx, stats = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local rid = ARGV[1]
local settled_in = tonumber(ARGV[2])
local campaign_id = ARGV[3]

if redis.call('EXISTS', resv) == 0 then
  return {0, 'NOT_FOUND', 0}
end

local state  = redis.call('HGET', resv, 'state')
local amount = tonumber(redis.call('HGET', resv, 'amount') or 0)

-- Accept BOTH 'reserved' and 'expired' (finding M4). A win for an expired reservation means
-- the impression was GENUINELY SERVED. Refusing it under-records real money and silently
-- re-grants the budget -- and since neither store records the spend, the loss is undetectable
-- by construction and scales with tail latency. That is worse than an overspend.
if state ~= 'reserved' and state ~= 'expired' then
  return {0, 'ALREADY_' .. tostring(state), 0}
end

local settle = settled_in
if settle == nil or settle < 0 then settle = 0 end
local reason = 'OK'
if settle > amount then
  settle = amount
  -- Make the clamp OBSERVABLE (finding M1) so a unit mismatch announces itself.
  redis.call('HINCRBY', stats, 'clamped', 1)
  reason = 'OK_CLAMPED'
end

-- Decrement `reserved` ONLY when coming from 'reserved'; an expired reservation already had
-- its reserved decremented by the sweeper.
if state == 'reserved' then
  redis.call('HINCRBY', budget, 'reserved', -amount)
  hclamp0(budget, 'reserved')
end
redis.call('HINCRBY', budget, 'spent', settle)

redis.call('HSET', resv, 'state', 'settled', 'settled', settle)
redis.call('ZREM', zidx, resv_member(rid, campaign_id, amount))
redis.call('HINCRBY', stats, 'wins', 1)

return {1, reason, settle}
