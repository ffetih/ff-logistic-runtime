#!/usr/bin/env bash
# Runs the walkthrough scenario (walkthrough.js) against the edge, as a k6 Job in the cluster, and
# samples the pods' CPU and memory while it runs (S07-01, S07-02). Run it once per side: the edge
# answers with whichever side is active, so the script and the numbers are the same for both.
#
#   deploy/load/run.sh             10 virtual users for 3 minutes
#   deploy/load/run.sh 20 5m       20 for 5 minutes
#
# Prints, and saves next to where it was started as load-<side>-<time>.txt:
#   the pods at rest, k6's figures per request, and each pod's highest CPU and memory under load.
# Needs metrics-server (k3s runs it) for kubectl top; its figures are refreshed about every 15 s.
set -euo pipefail
cd "$(dirname "$0")"
NS=ff-logistics
VUS=${1:-10}
DURATION=${2:-3m}
SIDE=$(kubectl -n $NS get rollout logistics-app >/dev/null 2>&1 && echo modulith || echo services)
STAMP=$(date +%Y%m%d-%H%M%S)
JOB=load-$SIDE-$(date +%H%M%S)
OUT=$OLDPWD/load-$SIDE-$STAMP.txt
SAMPLES=$(mktemp)

# The side's own pods, then the platform they share: the comparison is about the first group.
if [ "$SIDE" = modulith ]; then APP='^logistics-app-'; else APP='^(warehousing|transportation|routing|gateway)-'; fi
SHARED='^(postgres|kafka|identity|analytics-loader|edge)-'

top_pods() { kubectl -n $NS top pods --no-headers 2>/dev/null | grep -E "$1" | grep -v -E -- '-(migrate|smoke-test|database)-' || true; }

# One line per pod: highest CPU (millicores) and memory (MiB) among the samples, then the sums.
summarise() {
  echo "$2"
  printf "%-46s %9s %9s\n" pod "cpu m" "mem Mi"
  awk '{ cpu=$2; mem=$3; sub(/m$/,"",cpu); sub(/Mi$/,"",mem)
         if (!($1 in c) || cpu+0 > c[$1]) c[$1]=cpu+0
         if (!($1 in m) || mem+0 > m[$1]) m[$1]=mem+0 }
       END { for (p in c) printf "%-46s %9d %9d\n", p, c[p], m[p] }' "$1" | sort \
    | awk '{ print; tc+=$2; tm+=$3 } END { printf "%-46s %9d %9d\n", "total", tc, tm }'
}

{
  echo "== load test: $SIDE side, $VUS virtual users for $DURATION, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  top_pods "$APP" > "$SAMPLES.rest"; summarise "$SAMPLES.rest" "-- the $SIDE side at rest"
  echo
} | tee "$OUT"

kubectl -n $NS create configmap load-walkthrough --from-file=walkthrough.js \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n $NS apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB
  labels:
    app.kubernetes.io/name: load-walkthrough
    app.kubernetes.io/part-of: ff-logistics
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        app.kubernetes.io/name: load-walkthrough
    spec:
      restartPolicy: Never
      containers:
        - name: k6
          image: grafana/k6:1.0.0
          args: [run, --quiet, /scripts/walkthrough.js]
          env:
            - {name: VUS, value: "$VUS"}
            - {name: DURATION, value: "$DURATION"}
            - {name: SIDE, value: "$SIDE"}
            - name: LOAD_USERNAME
              valueFrom: {secretKeyRef: {name: smoke-test-user, key: username}}
            - name: LOAD_PASSWORD
              valueFrom: {secretKeyRef: {name: smoke-test-user, key: password}}
          resources:
            requests: {cpu: 200m, memory: 128Mi}
            limits: {memory: 512Mi}
          volumeMounts:
            - {name: scripts, mountPath: /scripts}
      volumes:
        - name: scripts
          configMap: {name: load-walkthrough}
YAML
echo "job/$JOB started; sampling the pods every 15 s until it ends ..."

kubectl -n $NS wait --for=condition=ready pod -l job-name=$JOB --timeout=120s >/dev/null 2>&1 || true
while kubectl -n $NS get job $JOB -o jsonpath='{.status.active}' 2>/dev/null | grep -q 1; do
  top_pods "$APP" >> "$SAMPLES"; top_pods "$SHARED" >> "$SAMPLES.shared"; sleep 15
done

{
  echo "-- k6"
  kubectl -n $NS logs job/$JOB | grep -v -E '^\s*$|level=' || true
  echo
  summarise "$SAMPLES" "-- the $SIDE side under load (highest sample per pod)"
  echo
  summarise "$SAMPLES.shared" "-- the shared platform under load (highest sample per pod)"
} | tee -a "$OUT"
rm -f "$SAMPLES" "$SAMPLES.rest" "$SAMPLES.shared"
echo
echo "saved: $OUT"
