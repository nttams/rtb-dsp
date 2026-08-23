-- reclaim_expired.lua — called by the topup worker on a short interval (default 1s).
-- KEYS: [1] resv_index zset          <- only genuine keys are declared (KEYS-contract fix)
-- ARGV: [1] batch_limit  [2] cluster
-- Returns: {reclaimed_count, reclaimed_micros}
--
-- Per-campaign budget/stats keys are built in-script from `cluster`. The original contract
-- passed key PREFIXES in KEYS[2]/KEYS[3] -- which are not keys, a lie to Redis that blocks any
-- future Cluster move -- while the reservation hash it actually read appeared in neither.
-- Standalone-Redis assumption recorded in docs/rtbkit-port-notes.md.

local zidx = KEYS[1]
local limit = tonumber(ARGV[1]) or 500
local cluster = ARGV[2]

local t = now_ms()
local expired = redis.call('ZRANGEBYSCORE', zidx, '-inf', t, 'LIMIT', 0, limit)

local count, total = 0, 0
for i = 1, #expired do
  local member = expired[i]
  -- Parse campaign_id and amount from the MEMBER, never from the hash (finding M3).
  -- The hash may be gone; that is now harmless.
  local rid, campaign_id, amount = parse_member(member)
  if rid then
    local budget = 'c:' .. cluster .. ':camp:' .. campaign_id .. ':budget'
    local stats  = 'c:' .. cluster .. ':camp:' .. campaign_id .. ':stats'
    local resv   = 'c:' .. cluster .. ':resv:' .. rid

    -- Only reclaim if still 'reserved'. If the hash is missing we cannot know, so we trust
    -- the ZSET: presence in the index means settle/release never removed it.
    local state = redis.call('HGET', resv, 'state')
    if state == nil or state == false or state == 'reserved' then
      redis.call('HINCRBY', budget, 'reserved', -amount)
      hclamp0(budget, 'reserved')
      if redis.call('EXISTS', resv) == 1 then
        redis.call('HSET', resv, 'state', 'expired')
      end
      redis.call('HINCRBY', stats, 'expired', 1)
      count = count + 1
      total = total + amount
    end
    redis.call('ZREM', zidx, member)
  else
    redis.call('ZREM', zidx, member)  -- unparseable member: drop it rather than loop forever
  end
end

return {count, total}
