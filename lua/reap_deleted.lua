-- reap_deleted.lua — called by the topup worker after each topup pass. (finding M9)
-- KEYS: [1] c:{cluster}:campaigns set
-- ARGV: [1] cluster  [2..n] campaign ids Postgres currently reports for this cluster
-- Returns: reaped count
--
-- topup.lua only ever SADDs. Nothing removed a campaign, yet AC-21 requires delete and the
-- schema cascades budget_allocations. A deleted campaign's budget hash, stats hash, and set
-- membership persisted forever; dashboard_snapshot kept returning it and stats.py found no
-- Postgres row to join. This sweep also closes the seeder race for free: the seeder commits
-- Postgres then DELs Redis, so a topup cycle whose SELECT ran pre-commit and whose writes
-- land post-DEL can resurrect a deleted campaign; the next reap removes it.

local campaigns = KEYS[1]
local cluster = ARGV[1]

local live = {}
for i = 2, #ARGV do live[ARGV[i]] = true end

local members = redis.call('SMEMBERS', campaigns)
local reaped = 0
for i = 1, #members do
  local id = members[i]
  if not live[id] then
    redis.call('DEL', 'c:' .. cluster .. ':camp:' .. id .. ':budget')
    redis.call('DEL', 'c:' .. cluster .. ':camp:' .. id .. ':stats')
    redis.call('SREM', campaigns, id)
    reaped = reaped + 1
  end
end
return reaped
