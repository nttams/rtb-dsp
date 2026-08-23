-- topup.lua — called by the topup worker per (campaign, cluster) each interval.
-- KEYS: [1] budget hash  [2] c:{cluster}:campaigns set
-- ARGV: [1] campaign_id  [2] allocated_micros (Postgres)  [3] pg_spent_micros (Postgres)
-- Returns: {allocated, reserved, spent, clamped}
--
-- ARBITRATION RULE: Postgres wins at hash-creation time; Redis wins while the hash lives.

local budget, campaigns = KEYS[1], KEYS[2]
local campaign_id = ARGV[1]
local allocated_in = tonumber(ARGV[2]) or 0
local pg_spent = tonumber(ARGV[3]) or 0
if pg_spent < 0 then pg_spent = 0 end

local clamped = 0

if redis.call('EXISTS', budget) == 0 then
  -- FINDING B1: seed `spent` FROM POSTGRES, not 0.
  -- The original hard-set spent=0 and never read Postgres' spend. After a Redis restart --
  -- or the FLUSHDB that the AC-20 verification itself performs -- a campaign that had spent
  -- $9 of $10 was handed a fresh $10. Postgres' spent_micros column had authority over
  -- nothing, making AC-20's flush write-only, and the reconciler's GREATEST(pg, redis) only
  -- ever raised Postgres, never seeded Redis.
  redis.call('HSET', budget, 'allocated', allocated_in, 'reserved', 0, 'spent', pg_spent)
else
  local reserved = hclamp0(budget, 'reserved')
  local spent    = hclamp0(budget, 'spent')
  -- FINDING M8: floor `allocated` at reserved + spent. A legal budget DECREASE via the CRUD
  -- form (AC-21 permits editing allocation) could otherwise leave reserved + spent > allocated,
  -- making assert_no_overspend report an overspend that never happened. Semantics: a decrease
  -- is BEST-EFFORT -- already-reserved impressions are honored, new reserves stop immediately
  -- because available clamps at zero.
  local floor = reserved + spent
  local allocated = allocated_in
  if allocated < floor then
    allocated = floor
    clamped = 1
    redis.call('HSET', budget, 'allocation_clamped', 1)
  end
  redis.call('HSET', budget, 'allocated', allocated)
end

redis.call('SADD', campaigns, campaign_id)

return {
  num(budget, 'allocated'),
  num(budget, 'reserved'),
  num(budget, 'spent'),
  clamped
}
