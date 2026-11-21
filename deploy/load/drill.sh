#!/usr/bin/env bash
# A failure drill (S07-03): stops one part for a while, then brings it back, and every 5 seconds
# writes down what is waiting and what has arrived, until everything sent during the drill has
# arrived. Run it while deploy/load/run.sh drives traffic in another terminal: k6 says what the
# user saw, this says what happened behind the edge.
#
#   deploy/load/drill.sh kafka 60       the broker away for 60 s
#   deploy/load/drill.sh postgres 30    the database away for 30 s
#   deploy/load/drill.sh routing 60     the routing service away for 60 s (services side only:
#                                       in the modulith, routing is not a process of its own)
#
# Columns, counted from the drill's start:
#   delivered  shipments delivered (tms_shipments)
#   arrivals   arrivals opened at the warehouse (flow 2: in-process in the modulith, via Kafka
#              between the services)
#   analytics  deliveries the analytics loader has stored (always via Kafka)
#   outbox     events published and not yet handed over to Kafka (this side's event_publication)
# Caught up means: arrivals and analytics no more than MARGIN (3) behind the deliveries, and no
# more than MARGIN events in the outbox: what is in flight at any moment of load.
# Prints, and saves where it was started as drill-<part>-<side>-<time>.txt.
set -euo pipefail
NS=ff-logistics
PART=${1:?"which part: kafka, postgres or routing"}
DOWN=${2:-60}
SIDE=$(kubectl -n $NS get rollout logistics-app >/dev/null 2>&1 && echo modulith || echo services)
OUTBOX=$([ "$SIDE" = modulith ] && echo modulith || echo transportation)
OUT=$PWD/drill-$PART-$SIDE-$(date +%Y%m%d-%H%M%S).txt

case $PART in
  kafka)    KIND=statefulset; NAME=kafka;    BACK=1 ;;
  postgres) KIND=deployment;  NAME=postgres; BACK=1 ;;
  routing)
    if [ "$SIDE" = modulith ]; then
      echo "routing is an area inside logistics-app, not a process of its own: nothing to stop alone." | tee "$OUT"
      exit 0
    fi
    KIND=rollout; NAME=routing; BACK=$(kubectl -n $NS get rollout routing -o jsonpath='{.spec.replicas}') ;;
  *) echo "unknown part: $PART (kafka, postgres or routing)"; exit 1 ;;
esac

# Load keeps running, so the counts never stand still: "caught up" allows this many in flight.
MARGIN=${MARGIN:-3}
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
T0=$(date +%s)
sql() { kubectl -n $NS exec deploy/postgres -- psql -U fflogistics -d "$1" -tAc "$2" 2>/dev/null; }
sample() {
  local delivered arrivals analytics outbox
  if delivered=$(sql ff_logistics "select count(*) from tms_shipments where delivered_at >= '$START'"); then
    arrivals=$(sql ff_logistics "select count(*) from wms_inbound_arrivals where created_at >= '$START'")
    outbox=$(sql ff_logistics "select count(*) from $OUTBOX.event_publication where completion_date is null")
    analytics=$(sql ff_logistics_analytics "select count(*) from shipment_deliveries where delivered_at >= '$START'")
    printf "%6s  %-9s %9s %9s %9s %7s\n" "+$(( $(date +%s) - T0 ))s" "$1" "$delivered" "$arrivals" "$analytics" "$outbox"
    # Caught up: no more than what is in flight at one moment of load (a few requests), behind.
    [ $(( delivered - arrivals )) -le $MARGIN ] && [ $(( delivered - analytics )) -le $MARGIN ] && [ "$outbox" -le $MARGIN ]
  else
    printf "%6s  %-9s %9s\n" "+$(( $(date +%s) - T0 ))s" "$1" "(the database does not answer)"
    return 1
  fi
}

{
  echo "== drill: $PART away for ${DOWN}s, $SIDE side, from $START"
  printf "%6s  %-9s %9s %9s %9s %7s\n" time "$PART" delivered arrivals analytics outbox
  sample up || true
  kubectl -n $NS scale $KIND $NAME --replicas=0 >/dev/null
  STOPPED=$(date +%s)
  while [ $(( $(date +%s) - STOPPED )) -lt "$DOWN" ]; do sleep 5; sample away || true; done
  kubectl -n $NS scale $KIND $NAME --replicas="$BACK" >/dev/null
  BACKAT=$(date +%s)
  echo "-- $PART back at +$(( BACKAT - T0 ))s"
  # Until everything delivered during the drill has arrived and the outbox is empty, at most 10 min.
  until sample back; do
    sleep 5
    if [ $(( $(date +%s) - BACKAT )) -gt 600 ]; then echo "not caught up within 10 minutes"; exit 1; fi
  done
  echo "-- caught up $(( $(date +%s) - BACKAT ))s after $PART came back"
} | tee "$OUT"
echo "saved: $OUT"
