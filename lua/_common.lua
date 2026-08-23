-- Shared helpers. Not registered directly; the text is prepended at load time by
-- crates/budget/src/scripts.rs and rtbdsp_common/redis_client.py so both languages
-- run byte-identical logic.

-- Time ALWAYS comes from Redis, never from a caller (finding M7). The engine and the
-- topup worker are different containers with different clocks; a worker running ahead
-- by more than RESERVATION_TTL_MS would reclaim live reservations, and the subsequent
-- legitimate win would be refused, silently under-counting real spend.
-- Caveat: TIME is non-deterministic, so these scripts require effect replication
-- (Redis >= 5 default). The demo runs a single unreplicated Redis.
local function now_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

-- Every decrement clamps at zero (finding M5). Without this, a reseed that clears budget
-- keys while reservations are outstanding lets a later reclaim drive `reserved` negative,
-- which makes available = allocated - reserved - spent LARGER than allocated -- an overspend
-- that `reserved + spent <= allocated` satisfies trivially and the gate cannot see.
local function hclamp0(key, field)
  local v = tonumber(redis.call('HGET', key, field) or 0)
  if v < 0 then redis.call('HSET', key, field, 0); return 0 end
  return v
end

local function num(key, field)
  return tonumber(redis.call('HGET', key, field) or 0)
end

-- resv_index members are self-describing: "{reservation_id}:{campaign_id}:{amount}".
-- This is what lets reclaim work without reading the reservation hash, so sweeper
-- downtime of any duration cannot strand budget (finding M3).
local function resv_member(rid, campaign_id, amount)
  return rid .. ':' .. campaign_id .. ':' .. amount
end

local function parse_member(m)
  local rid, cid, amt = string.match(m, '^(.-):(%d+):(%-?%d+)$')
  if not rid then return nil end
  return rid, cid, tonumber(amt)
end
