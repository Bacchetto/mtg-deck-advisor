#!/usr/bin/env sh
# Runs the README's demo in Docker, replaying the recorded session, and checks
# what comes out (#141). CI runs it on every push, so a change that stops the
# recordings replaying (a prompt, a tool, the dataset, the search code) fails
# CI instead of the demo failing silently for the next newcomer.
#
#   scripts/check_demo.sh
#
# It brings up the demo's own Compose project, named in .env.demo, with its own
# volume, and removes it afterwards. Your .env and your development database
# aren't touched, but ports 5432 and 8000 must be free.
set -eu

project="docker compose --env-file .env.demo"
cleanup() { $project down --volumes --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Migrate, seed and start the API: the README's `docker compose up` step.
$project up -d --build --wait

# The README's `mtg-advisor build` step, with its answers: the Demo collection,
# Enter to let the agent choose, approve, change, approve, export to the
# default file, quit.
printf '1\n\na\nc\nAdd more card draw and cut the weakest creatures.\na\ne\n\nq\n' |
  $project exec -T app mtg-advisor build | tee /tmp/demo-build.txt

# A freshly seeded demo database has exactly one pool, so `1` is the Demo
# collection, as the README says.
head -n 1 /tmp/demo-build.txt | grep -q "^1) Demo collection: 2057 cards$"
! grep -q "^2) " /tmp/demo-build.txt

# The exported decklist: "N Name" lines, which must add up to 100 cards.
deck=$($project exec -T app sh -c 'cat /app/*.txt')
cards=$(printf '%s\n' "$deck" | awk '{ total += $1 } END { print total + 0 }')
echo "exported decklist: $cards cards"
test "$cards" -eq 100

# The README's rules question, answered from its recording.
answer=$($project exec -T app mtg-advisor ask \
  "How much extra does it cost to cast my commander for the third time?")
printf '%s\n' "$answer"
printf '%s\n' "$answer" | grep -q "903.8"

echo "the demo replayed end to end"
