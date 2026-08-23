-- dashboard_snapshot.lua — called by the campaign app on each dashboard render.
-- KEYS: [1] c:{cluster}:campaigns set
-- ARGV: [1] cluster
-- Returns: flat array, 10 entries per campaign:
--   campaign_id, allocated, reserved, spent, bids, wins, losses, nobid_budget, expired, clamped
-- One round trip for all 100 campaigns instead of 200 (Risk R8).

local campaigns = KEYS[1]
local cluster = ARGV[1]

local ids = redis.call('SMEMBERS', campaigns)
local out = {}
for i = 1, #ids do
  local id = ids[i]
  local b = 'c:' .. cluster .. ':camp:' .. id .. ':budget'
  local s = 'c:' .. cluster .. ':camp:' .. id .. ':stats'
  out[#out+1] = id
  out[#out+1] = tostring(redis.call('HGET', b, 'allocated') or 0)
  out[#out+1] = tostring(redis.call('HGET', b, 'reserved') or 0)
  out[#out+1] = tostring(redis.call('HGET', b, 'spent') or 0)
  out[#out+1] = tostring(redis.call('HGET', s, 'bids') or 0)
  out[#out+1] = tostring(redis.call('HGET', s, 'wins') or 0)
  out[#out+1] = tostring(redis.call('HGET', s, 'losses') or 0)
  out[#out+1] = tostring(redis.call('HGET', s, 'nobid_budget') or 0)
  out[#out+1] = tostring(redis.call('HGET', s, 'expired') or 0)
  out[#out+1] = tostring(redis.call('HGET', b, 'allocation_clamped') or 0)
end
return out
