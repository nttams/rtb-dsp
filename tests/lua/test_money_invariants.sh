#!/usr/bin/env bash
# Money-invariant regression tests for lua/*.lua, run against a real Redis.
#
# Each case names the consensus-review finding it regresses. These cover the overspend paths
# that the 500-concurrent-reserve test CANNOT reach: that test proves Redis Lua is atomic
# (it cannot race against single-threaded Redis), not that the system cannot overspend.
#
# Usage:  bash tests/lua/test_money_invariants.sh
# Requires Docker. Starts and tears down its own throwaway Redis.
set -u

CONTAINER=rtb-lua-test
LUA_DIR="$(cd "$(dirname "$0")/../.." && pwd)/lua"

cleanup() { docker rm -f "$CONTAINER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

cleanup
docker run -d --name "$CONTAINER" redis:8 redis-server --appendonly yes >/dev/null
for i in $(seq 1 30); do
  docker exec "$CONTAINER" redis-cli PING >/dev/null 2>&1 && break
  sleep 0.5
done

# _common.lua defines now_ms/hclamp0/num/resv_member/parse_member and must be PREPENDED —
# Redis has no `require`. crates/budget and rtbdsp_common do the same at load time.
docker exec "$CONTAINER" mkdir -p /lua
TMP=$(mktemp -d)
for f in reserve settle release reclaim_expired topup reap_deleted dashboard_snapshot; do
  cat "$LUA_DIR/_common.lua" "$LUA_DIR/$f.lua" > "$TMP/$f.lua"
  docker cp "$TMP/$f.lua" "$CONTAINER:/lua/$f.lua" >/dev/null
done
rm -rf "$TMP"

R() { docker exec -i "$CONTAINER" redis-cli "$@"; }
run() { local f=$1; shift; docker exec -i "$CONTAINER" redis-cli --eval /lua/$f "$@"; }

PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
no(){ echo "  FAIL: $1 -- $2"; FAIL=$((FAIL+1)); }

C=us-east
B="c:$C:camp:1:budget"; S="c:$C:camp:1:stats"; Z="c:$C:resv_index"; CS="c:$C:campaigns"

echo "== B1: Redis restart must not re-grant spent budget =="
R FLUSHDB >/dev/null
# topup on a fresh hash, seeding spent from Postgres' value (900000 of 1000000 allocated)
run topup.lua "$B" "$CS" , 1 1000000 900000 >/dev/null
ALLOC=$(R HGET $B allocated); SPENT=$(R HGET $B spent)
[ "$ALLOC" = "1000000" ] && [ "$SPENT" = "900000" ] && ok "topup seeds spent from Postgres ($SPENT/$ALLOC)" || no "B1" "allocated=$ALLOC spent=$SPENT (expected 1000000/900000)"

echo "== M6: zero-amount reserve must be rejected =="
OUT=$(run reserve.lua "$B" "c:$C:resv:z0" "$Z" "$S" , 0 z0 bid0 1 5000)
echo "$OUT" | grep -q BAD_AMOUNT && ok "amount=0 rejected as BAD_AMOUNT" || no "M6" "got: $OUT"

echo "== reserve happy path (100000 of 100000 available) =="
OUT=$(run reserve.lua "$B" "c:$C:resv:r1" "$Z" "$S" , 100000 r1 bid1 1 5000)
echo "$OUT" | grep -q OK && ok "reserve succeeded" || no "reserve" "got: $OUT"
RES=$(R HGET $B reserved); [ "$RES" = "100000" ] && ok "reserved=$RES" || no "reserve" "reserved=$RES"

echo "== M2: replay guard -- same reservation_id must not double-increment =="
run reserve.lua "$B" "c:$C:resv:r1" "$Z" "$S" , 100000 r1 bid1 1 5000 >/dev/null
RES2=$(R HGET $B reserved)
[ "$RES2" = "100000" ] && ok "replay left reserved unchanged ($RES2)" || no "M2" "reserved=$RES2 (expected 100000)"

echo "== INSUFFICIENT when budget exhausted =="
OUT=$(run reserve.lua "$B" "c:$C:resv:r2" "$Z" "$S" , 100000 r2 bid2 1 5000)
echo "$OUT" | grep -q INSUFFICIENT && ok "over-budget reserve refused" || no "insufficient" "got: $OUT"

echo "== M3: reclaim works with the reservation hash DELETED =="
R DEL "c:$C:resv:r1" >/dev/null
R ZADD $Z 1 "r1:1:100000" >/dev/null   # score in the past
OUT=$(run reclaim_expired.lua "$Z" , 500 $C)
RES3=$(R HGET $B reserved)
[ "$RES3" = "0" ] && ok "budget reclaimed from ZSET member alone (reserved=$RES3)" || no "M3" "reserved=$RES3 (expected 0)"

echo "== M5: reserved must never go negative =="
R DEL $B >/dev/null
R ZADD $Z 1 "ghost:1:50000" >/dev/null
run reclaim_expired.lua "$Z" , 500 $C >/dev/null
NEG=$(R HGET $B reserved)
[ -z "$NEG" ] || [ "$NEG" -ge 0 ] 2>/dev/null && ok "reserved floored at 0 (got '${NEG:-unset}')" || no "M5" "reserved=$NEG"

echo "== M8: topup must not lower allocated below reserved+spent =="
R FLUSHDB >/dev/null
run topup.lua "$B" "$CS" , 1 1000000 0 >/dev/null
run reserve.lua "$B" "c:$C:resv:m8" "$Z" "$S" , 100000 m8 bid8 1 5000 >/dev/null
R HSET $B spent 600000 >/dev/null
run topup.lua "$B" "$CS" , 1 500000 600000 >/dev/null
A=$(R HGET $B allocated); CL=$(R HGET $B allocation_clamped)
[ "$A" = "700000" ] && [ "$CL" = "1" ] && ok "allocated clamped to reserved+spent ($A), flag set" || no "M8" "allocated=$A clamped=$CL (expected 700000/1)"

echo "== M4: settle must accept an EXPIRED reservation =="
R FLUSHDB >/dev/null
run topup.lua "$B" "$CS" , 1 1000000 0 >/dev/null
run reserve.lua "$B" "c:$C:resv:m4" "$Z" "$S" , 100000 m4 bid4 1 1 >/dev/null
sleep 1
run reclaim_expired.lua "$Z" , 500 $C >/dev/null
ST=$(R HGET "c:$C:resv:m4" state)
OUT=$(run settle.lua "c:$C:resv:m4" "$B" "$Z" "$S" , m4 90000 1 100000)
SP=$(R HGET $B spent); RS=$(R HGET $B reserved)
[ "$SP" = "90000" ] && ok "late win still recorded spend (state was '$ST', spent=$SP)" || no "M4" "spent=$SP (expected 90000)"
[ "$RS" -ge 0 ] && ok "reserved not negative after late settle ($RS)" || no "M4" "reserved=$RS"

echo "== M1: settle clamp must be observable =="
R FLUSHDB >/dev/null
run topup.lua "$B" "$CS" , 1 10000000 0 >/dev/null
run reserve.lua "$B" "c:$C:resv:m1" "$Z" "$S" , 2500 m1 bid1 1 5000 >/dev/null
OUT=$(run settle.lua "c:$C:resv:m1" "$B" "$Z" "$S" , m1 2500000 1 2500)
CLAMP=$(R HGET $S clamped)
echo "$OUT" | grep -q OK_CLAMPED && [ "$CLAMP" = "1" ] && ok "CPM-instead-of-per-imp clamped AND counted (clamped=$CLAMP)" || no "M1" "out=$OUT clamped=$CLAMP"

echo "== M9: reap_deleted removes orphaned campaigns =="
R FLUSHDB >/dev/null
run topup.lua "$B" "$CS" , 1 1000000 0 >/dev/null
run topup.lua "c:$C:camp:2:budget" "$CS" , 2 1000000 0 >/dev/null
BEFORE=$(R SCARD $CS)
run reap_deleted.lua "$CS" , $C 1 >/dev/null
AFTER=$(R SCARD $CS); EX=$(R EXISTS "c:$C:camp:2:budget")
[ "$BEFORE" = "2" ] && [ "$AFTER" = "1" ] && [ "$EX" = "0" ] && ok "orphan reaped ($BEFORE -> $AFTER, keys gone)" || no "M9" "before=$BEFORE after=$AFTER exists=$EX"

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ]
