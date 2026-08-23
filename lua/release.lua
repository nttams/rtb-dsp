-- release.lua — called on explicit loss.
-- KEYS: [1] reservation hash  [2] budget hash  [3] resv_index zset  [4] stats hash
-- ARGV: [1] reservation_id  [2] campaign_id
-- Returns: {ok, reason, released_micros}

local resv, budget, zidx, stats = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local rid, campaign_id = ARGV[1], ARGV[2]

if redis.call('EXISTS', resv) == 0 then
  return {0, 'NOT_FOUND', 0}
end

local state  = redis.call('HGET', resv, 'state')
local amount = tonumber(redis.call('HGET', resv, 'amount') or 0)

-- A release for an already-expired reservation is a no-op: the sweeper already returned
-- the budget, so decrementing again would double-release.
if state ~= 'reserved' then
  return {0, 'ALREADY_' .. tostring(state), 0}
end

redis.call('HINCRBY', budget, 'reserved', -amount)
hclamp0(budget, 'reserved')
redis.call('HSET', resv, 'state', 'released')
redis.call('ZREM', zidx, resv_member(rid, campaign_id, amount))
redis.call('HINCRBY', stats, 'losses', 1)

return {1, 'OK', amount}
