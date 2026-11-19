{{/* The pod: shared by the Deployment and the Rollout, so switching between them changes nothing in the pod. */}}
{{- define "ff-service.podTemplate" -}}
metadata:
  labels:
    {{- include "ff-service.labels" . | nindent 4 }}
spec:
  # Otherwise Kubernetes adds <SERVICE>_PORT=tcp://<ip>:<port> for every Service in the
  # namespace, and Spring reads e.g. LOGISTICS_APP_PORT as a property it does not expect.
  # Services find each other by DNS name instead.
  enableServiceLinks: false
  {{- with .Values.imagePullSecrets }}
  imagePullSecrets:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  securityContext:
    {{- toYaml .Values.podSecurityContext | nindent 4 }}
  terminationGracePeriodSeconds: {{ .Values.shutdown.terminationGracePeriodSeconds }}
  containers:
    - name: {{ include "ff-service.name" . }}
      image: "{{ required "image.repository is required" .Values.image.repository }}:{{ required "image.tag is required" .Values.image.tag }}"
      imagePullPolicy: {{ .Values.image.pullPolicy }}
      ports:
        - name: http
          containerPort: {{ .Values.containerPort }}
      {{- if or .Values.env .Values.migration.enabled }}
      env:
        {{- with .Values.env }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
        {{- if .Values.migration.enabled }}
        # Flyway runs in the PreSync hook (job-migrate.yaml); the pods only use the schema.
        - name: SPRING_FLYWAY_ENABLED
          value: "false"
        {{- end }}
      {{- end }}
      startupProbe:
        httpGet:
          path: {{ .Values.probes.liveness }}
          port: http
        periodSeconds: {{ .Values.probes.startup.periodSeconds }}
        failureThreshold: {{ .Values.probes.startup.failureThreshold }}
      readinessProbe:
        httpGet:
          path: {{ .Values.probes.readiness }}
          port: http
        periodSeconds: {{ .Values.probes.readinessPeriodSeconds }}
      livenessProbe:
        httpGet:
          path: {{ .Values.probes.liveness }}
          port: http
        periodSeconds: {{ .Values.probes.livenessPeriodSeconds }}
      # Keep answering while the routing learns that this pod is going (values: shutdown).
      lifecycle:
        preStop:
          sleep:
            seconds: {{ .Values.shutdown.preStopSleepSeconds }}
      resources:
        {{- toYaml .Values.resources | nindent 8 }}
      securityContext:
        {{- toYaml .Values.securityContext | nindent 8 }}
      volumeMounts:
        - name: tmp
          mountPath: /tmp
  volumes:
    - name: tmp
      emptyDir: {}
{{- end -}}
